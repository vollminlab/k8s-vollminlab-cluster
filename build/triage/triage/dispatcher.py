"""The dispatcher's decisions: what to queue, what to drop, and when to run it."""
import dataclasses
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone

from prometheus_client import Counter, Gauge

from . import dedup, jobs
from .incident import SELF_NAMESPACE, Incident, from_alertmanager, sweep_incident
from .issuebody import incident_title
from .owners import RESOLVABLE, name_candidates, resolve

log = logging.getLogger("triage.dispatcher")

EVENTS = Counter("triage_events_total", "Events received, by source and what the dispatcher did",
                 ["source", "outcome"])
JOBS_24H = Gauge("triage_jobs_24h", "Triage Jobs finished in the last 24h, by outcome", ["outcome"])
SPEND_24H = Gauge("triage_spend_usd_24h", "Claude spend of triage Jobs finished in the last 24h")
QUEUE = Gauge("triage_queue_depth", "Suspended triage Jobs waiting to run")
LAST_SWEEP = Gauge("triage_last_sweep_success_timestamp",
                   "Completion time of the last clean sweep (dispatcher start time until one is seen)")

OUTCOMES = ("issue_created", "commented", "sweep_ok", "agent_error", "publish_failed", "job_crashed",
            "outcome_lost")
DAY = 86400
# How often open issues are checked against what Alertmanager still holds, and how long
# an issue must sit untouched before that check may close it (covers a webhook in flight).
RECONCILE_SECONDS = 900
RECONCILE_GRACE_SECONDS = 1800


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _ago(then: datetime, now: float) -> str:
    minutes = int((now - then.timestamp()) // 60)
    return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"


def _failed(job) -> bool:
    return any(c.type == "Failed" and c.status == "True" for c in (job.status.conditions or []))


class Dispatcher:
    def __init__(self, policy, queue, template, github, get_owner, active_alerts, notify, read_outcome,
                 clock=time.time, held_alerts=None):
        self.policy, self.queue, self.template, self.github = policy, queue, template, github
        self.get_owner, self.active_alerts, self.notify, self.read_outcome = get_owner, active_alerts, notify, read_outcome
        # Every alert Alertmanager holds, silenced and inhibited included. None disables
        # the reconciler.
        self.held_alerts = held_alerts
        self.clock = clock
        self._last_reconcile = 0.0
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
                merged.alerts = sorted({a for i in firing for a in i.alerts})
                results.append(self._enqueue(merged))
            else:
                results.append(self._resolved(incs[0]))
        return results

    def handle_sweep(self) -> str:
        return self._enqueue(sweep_incident(_utc(self.clock()).isoformat()))

    def _resolve_workload(self, inc: Incident, strict: bool = False) -> Incident:
        """Key the incident on the pod's top-level owner. A failed lookup keeps the
        unresolved workload, unless `strict`, when it raises instead."""
        if "/" not in inc.workload:
            return inc
        kind, name = inc.workload.split("/", 1)
        try:
            kind, name = resolve(kind, inc.namespace, name, self.get_owner)
        except Exception as e:
            if strict:
                raise
            # Owner lookup must never drop an alert; worst case the incident is keyed
            # on the pod instead of its Deployment.
            log.warning("owner lookup failed for %s %s/%s (%s); fingerprinting unresolved",
                        kind, inc.namespace, name, e)
        inc.workload = f"{kind}/{name}"
        return inc

    def _enqueue(self, inc: Incident) -> str:
        active = [j for j in self.queue.list() if not jobs.finished(j)]
        job_exists = any(jobs.fp_of(j) == inc.fp12 for j in active)
        issue = raw = None
        if not job_exists and inc.source != "sweep":
            try:
                raw = self.github.find_open_issue(self.policy.reports_repo, inc.fp12)
            except Exception as e:
                log.warning("issue lookup for fp/%s failed (%s); dispatching as new", inc.fp12, e)
                raw = None
            reopened = False
            if raw is None:
                raw = self._reopen_recurrence(inc)
                reopened = raw is not None
            if raw:
                issue = dedup.OpenIssue(raw["number"], _parse(raw["updated_at"]))
        else:
            reopened = False
        action = dedup.decide(job_exists, issue, _utc(self.clock()),
                              timedelta(hours=self.policy.reinvestigate_after_hours))
        if raw and action in (dedup.Action.RECENT, dedup.Action.COMMENT):
            self._note_late_alerts(raw, inc)
        outcome = action.value
        if reopened:
            outcome = {dedup.Action.RECENT: "reopened_recent", dedup.Action.COMMENT: "reopened_comment"}[action]
        if action in (dedup.Action.INFLIGHT, dedup.Action.RECENT):
            EVENTS.labels(inc.source, outcome).inc()
            return outcome
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
        EVENTS.labels(inc.source, outcome).inc()
        return outcome

    def _reopen_recurrence(self, inc: Incident):
        """Reopen the incident's issue if it was closed within the recurrence window, and
        return it; else None. A flapping alert then keeps one issue and the usual
        reinvestigate rule, instead of a new issue and a paid run on every recurrence
        (the 2026-10-21..23 velero alert filed six). Its age for that rule is when it was
        closed. Any failure returns None: filing a new issue beats dropping the alert."""
        repo = self.policy.reports_repo
        try:
            raw = self.github.find_recent_closed_issue(repo, inc.fp12)
        except Exception as e:
            log.warning("closed-issue lookup for fp/%s failed (%s); filing as new", inc.fp12, e)
            return None
        if not raw or not raw.get("closed_at"):
            return None
        closed = _parse(raw["closed_at"])
        if _utc(self.clock()) - closed > timedelta(hours=self.policy.recur_window_hours):
            return None
        when = _utc(self.clock()).strftime("%Y-%m-%d %H:%M UTC")
        try:
            self.github.reopen_issue(repo, raw["number"])
            self.github.comment(repo, raw["number"],
                                f"Recurred at {when}: " + ", ".join(f"`{a}`" for a in (inc.alerts or [inc.reason]))
                                + f" firing again, {_ago(closed, self.clock())} after this was closed. Reopened "
                                "instead of filing a new issue.")
        except Exception as e:
            log.warning("could not reopen issue #%s for fp/%s (%s: %s); filing as new",
                        raw["number"], inc.fp12, type(e).__name__, e)
            return None
        log.info("reopened issue #%s: fp/%s recurred", raw["number"], inc.fp12)
        return dict(raw, updated_at=raw["closed_at"])

    def _note_late_alerts(self, raw: dict, inc: Incident) -> None:
        """Record alerts that joined a workload's incident after its issue was filed.

        The title is the record: it names every alert merged so far, so an alert that
        Alertmanager re-sends is noted once. The title is set before the comment, so a
        failure in between loses the comment rather than repeating it on every re-send.
        A truncated title cannot be parsed and is left alone. Alerts arriving while the
        Job is still queued land here on Alertmanager's next repeat: a suspended Job's
        pod environment, which carries the incident, cannot be changed.
        """
        title = raw.get("title", "")
        if not title.startswith("[") or "] " not in title:
            return
        known = title[1:title.index("] ")].split(", ")
        new = [a for a in (inc.alerts or [inc.reason]) if a not in known]
        if not new:
            return
        repo, number = self.policy.reports_repo, raw["number"]
        when = _utc(self.clock()).strftime("%Y-%m-%d %H:%M UTC")
        try:
            self.github.set_title(repo, number, incident_title(dataclasses.replace(inc, alerts=known + new)))
            self.github.comment(repo, number,
                                f"Also firing on this workload as of {when}: "
                                + ", ".join(f"`{a}`" for a in new)
                                + ". Covered by this incident, not investigated separately.")
        except Exception as e:
            log.warning("could not note late alerts %s on issue #%s (%s: %s)",
                        new, number, type(e).__name__, e)
            return
        EVENTS.labels(inc.source, "late_alert_noted").inc()
        log.info("late_alert_noted: %s added to issue #%s (fp/%s)", ", ".join(new), number, inc.fp12)

    def _match_fps(self, inc: Incident) -> list:
        """The incident's fingerprint, then, only when its owner could not be resolved
        (the workload is still a Pod, Job or ReplicaSet), the fingerprint of every
        controller its name fits. See owners.name_candidates."""
        fps = [inc.fp12]
        kind, _, name = inc.workload.partition("/")
        if kind in RESOLVABLE:
            fps += [dataclasses.replace(inc, workload=f"{k}/{base}").fp12
                    for k, base in name_candidates(kind, name)]
        return fps

    def _still_firing(self, inc: Incident):
        """Whether any alert Alertmanager still holds maps to this incident: True, False,
        or None when Alertmanager cannot be asked.

        One incident now covers every alert on its workload, so one alert resolving (or
        leaving Alertmanager unannounced, e.g. silenced) does not end it while another
        is still firing. The check is by incident, not by Alertmanager fingerprint, for
        the same reason.
        """
        try:
            active = self.active_alerts()
        except Exception as e:
            log.warning("alertmanager re-check for fp/%s failed (%s: %s)", inc.fp12, type(e).__name__, e)
            return None
        unknown = False
        fps = set(self._match_fps(inc))
        for other in from_alertmanager({"alerts": active}):
            # Namespace first: it is free, and owner lookup costs an API call per alert.
            if other.namespace != inc.namespace:
                continue
            try:
                if fps.intersection(self._match_fps(self._resolve_workload(other, strict=True))):
                    return True
            except Exception as e:
                # An unresolved pod never matches its Deployment's fingerprint, so a
                # failed lookup must not count as "this alert is about something else".
                log.warning("owner lookup for %s failed during re-check of fp/%s (%s: %s)",
                            other.workload, inc.fp12, type(e).__name__, e)
                unknown = True
        return None if unknown else False

    def _resolved(self, inc: Incident) -> str:
        if self._still_firing(inc):
            EVENTS.labels("alertmanager", "resolved_partial").inc()
            log.info("resolved_partial: %s resolved on %s %s, other alerts still firing (fp/%s)",
                     inc.reason, inc.namespace or "-", inc.workload or "-", inc.fp12)
            return "resolved_partial"
        # Alertmanager unreachable (None) still closes: no second resolve webhook would
        # arrive to do it later, and a later firing alert simply files a new incident.
        fps = self._match_fps(inc)
        dropped = 0
        for j in self.queue.list():
            if jobs.fp_of(j) in fps and jobs.is_suspended(j) and not jobs.finished(j):
                self.queue.delete(j.metadata.name)
                dropped += 1
        raw = None
        for fp in fps:
            raw = self.github.find_open_issue(self.policy.reports_repo, fp)
            if raw:
                if fp != inc.fp12:
                    log.info("resolve of %s %s matched issue #%s by name (fp/%s): its owner is gone",
                             inc.namespace or "-", inc.workload, raw["number"], fp)
                break
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

    # ---- reconcile ---------------------------------------------------------------

    def reconcile(self) -> int:
        """Close open incident issues that nothing in Alertmanager maps to any more, and
        return how many were closed.

        A resolve webhook is the normal close path, and two cases never send one: an
        alert that resolves while silenced, and an app deleted along with its pods.
        Silenced and inhibited alerts count as still firing. The round is skipped if
        Alertmanager cannot be asked or any alert's owner cannot be looked up: an alert
        we cannot place might be the one keeping an issue open.
        """
        if self.held_alerts is None:
            return 0
        repo, now = self.policy.reports_repo, self.clock()
        candidates = []
        for issue in self.github.list_open_issues(repo):
            names = [lb["name"] for lb in issue.get("labels", [])]
            fps = [n[3:] for n in names if n.startswith("fp/")]
            if not fps or "sweep" in names or any(n.startswith("check/") for n in names):
                continue
            if now - _parse(issue["updated_at"]).timestamp() < RECONCILE_GRACE_SECONDS:
                continue
            candidates.append((issue["number"], fps[0]))
        if not candidates:
            return 0
        busy = {jobs.fp_of(j) for j in self.queue.list() if not jobs.finished(j)}
        try:
            held = self.held_alerts()
        except Exception as e:
            log.warning("reconcile skipped: alertmanager unreachable (%s: %s)", type(e).__name__, e)
            return 0
        live = set()
        for inc in from_alertmanager({"alerts": held}):
            try:
                live.update(self._match_fps(self._resolve_workload(inc, strict=True)))
            except Exception as e:
                log.warning("reconcile skipping this round: owner lookup for %s %s failed (%s: %s)",
                            inc.namespace or "-", inc.workload, type(e).__name__, e)
                return 0
        closed = 0
        when = _utc(now).strftime("%Y-%m-%d %H:%M UTC")
        for number, fp in candidates:
            if fp in live or fp in busy:
                continue
            self.github.comment(repo, number,
                                f"Closed at {when}: Alertmanager no longer holds any alert for this incident, "
                                "silenced ones included. It resolved without a resolve notification (for "
                                "example while silenced, or its workload was deleted). If it recurs within "
                                f"{self.policy.recur_window_hours}h this issue is reopened.")
            self.github.close_issue(repo, number)
            EVENTS.labels("reconcile", "closed_stale").inc()
            log.info("reconcile closed issue #%s (fp/%s): no alert held", number, fp)
            closed += 1
        return closed

    def _maybe_reconcile(self, now: float) -> None:
        if now - self._last_reconcile < RECONCILE_SECONDS:
            return
        self._last_reconcile = now
        try:
            self.reconcile()
        except Exception as e:
            # Housekeeping: never let it stop Jobs from being scheduled.
            log.warning("reconcile failed (%s: %s); retrying in %ss", type(e).__name__, e, RECONCILE_SECONDS)

    # ---- scheduling --------------------------------------------------------------

    def tick(self) -> str:
        now = self.clock()
        self._maybe_reconcile(now)
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
            # None (Alertmanager unreachable) runs the Job: a wasted run costs a few
            # dollars, a dropped one loses the incident.
            if inc.source == "alertmanager" and self._still_firing(inc) is False:
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
            outcome = self._outcome_of(j)
            if outcome is None and _failed(j):
                outcome = {"outcome": "job_crashed", "cost_usd": None}
                self._notify_crash_once(j)
            elif outcome is None:
                # Complete means publish exited 0, which it does only after writing its
                # outcome, so the record was lost (pod deleted before it was first read),
                # not the run. Recorded once so it is logged once; cost unknown, so capped.
                outcome = {"outcome": "outcome_lost", "cost_usd": None}
                log.warning("%s completed but its pod is gone and its outcome was never read; "
                            "charging the per-run cap", j.metadata.name)
                self._store_outcome(j.metadata.name, outcome)
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

    def _outcome_of(self, job):
        """The stored copy if there is one, else publish's termination message, which is
        then stored on the Job so it outlives the pod."""
        stored = (job.metadata.annotations or {}).get(jobs.OUTCOME_ANNOTATION)
        if stored is not None:
            try:
                value = json.loads(stored)
            except ValueError:
                value = None
            if isinstance(value, dict):
                return value
            log.warning("%s has an unreadable %s; reading the pod instead",
                        job.metadata.name, jobs.OUTCOME_ANNOTATION)
        outcome = self.read_outcome(job.metadata.name)
        if outcome is not None:
            self._store_outcome(job.metadata.name, outcome)
        return outcome

    def _store_outcome(self, name: str, outcome: dict) -> None:
        # Best effort: a failed write is retried on the next tick, while the pod still exists.
        try:
            self.queue.annotate(name, jobs.OUTCOME_ANNOTATION, json.dumps(outcome, separators=(",", ":")))
        except Exception as e:
            log.warning("could not store the outcome on %s (%s); will retry", name, e)

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
