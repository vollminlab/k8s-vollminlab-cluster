import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from testsupport import CRASHLOOP_LABELS, FLUX_LABELS, JOB_TEMPLATE, am_alert, am_payload, make_policy
from triage import jobs
from triage.dispatcher import Dispatcher
from triage.incident import Incident

NOW = 1791700000.0
TEMPLATE = JOB_TEMPLATE
OWNERS = {
    ("Pod", "radarr-7d9c8b6f5-x2x9q"): ("ReplicaSet", "radarr-7d9c8b6f5"),
    ("Pod", "radarr-7d9c8b6f5-zzzzz"): ("ReplicaSet", "radarr-7d9c8b6f5"),
    ("ReplicaSet", "radarr-7d9c8b6f5"): ("Deployment", "radarr"),
}


class FakeQueue:
    def __init__(self):
        self.jobs, self.deleted, self.unsuspended, self.annotated = [], [], [], []

    def list(self):
        return list(self.jobs)

    def create(self, d):
        m = d["metadata"]
        self.jobs.append(NS(
            metadata=NS(name=m["name"], labels=m["labels"], annotations=m["annotations"],
                        creation_timestamp=datetime.fromtimestamp(NOW + len(self.jobs), timezone.utc)),
            spec=NS(suspend=d["spec"]["suspend"]),
            status=NS(conditions=[], completion_time=None),
            raw=d))

    def unsuspend(self, name):
        self.unsuspended.append(name)
        next(j for j in self.jobs if j.metadata.name == name).spec.suspend = False

    def annotate(self, name, key, value):
        self.annotated.append((name, key, value))
        job = next(j for j in self.jobs if j.metadata.name == name)
        job.metadata.annotations = dict(job.metadata.annotations or {}, **{key: value})

    def delete(self, name):
        self.deleted.append(name)
        self.jobs = [j for j in self.jobs if j.metadata.name != name]


class FakeGitHub:
    def __init__(self, issue=None, fail=False):
        self.issue, self.fail, self.calls = issue, fail, []

    def find_open_issue(self, repo, fp12):
        if self.fail:
            raise RuntimeError("github down")
        return self.issue

    def comment(self, repo, n, body):
        self.calls.append(("comment", n, body))

    def close_issue(self, repo, n):
        self.calls.append(("close", n))

    def set_title(self, repo, n, title):
        self.calls.append(("title", n, title))


class FakeAlertmanager:
    """Alertmanager's active-alert set, driven by the same payloads the test posts to the
    dispatcher: a firing alert becomes active, a resolved one leaves. `down` makes the
    API unreachable; `muted` keeps the set empty whatever is posted."""
    def __init__(self, muted=False):
        self.active, self.down, self.muted, self.calls = {}, False, muted, 0

    def observe(self, payload):
        for a in payload.get("alerts", []):
            key = a.get("fingerprint") or repr(sorted(a["labels"].items()))
            if a.get("status") == "resolved":
                self.active.pop(key, None)
            elif not self.muted:
                self.active[key] = {"labels": a["labels"], "fingerprint": a.get("fingerprint", ""),
                                    "startsAt": a.get("startsAt", ""), "status": {"state": "active"}}

    def alerts(self):
        self.calls += 1
        if self.down:
            raise ConnectionError("alertmanager down")
        return list(self.active.values())


def other_workload(name, **extra):
    """Crash-loop labels for a different (owner-less) pod, so a different incident."""
    return dict(CRASHLOOP_LABELS, pod=name, **extra)


def make(github=None, firing=True, outcomes=None, policy=None, clock=None, queue=None):
    notes = []
    am = FakeAlertmanager(muted=not firing)
    d = Dispatcher(
        policy=policy or make_policy(),
        queue=queue or FakeQueue(),
        template=TEMPLATE,
        github=github or FakeGitHub(),
        get_owner=lambda kind, ns, name: OWNERS.get((kind, name)),
        active_alerts=am.alerts,
        notify=lambda title, msg, url=None: notes.append(title),
        read_outcome=lambda name: (outcomes or {}).get(name),
        clock=clock or (lambda: NOW),
    )
    post = d.handle_alertmanager

    def handle_alertmanager(payload):
        am.observe(payload)
        return post(payload)

    d.handle_alertmanager = handle_alertmanager
    d.am = am
    return d, notes


