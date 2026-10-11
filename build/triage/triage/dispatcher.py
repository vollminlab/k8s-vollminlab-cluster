"""The dispatcher's decisions: what to queue, what to drop, and when to run it."""
import logging
import time
from datetime import datetime, timedelta, timezone

from prometheus_client import Counter, Gauge

from . import dedup, jobs
from .incident import SELF_NAMESPACE, Incident, from_alertmanager, sweep_incident
from .owners import resolve

log = logging.getLogger("triage.dispatcher")

EVENTS = Counter("triage_events_total", "Events received, by source and what the dispatcher did",
                 ["source", "outcome"])
JOBS_24H = Gauge("triage_jobs_24h", "Triage Jobs finished in the last 24h, by outcome", ["outcome"])
SPEND_24H = Gauge("triage_spend_usd_24h", "Claude spend of triage Jobs finished in the last 24h")
QUEUE = Gauge("triage_queue_depth", "Suspended triage Jobs waiting to run")
LAST_SWEEP = Gauge("triage_last_sweep_success_timestamp",
                   "Completion time of the last clean sweep (dispatcher start time until one is seen)")

OUTCOMES = ("issue_created", "commented", "sweep_ok", "agent_error", "publish_failed", "job_crashed")
DAY = 86400


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


class Dispatcher:
    def __init__(self, policy, queue, template, github, get_owner, still_firing, notify, read_outcome,
                 clock=time.time):
        self.policy, self.queue, self.template, self.github = policy, queue, template, github
        self.get_owner, self.still_firing, self.notify, self.read_outcome = get_owner, still_firing, notify, read_outcome
        self.clock = clock
        self._queue_full_alerted = False
        self._budget_alerted_at = 0.0
        self._crash_alerted = set()
        self._spend_24h = 0.0
        # Seeded with the start time, so TriageSweepStale measures "26h without a sweep"
        # from whichever is later: the last clean sweep, or this process starting. Jobs
        # are kept 25h, so a restart cannot see an older sweep and would otherwise
        # report the timestamp as absent, which no `time() - x > ...` rule can fire on.
        self.last_sweep = clock()
        LAST_SWEEP.set(self.last_sweep)

    # ---- intake ------------------------------------------------------------------

    def handle_alertmanager(self, payload: dict) -> list:
        groups = {}
        for inc in from_alertmanager(payload):
            inc = self._resolve_workload(inc)
            groups.setdefault(inc.fingerprint, []).append(inc)
        results = []
        for incs in groups.values():
            if incs[0].namespace == SELF_NAMESPACE:
                EVENTS.labels("alertmanager", "ignored_self").inc()
                results.append("ignored_self")
                continue
            if len(incs) > 1:
                EVENTS.labels("alertmanager", "deduped_batch").inc(len(incs) - 1)
            firing = [i for i in incs if i.status != "resolved"]
            if firing:
                # One webhook can carry several pods of one workload, some resolved
                # and some not: any firing alert keeps the incident open.
                merged = firing[0]
                merged.am_fingerprints = sorted({f for i in firing for f in i.am_fingerprints})
                results.append(self._enqueue(merged))
            else:
                results.append(self._resolved(incs[0]))
        return results

    def handle_sweep(self) -> str:
        return self._enqueue(sweep_incident(_utc(self.clock()).isoformat()))

    def _resolve_workload(self, inc: Incident) -> Incident:
        if "/" not in inc.workload:
            return inc
        kind, name = inc.workload.split("/", 1)
        try:
            kind, name = resolve(kind, inc.namespace, name, self.get_owner)
        except Exception as e:
            # Owner lookup must never drop an alert; worst case the incident is keyed
            # on the pod instead of its Deployment.
            log.warning("owner lookup failed for %s %s/%s (%s); fingerprinting unresolved",
                        kind, inc.namespace, name, e)
        inc.workload = f"{kind}/{name}"
        return inc

    def _enqueue(self, inc: Incident) -> str:
        active = [j for j in self.queue.list() if not jobs.finished(j)]
        job_exists = any(jobs.fp_of(j) == inc.fp12 for j in active)
        issue = None
        if not job_exists and inc.source != "sweep":
            try:
                raw = self.github.find_open_issue(self.policy.reports_repo, inc.fp12)
            except Exception as e:
                log.warning("issue lookup for fp/%s failed (%s); dispatching as new", inc.fp12, e)
                raw = None
            if raw:
                issue = dedup.OpenIssue(raw["number"],
                                        datetime.fromisoformat(raw["updated_at"].replace("Z", "+00:00")))
        action = dedup.decide(job_exists, issue, _utc(self.clock()),
                              timedelta(hours=self.policy.reinvestigate_after_hours))
        if action in (dedup.Action.INFLIGHT, dedup.Action.RECENT):
            EVENTS.labels(inc.source, action.value).inc()
            return action.value
        waiting = [j for j in active if jobs.is_suspended(j)]
        if len(waiting) >= self.policy.queue_cap:
            EVENTS.labels(inc.source, "dropped_queue_full").inc()
            if not self._queue_full_alerted:
                self._notify("Triage queue full",
                             f"{len(waiting)} incidents are waiting; new ones are dropped until the queue drains.")
                self._queue_full_alerted = True
            return "dropped_queue_full"
        existing = issue.number if action is dedup.Action.COMMENT else None
        self.queue.create(jobs.build_job(self.template, inc, existing,
                                         self.policy.job_deadline_seconds, int(self.clock())))
        EVENTS.labels(inc.source, action.value).inc()
        return action.value

    def _resolved(self, inc: Incident) -> str:
        for j in self.queue.list():
            if jobs.fp_of(j) == inc.fp12 and jobs.is_suspended(j) and not jobs.finished(j):
                self.queue.delete(j.metadata.name)
        raw = self.github.find_open_issue(self.policy.reports_repo, inc.fp12)
        if raw:
            when = _utc(self.clock()).strftime("%Y-%m-%d %H:%M UTC")
            self.github.comment(self.policy.reports_repo, raw["number"],
                                f"Resolved at {when}: Alertmanager reports `{inc.reason}` resolved. Closing.")
            self.github.close_issue(self.policy.reports_repo, raw["number"])
        EVENTS.labels("alertmanager", "resolved").inc()
        return "resolved"

    # ---- scheduling --------------------------------------------------------------

    def tick(self) -> str:
        now = self.clock()
        all_jobs = self.queue.list()
        self._account(all_jobs, now)
        active = [j for j in all_jobs if not jobs.finished(j)]
        waiting = sorted((j for j in active if jobs.is_suspended(j)),
                         key=lambda j: j.metadata.creation_timestamp)
        QUEUE.set(len(waiting))
        if len(waiting) < self.policy.queue_cap:
            self._queue_full_alerted = False
        if any(not jobs.is_suspended(j) for j in active):
            return "busy"
        if not waiting:
            return "idle"
        if self._spend_24h >= self.policy.daily_budget_usd:
            if now - self._budget_alerted_at > DAY:
                self._notify("Triage daily budget reached",
                             f"${self._spend_24h:.2f} spent in 24h (cap ${self.policy.daily_budget_usd:.2f}). "
                             f"{len(waiting)} incidents held until spend ages out.")
                self._budget_alerted_at = now
            return "budget_hold"
        for job in waiting:
            inc = Incident.from_json(job.metadata.annotations[jobs.INCIDENT_ANNOTATION])
            if inc.source == "alertmanager" and not self.still_firing(inc.am_fingerprints):
                self.queue.delete(job.metadata.name)
                EVENTS.labels(inc.source, "dropped_resolved").inc()
                continue
            self.queue.unsuspend(job.metadata.name)
            return f"started {job.metadata.name}"
        return "idle"

    def _account(self, all_jobs, now: float) -> None:
        """Recompute 24h outcomes and spend from finished Jobs. Stateless by design:
        a dispatcher restart cannot reset the daily cap."""
        counts = {o: 0 for o in OUTCOMES}
        spend = 0.0
        for j in all_jobs:
            if not jobs.finished(j):
                continue
            done = jobs.finished_at(j)
            if done is None or now - done.timestamp() > DAY:
                continue
            outcome = self.read_outcome(j.metadata.name)
            if outcome is None:
                outcome = {"outcome": "job_crashed", "cost_usd": 0.0}
                if j.metadata.name not in self._crash_alerted:
                    self._notify("Triage job crashed",
                                 f"{j.metadata.name} finished without publishing (killed, OOM or deadline). "
                                 f"kubectl describe job -n triage {j.metadata.name}")
                    self._crash_alerted.add(j.metadata.name)
            name = outcome.get("outcome")
            counts[name if name in counts else "publish_failed"] += 1
            spend += float(outcome.get("cost_usd") or 0.0)
            if name == "sweep_ok" and done.timestamp() > self.last_sweep:
                self.last_sweep = done.timestamp()
        for o, n in counts.items():
            JOBS_24H.labels(o).set(n)
        self._spend_24h = spend
        SPEND_24H.set(spend)
        LAST_SWEEP.set(self.last_sweep)

    def _notify(self, title: str, message: str) -> None:
        try:
            self.notify(title, message)
        except Exception as e:
            log.error("pushover notification %r failed: %s", title, e)
