"""The dispatcher's decisions: what to queue, what to drop, and when to run it."""
import logging
import math
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
        dropped = 0
        for j in self.queue.list():
            if jobs.fp_of(j) == inc.fp12 and jobs.is_suspended(j) and not jobs.finished(j):
                self.queue.delete(j.metadata.name)
                dropped += 1
        raw = self.github.find_open_issue(self.policy.reports_repo, inc.fp12)
        if raw:
            when = _utc(self.clock()).strftime("%Y-%m-%d %H:%M UTC")
            self.github.comment(self.policy.reports_repo, raw["number"],
                                f"Resolved at {when}: Alertmanager reports `{inc.reason}` resolved. Closing.")
            self.github.close_issue(self.policy.reports_repo, raw["number"])
        elif not dropped:
            # Nothing to close and nothing queued: the alert was never filed (dropped,
            # deduped onto another fingerprint, or still being investigated). Counted
            # so the gap is visible rather than reported as a clean resolve.
            EVENTS.labels("alertmanager", "resolved_unmatched").inc()
            log.info("resolved_unmatched: %s %s %s matched no open issue or queued job (fp/%s)",
                     inc.reason, inc.namespace or "-", inc.workload or "-", inc.fp12)
            return "resolved_unmatched"
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
            try:
                inc = Incident.from_json((job.metadata.annotations or {})[jobs.INCIDENT_ANNOTATION])
            except Exception as e:
                # A Job we cannot read must not block every Job queued behind it.
                log.error("dropping waiting job %s: unreadable incident annotation (%s: %s)",
                          job.metadata.name, type(e).__name__, e)
                self.queue.delete(job.metadata.name)
                EVENTS.labels("unknown", "dropped_invalid").inc()
                continue
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
                outcome = {"outcome": "job_crashed", "cost_usd": None}
                self._notify_crash_once(j)
            name = outcome.get("outcome")
            counts[name if name in counts else "publish_failed"] += 1
            spend += self._cost_of(outcome)
            if name == "sweep_ok" and done.timestamp() > self.last_sweep:
                self.last_sweep = done.timestamp()
        for o, n in counts.items():
            JOBS_24H.labels(o).set(n)
        self._spend_24h = spend
        SPEND_24H.set(spend)
        LAST_SWEEP.set(self.last_sweep)

    def _cost_of(self, outcome: dict) -> float:
        """The outcome's reported cost; an unknown cost counts as the per-run cap,
        because a killed or garbled run very likely spent real money."""
        cost = outcome.get("cost_usd")
        # Non-finite or negative counts as unknown: NaN would make every budget check
        # pass, and a negative cost would refund the day's spend.
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
            return float(cost)
        return float(self.policy.per_run_budget_usd)

    def _notify_crash_once(self, job) -> None:
        """Page once per crashed Job, across dispatcher restarts. The Job is annotated
        BEFORE paging: a failed annotate retries next tick, whereas paging first would
        re-page every tick for as long as the annotate kept failing."""
        name = job.metadata.name
        if (job.metadata.annotations or {}).get(jobs.CRASH_NOTIFIED_ANNOTATION) == "true":
            return
        try:
            self.queue.annotate(name, jobs.CRASH_NOTIFIED_ANNOTATION, "true")
        except Exception as e:
            log.error("could not mark %s crash-notified (%s); will page on a later tick", name, e)
            return
        self._notify("Triage job crashed",
                     f"{name} finished without publishing (killed, OOM or deadline). "
                     f"kubectl describe job -n triage {name}")

    def _notify(self, title: str, message: str) -> None:
        try:
            self.notify(title, message)
        except Exception as e:
            log.error("pushover notification %r failed: %s", title, e)