def env_of(job):
    c = job.raw["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e["value"] for e in c["env"]}


def test_new_alert_queues_one_suspended_job_on_the_owning_deployment():
    d, _ = make()
    assert d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS))) == ["dispatched_new"]
    [job] = d.queue.jobs
    assert job.spec.suspend is True
    inc = Incident.from_json(env_of(job)["TRIAGE_INCIDENT"])
    assert inc.workload == "Deployment/radarr"


def test_two_pods_of_one_deployment_in_one_webhook_are_one_job():
    d, _ = make()
    other = dict(CRASHLOOP_LABELS, pod="radarr-7d9c8b6f5-zzzzz")
    result = d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, fingerprint="a"),
                                              am_alert(other, fingerprint="b")))
    assert result == ["dispatched_new"]
    inc = Incident.from_json(env_of(d.queue.jobs[0])["TRIAGE_INCIDENT"])
    assert sorted(inc.am_fingerprints) == ["a", "b"]


def test_job_in_flight_dedupes():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    assert d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS))) == ["deduped_inflight"]
    assert len(d.queue.jobs) == 1


def test_recent_open_issue_dedupes_and_stale_one_comments():
    recent = {"number": 9, "updated_at": "2026-10-11T09:00:00Z"}
    d, _ = make(github=FakeGitHub(recent), clock=lambda: datetime(2026, 10, 11, 10, tzinfo=timezone.utc).timestamp())
    assert d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS))) == ["deduped_recent"]
    stale = {"number": 9, "updated_at": "2026-10-09T09:00:00Z"}
    d2, _ = make(github=FakeGitHub(stale), clock=lambda: datetime(2026, 10, 11, 10, tzinfo=timezone.utc).timestamp())
    assert d2.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS))) == ["dispatched_comment"]
    assert env_of(d2.queue.jobs[0])["TRIAGE_EXISTING_ISSUE"] == "9"


def test_github_outage_still_dispatches():
    d, _ = make(github=FakeGitHub(fail=True))
    assert d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS))) == ["dispatched_new"]


def test_own_namespace_is_ignored():
    d, _ = make()
    labels = dict(CRASHLOOP_LABELS, namespace="triage", pod="triage-dispatcher-abc")
    assert d.handle_alertmanager(am_payload(am_alert(labels))) == ["ignored_self"]
    assert d.queue.jobs == []


def test_ksm_alert_lands_in_the_objects_namespace():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(FLUX_LABELS)))
    inc = Incident.from_json(env_of(d.queue.jobs[0])["TRIAGE_INCIDENT"])
    assert (inc.namespace, inc.workload) == ("mediastack", "HelmRelease/radarr")


def test_resolved_deletes_queued_job_and_closes_issue():
    gh = FakeGitHub()
    d, _ = make(github=gh)
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    gh.issue = {"number": 4, "updated_at": "2026-10-11T09:00:00Z"}
    assert d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, status="resolved"))) == ["resolved"]
    assert d.queue.jobs == []
    assert [c[0] for c in gh.calls] == ["comment", "close"]


def test_mixed_firing_and_resolved_for_one_workload_stays_firing():
    d, _ = make()
    other = dict(CRASHLOOP_LABELS, pod="radarr-7d9c8b6f5-zzzzz")
    result = d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, status="resolved", fingerprint="a"),
                                              am_alert(other, status="firing", fingerprint="b")))
    assert result == ["dispatched_new"]


def test_queue_full_drops_and_notifies_once():
    d, notes = make(policy=make_policy(queue_cap=1))
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    for i in range(2):
        labels = other_workload(f"other{i}-pod")
        assert d.handle_alertmanager(am_payload(am_alert(labels))) == ["dropped_queue_full"]
    assert notes == ["Triage queue full"]


def test_tick_starts_oldest_when_idle_and_waits_when_busy():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    d.handle_alertmanager(am_payload(am_alert(other_workload("second-pod"), fingerprint="bbbb2222")))
    first, second = (j.metadata.name for j in d.queue.jobs)
    assert d.tick() == f"started {first}"
    assert d.tick() == "busy"
    assert d.queue.unsuspended == [first]


def test_tick_drops_jobs_whose_alert_resolved_meanwhile():
    d, _ = make(firing=False)
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    assert d.tick() == "idle"
    assert len(d.queue.deleted) == 1


def _finish(d, name, ts):
    j = next(j for j in d.queue.jobs if j.metadata.name == name)
    j.spec.suspend = False
    j.status = NS(conditions=[NS(type="Complete", status="True", last_transition_time=ts)], completion_time=ts)


def _fail(d, name, ts):
    """publish was killed (OOM, deadline): the Job ends Failed with no termination message."""
    j = next(j for j in d.queue.jobs if j.metadata.name == name)
    j.spec.suspend = False
    j.status = NS(conditions=[NS(type="Failed", status="True", last_transition_time=ts)], completion_time=None)


def test_budget_hold_after_daily_spend_and_one_notification():
    d, notes = make(policy=make_policy(daily_budget_usd=1))
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    first = d.queue.jobs[0].metadata.name
    _finish(d, first, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.read_outcome = lambda name: {"outcome": "issue_created", "cost_usd": 1.5}
    d.handle_alertmanager(am_payload(am_alert(other_workload("second-pod"), fingerprint="bbbb2222")))
    assert d.tick() == "budget_hold"
    assert d.tick() == "budget_hold"
    assert notes == ["Triage daily budget reached"]


def test_crashed_job_is_counted_and_notified_once():
    d, notes = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    _fail(d, d.queue.jobs[0].metadata.name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.tick()
    d.tick()
    assert notes == ["Triage job crashed"]


def test_sweep_success_moves_the_sweep_timestamp():
    d, _ = make()
    assert d.handle_sweep() == "dispatched_new"
    job = d.queue.jobs[0]
    assert job.metadata.labels[jobs.KIND_LABEL] == "sweep"
    assert d.handle_sweep() == "deduped_inflight"
    done = datetime.fromtimestamp(NOW + 600, timezone.utc)
    _finish(d, job.metadata.name, done)
    d.read_outcome = lambda name: {"outcome": "sweep_ok", "cost_usd": 0.8}
    d.tick()
    assert d.last_sweep == done.timestamp()


def _spend(d):
    from triage.dispatcher import SPEND_24H
    return SPEND_24H._value.get()


def test_crashed_job_is_charged_the_per_run_cap():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    _fail(d, d.queue.jobs[0].metadata.name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.tick()
    assert d._spend_24h == 3.0 and _spend(d) == 3.0


@pytest.mark.parametrize("outcome", [
    {"outcome": "publish_failed", "cost_usd": None, "detail": "unparseable termination message"},
    {"outcome": "agent_error"},
    {"outcome": "agent_error", "cost_usd": "lots"},
    # json.loads accepts NaN/Infinity: NaN would make the budget check always pass,
    # and a negative cost would refund the day's spend.
    {"outcome": "issue_created", "cost_usd": float("nan")},
    {"outcome": "issue_created", "cost_usd": float("inf")},
    {"outcome": "issue_created", "cost_usd": -5.0},
    {"outcome": "issue_created", "cost_usd": True},
])
def test_outcome_with_unknown_cost_is_charged_the_per_run_cap(outcome):
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    _finish(d, d.queue.jobs[0].metadata.name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.read_outcome = lambda name: outcome
    d.tick()
    assert d._spend_24h == 3.0


def test_outcome_with_known_cost_is_charged_exactly():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    _finish(d, d.queue.jobs[0].metadata.name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.read_outcome = lambda name: {"outcome": "agent_error", "cost_usd": 0.0}
    d.tick()
    assert d._spend_24h == 0.0


def test_resolved_without_a_matching_issue_or_job_is_counted_unmatched(caplog):
    from triage.dispatcher import EVENTS
    before = EVENTS.labels("alertmanager", "resolved_unmatched")._value.get()
    gh = FakeGitHub()
    d, _ = make(github=gh)
    with caplog.at_level("INFO", logger="triage.dispatcher"):
        assert d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, status="resolved"))) == \
            ["resolved_unmatched"]
    assert EVENTS.labels("alertmanager", "resolved_unmatched")._value.get() == before + 1
    assert gh.calls == []
    assert any("unmatched" in r.getMessage() and "fp/" in r.getMessage() for r in caplog.records)


def test_crash_page_is_not_repeated_after_a_dispatcher_restart():
    queue = FakeQueue()
    d, notes = make(queue=queue)
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    name = queue.jobs[0].metadata.name
    _fail(d, name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.tick()
    assert notes == ["Triage job crashed"]
    assert queue.annotated == [(name, jobs.CRASH_NOTIFIED_ANNOTATION, "true")]
    restarted, notes2 = make(queue=queue)       # fresh process, same cluster state
    restarted.tick()
    restarted.tick()
    assert notes2 == []


def test_crash_page_waits_if_the_job_cannot_be_annotated():
    class Unannotatable(FakeQueue):
        def annotate(self, name, key, value):
            raise RuntimeError("apiserver down")
    d, notes = make(queue=Unannotatable())
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    _fail(d, d.queue.jobs[0].metadata.name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.tick()
    assert notes == []        # at most once: no page we could not record


@pytest.mark.parametrize("annotations", [{}, None, {jobs.INCIDENT_ANNOTATION: "{not json"},
                                         {jobs.INCIDENT_ANNOTATION: "[]"}])
def test_waiting_job_with_a_bad_incident_annotation_is_dropped_not_blocking(annotations):
    from triage.dispatcher import EVENTS
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    d.handle_alertmanager(am_payload(am_alert(other_workload("second-pod"), fingerprint="bbbb2222")))
    bad, good = (j.metadata.name for j in d.queue.jobs)
    d.queue.jobs[0].metadata.annotations = annotations
    before = EVENTS.labels("unknown", "dropped_invalid")._value.get()
    assert d.tick() == f"started {good}"
    assert d.queue.deleted == [bad]
    assert EVENTS.labels("unknown", "dropped_invalid")._value.get() == before + 1


# --- the real owner path: fire while the pod lives, resolve after it is replaced ------

class KeyedGitHub(FakeGitHub):
    """Finds an issue only under the fingerprint it was filed with."""
    def __init__(self):
        super().__init__()
        self.issues = {}

    def find_open_issue(self, repo, fp12):
        return self.issues.get(fp12)


class FakeCluster:
    """Fake kube API clients for owners.kube_owner_lookup; objects can be deleted."""
    def __init__(self, deployment_read=None):
        from kubernetes.client.exceptions import ApiException
        self.ApiException = ApiException
        ref = lambda kind, name: NS(kind=kind, name=name, controller=True)
        self.objs = {
            ("Pod", "radarr-7d9c8b6f5-x2x9q"): ref("ReplicaSet", "radarr-7d9c8b6f5"),
            ("ReplicaSet", "radarr-7d9c8b6f5"): ref("Deployment", "radarr"),
            ("Deployment", "radarr"): None,
        }
        self.deployment_read = deployment_read
        self.core = NS(read_namespaced_pod=self._reader("Pod"))
        self.apps = NS(read_namespaced_replica_set=self._reader("ReplicaSet"),
                       read_namespaced_deployment=deployment_read or self._reader("Deployment"),
                       read_namespaced_stateful_set=self._reader("StatefulSet"),
                       read_namespaced_daemon_set=self._reader("DaemonSet"))
        self.batch = NS(read_namespaced_job=self._reader("Job"), read_namespaced_cron_job=self._reader("CronJob"))

    def _reader(self, kind):
        def read(name, ns):
            if (kind, name) not in self.objs:
                raise self.ApiException(status=404)
            ref = self.objs[(kind, name)]
            return NS(metadata=NS(owner_references=[ref] if ref else []))
        return read


def _fire_then_resolve_after_replacement(cluster):
    from triage.owners import kube_owner_lookup
    gh = KeyedGitHub()
    d, _ = make(github=gh)
    d.get_owner = kube_owner_lookup(cluster.core, cluster.apps, cluster.batch)
    assert d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS))) == ["dispatched_new"]
    fired = Incident.from_json(env_of(d.queue.jobs[0])["TRIAGE_INCIDENT"])
    assert fired.workload == "Deployment/radarr"
    gh.issues[fired.fp12] = {"number": 7, "updated_at": "2026-10-11T09:00:00Z"}
    d.queue.jobs.clear()                                   # the Job ran and published
    # The crash-looping pod is replaced and its ReplicaSet rolled away.
    del cluster.objs[("Pod", "radarr-7d9c8b6f5-x2x9q")]
    del cluster.objs[("ReplicaSet", "radarr-7d9c8b6f5")]
    return d, gh, d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, status="resolved")))


def test_resolve_after_pod_and_replicaset_are_gone_closes_the_same_issue():
    d, gh, result = _fire_then_resolve_after_replacement(FakeCluster())
    assert result == ["resolved"]
    assert [c[:2] for c in gh.calls] == [("comment", 7), ("close", 7)]


def test_forbidden_deployment_read_falls_back_to_the_name_on_resolve(caplog):
    # A 403 on the confirm still breaks owner lookup, and that still matters on the FIRING
    # side (a new incident would be keyed on the Pod), which is why the dispatcher's
    # ClusterRole grants `get` on deployments. On resolve, the name fallback reaches the
    # Deployment's existing issue anyway.
    from kubernetes.client.exceptions import ApiException
    def forbidden(name, ns):
        raise ApiException(status=403)
    cluster = FakeCluster(deployment_read=forbidden)
    with caplog.at_level("INFO", logger="triage.dispatcher"):
        d, gh, result = _fire_then_resolve_after_replacement(cluster)
    assert result == ["resolved"]
    assert [c[:2] for c in gh.calls] == [("comment", 7), ("close", 7)]
    assert any("matched issue #7 by name" in r.getMessage() for r in caplog.records)


def test_resolve_after_the_whole_app_is_deleted_closes_its_issue():
    # 2026-10-17: removing the canary pruned Deployment, ReplicaSet and pods together.
    # Nothing was left to confirm the owner, so the resolve was keyed on the Pod and both
    # canary issues stayed open.
    cluster = FakeCluster()
    del cluster.objs[("Deployment", "radarr")]
    d, gh, result = _fire_then_resolve_after_replacement(cluster)
    assert result == ["resolved"]
    assert [c[:2] for c in gh.calls] == [("comment", 7), ("close", 7)]


def test_resolve_after_deletion_drops_the_deployments_queued_job():
    from triage.owners import kube_owner_lookup
    cluster = FakeCluster()
    d, _ = make(github=KeyedGitHub())
    d.get_owner = kube_owner_lookup(cluster.core, cluster.apps, cluster.batch)
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    [job] = d.queue.jobs
    cluster.objs.clear()
    assert d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, status="resolved"))) == ["resolved"]
    assert d.queue.deleted == [job.metadata.name]


def test_name_fallback_is_not_used_when_the_owner_resolves():
    # A live Deployment is keyed exactly; its pods' names are never second-guessed.
    gh = KeyedGitHub()
    d, _ = make(github=gh)
    from triage.dispatcher import Incident as I
    inc = d._resolve_workload(I(source="alertmanager", reason="X", namespace="mediastack",
                                workload="Pod/radarr-7d9c8b6f5-x2x9q"))
    assert d._match_fps(inc) == [inc.fp12]


def test_outcome_is_copied_onto_the_job_the_first_time_it_is_read():
    # The nightly disk-cleanup CronJob deletes every Succeeded pod older than an hour, and
    # the outcome lives in the publish pod's termination message. Copied onto the Job, it
    # outlives the pod.
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    name = d.queue.jobs[0].metadata.name
    _finish(d, name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.read_outcome = lambda n: {"outcome": "issue_created", "cost_usd": 0.6, "issue": 8}
    d.tick()
    assert [(k, json.loads(v)) for n, k, v in d.queue.annotated if n == name] == \
        [(jobs.OUTCOME_ANNOTATION, {"outcome": "issue_created", "cost_usd": 0.6, "issue": 8})]
    d.read_outcome = lambda n: None          # the pod is gone
    d.tick()
    assert d._spend_24h == 0.6
    assert len(d.queue.annotated) == 1        # read from the Job, not written again


def test_completed_job_whose_pod_vanished_is_lost_not_crashed():
    # A Complete Job means publish exited 0, which it only does after writing its outcome.
    # Missing pod + Complete is a lost record, not a crash: no page, charged the cap.
    from triage.dispatcher import JOBS_24H
    d, notes = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    _finish(d, d.queue.jobs[0].metadata.name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.tick()
    d.tick()
    assert notes == []
    assert d._spend_24h == 3.0
    assert JOBS_24H.labels("outcome_lost")._value.get() == 1
    assert JOBS_24H.labels("job_crashed")._value.get() == 0


def test_unreadable_stored_outcome_falls_back_to_the_pod():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    name = d.queue.jobs[0].metadata.name
    _finish(d, name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.queue.jobs[0].metadata.annotations[jobs.OUTCOME_ANNOTATION] = "{not json"
    d.read_outcome = lambda n: {"outcome": "issue_created", "cost_usd": 0.4}
    d.tick()
    assert d._spend_24h == 0.4


def test_outcome_is_still_counted_if_the_job_cannot_be_annotated():
    class Unannotatable(FakeQueue):
        def annotate(self, name, key, value):
            raise RuntimeError("apiserver down")
    d, notes = make(queue=Unannotatable())
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    _finish(d, d.queue.jobs[0].metadata.name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.read_outcome = lambda n: {"outcome": "issue_created", "cost_usd": 0.5}
    d.tick()
    assert d._spend_24h == 0.5 and notes == []


# ---- 0.1.3: one incident per workload -----------------------------------------------

def test_different_alerts_on_one_workload_in_one_webhook_are_one_job_listing_both():
    d, _ = make()
    ready = dict(CRASHLOOP_LABELS, alertname="KubePodNotReady")
    result = d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, fingerprint="a"),
                                              am_alert(ready, fingerprint="b")))
    assert result == ["dispatched_new"]
    inc = Incident.from_json(env_of(d.queue.jobs[0])["TRIAGE_INCIDENT"])
    assert inc.alerts == ["KubePodCrashLooping", "KubePodNotReady"]
    assert sorted(inc.am_fingerprints) == ["a", "b"]


def test_a_second_alert_on_a_queued_workload_dedupes():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, fingerprint="a")))
    ready = dict(CRASHLOOP_LABELS, alertname="KubePodNotReady")
    assert d.handle_alertmanager(am_payload(am_alert(ready, fingerprint="b"))) == ["deduped_inflight"]
    assert len(d.queue.jobs) == 1


def test_resolving_one_alert_keeps_the_incident_while_another_on_the_workload_fires():
    gh = FakeGitHub()
    d, _ = make(github=gh)
    ready = dict(CRASHLOOP_LABELS, alertname="KubePodNotReady")
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, fingerprint="a")))
    d.handle_alertmanager(am_payload(am_alert(ready, fingerprint="b")))
    gh.issue = {"number": 4, "updated_at": "2026-10-11T09:00:00Z"}
    result = d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, status="resolved", fingerprint="a")))
    assert result == ["resolved_partial"]
    assert len(d.queue.jobs) == 1 and gh.calls == []
    # ...and the last one resolving closes it.
    assert d.handle_alertmanager(am_payload(am_alert(ready, status="resolved", fingerprint="b"))) == ["resolved"]
    assert d.queue.jobs == [] and [c[0] for c in gh.calls] == ["comment", "close"]


def test_an_alert_on_another_workload_does_not_hold_an_incident_open():
    gh = FakeGitHub()
    d, _ = make(github=gh)
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, fingerprint="a"),
                                     am_alert(other_workload("sonarr-pod"), fingerprint="b")))
    gh.issue = {"number": 4, "updated_at": "2026-10-11T09:00:00Z"}
    result = d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, status="resolved", fingerprint="a")))
    assert result == ["resolved"]


def test_resolve_still_closes_when_alertmanager_cannot_be_asked():
    # The pre-0.1.3 behaviour: a resolve webhook closes. Leaving it open on an outage
    # would strand the issue, since no second resolve arrives.
    gh = FakeGitHub()
    d, _ = make(github=gh)
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, fingerprint="a")))
    gh.issue = {"number": 4, "updated_at": "2026-10-11T09:00:00Z"}
    d.am.down = True
    assert d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, status="resolved", fingerprint="a"))) \
        == ["resolved"]


def test_tick_runs_a_job_whose_first_alert_resolved_while_another_on_the_workload_fires():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, fingerprint="a")))
    # The first alert leaves Alertmanager without a resolve webhook reaching us (e.g. it
    # was silenced); a second alert on the same workload is still firing.
    d.am.active.pop("a")
    d.am.observe(am_payload(am_alert(dict(CRASHLOOP_LABELS, alertname="KubePodNotReady"), fingerprint="b")))
    assert d.tick().startswith("started ")


def test_tick_starts_the_job_when_alertmanager_cannot_be_asked():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    d.am.down = True
    assert d.tick().startswith("started ")


def test_cluster_level_alerts_with_different_names_stay_separate_jobs():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert({"alertname": "NodeDiskWriteLatencyHigh"}, fingerprint="a")))
    d.handle_alertmanager(am_payload(am_alert({"alertname": "etcdDatabaseHighFragmentationRatio"},
                                              fingerprint="b")))
    assert len(d.queue.jobs) == 2


def test_alertmanager_is_not_asked_for_an_ordinary_new_alert():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    assert d.am.calls == 0


def test_an_owner_lookup_failure_is_unknown_not_resolved():
    # The active alert names a pod; if its owner cannot be looked up, its fingerprint is
    # the pod's, which never matches the Deployment's. Reading that as "nothing firing"
    # would make the tick drop a live incident and a resolve close a live issue.
    gh = FakeGitHub()
    d, _ = make(github=gh)
    ready = dict(CRASHLOOP_LABELS, alertname="KubePodNotReady")
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS, fingerprint="a")))
    d.handle_alertmanager(am_payload(am_alert(ready, fingerprint="b")))

    def broken(kind, ns, name):
        raise RuntimeError("apiserver 503")
    d.get_owner = broken
    assert d._still_firing(Incident.from_json(env_of(d.queue.jobs[0])["TRIAGE_INCIDENT"])) is None
    assert d.tick().startswith("started ")


def _late(title, updated="2026-10-11T09:00:00Z", fail=False):
    issue = {"number": 9, "updated_at": updated, "title": title}
    gh = FakeGitHub(issue)
    if fail:
        def boom(*a):
            raise RuntimeError("github down")
        gh.set_title = boom
    d, _ = make(github=gh, clock=lambda: datetime(2026, 10, 11, 10, tzinfo=timezone.utc).timestamp())
    return d, gh


def test_a_late_alert_is_added_to_the_issue_title_and_noted_once():
    d, gh = _late("[KubePodCrashLooping] mediastack/Deployment/radarr")
    ready = dict(CRASHLOOP_LABELS, alertname="KubePodNotReady")
    assert d.handle_alertmanager(am_payload(am_alert(ready))) == ["deduped_recent"]
    assert gh.calls[0] == ("title", 9, "[KubePodCrashLooping, KubePodNotReady] mediastack/Deployment/radarr")
    assert gh.calls[1][:2] == ("comment", 9) and "`KubePodNotReady`" in gh.calls[1][2]
    gh.issue["title"] = gh.calls[0][2]
    gh.calls.clear()
    assert d.handle_alertmanager(am_payload(am_alert(ready))) == ["deduped_recent"]
    assert gh.calls == []


def test_an_alert_already_in_the_title_is_not_noted():
    d, gh = _late("[KubePodCrashLooping] mediastack/Deployment/radarr")
    assert d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS))) == ["deduped_recent"]
    assert gh.calls == []


def test_a_late_alert_is_also_noted_when_the_issue_is_reinvestigated():
    d, gh = _late("[KubePodCrashLooping] mediastack/Deployment/radarr", updated="2026-10-09T09:00:00Z")
    ready = dict(CRASHLOOP_LABELS, alertname="KubePodNotReady")
    assert d.handle_alertmanager(am_payload(am_alert(ready))) == ["dispatched_comment"]
    assert [c[:2] for c in gh.calls] == [("title", 9), ("comment", 9)]


def test_a_truncated_title_is_left_alone():
    d, gh = _late("[KubePodCrashLooping, KubePodNotReady, KubeDeploymentRepl")
    ready = dict(CRASHLOOP_LABELS, alertname="KubeContainerWaiting")
    assert d.handle_alertmanager(am_payload(am_alert(ready))) == ["deduped_recent"]
    assert gh.calls == []


def test_a_github_failure_noting_a_late_alert_does_not_drop_the_event(caplog):
    d, gh = _late("[KubePodCrashLooping] mediastack/Deployment/radarr", fail=True)
    ready = dict(CRASHLOOP_LABELS, alertname="KubePodNotReady")
    with caplog.at_level("WARNING", logger="triage.dispatcher"):
        assert d.handle_alertmanager(am_payload(am_alert(ready))) == ["deduped_recent"]
    assert gh.calls == []
    assert any("could not note late alerts" in r.getMessage() for r in caplog.records)
