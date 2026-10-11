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


def make(github=None, firing=True, outcomes=None, policy=None, clock=None, queue=None):
    notes = []
    d = Dispatcher(
        policy=policy or make_policy(),
        queue=queue or FakeQueue(),
        template=TEMPLATE,
        github=github or FakeGitHub(),
        get_owner=lambda kind, ns, name: OWNERS.get((kind, name)),
        still_firing=lambda fps: firing,
        notify=lambda title, msg, url=None: notes.append(title),
        read_outcome=lambda name: (outcomes or {}).get(name),
        clock=clock or (lambda: NOW),
    )
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
        labels = dict(CRASHLOOP_LABELS, alertname=f"Other{i}")
        assert d.handle_alertmanager(am_payload(am_alert(labels))) == ["dropped_queue_full"]
    assert notes == ["Triage queue full"]


def test_tick_starts_oldest_when_idle_and_waits_when_busy():
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    d.handle_alertmanager(am_payload(am_alert(dict(CRASHLOOP_LABELS, alertname="Second"))))
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


def test_budget_hold_after_daily_spend_and_one_notification():
    d, notes = make(policy=make_policy(daily_budget_usd=1))
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    first = d.queue.jobs[0].metadata.name
    _finish(d, first, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.read_outcome = lambda name: {"outcome": "issue_created", "cost_usd": 1.5}
    d.handle_alertmanager(am_payload(am_alert(dict(CRASHLOOP_LABELS, alertname="Second"))))
    assert d.tick() == "budget_hold"
    assert d.tick() == "budget_hold"
    assert notes == ["Triage daily budget reached"]


def test_crashed_job_is_counted_and_notified_once():
    d, notes = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    _finish(d, d.queue.jobs[0].metadata.name, datetime.fromtimestamp(NOW - 60, timezone.utc))
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
    _finish(d, d.queue.jobs[0].metadata.name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.tick()
    assert d._spend_24h == 3.0 and _spend(d) == 3.0


@pytest.mark.parametrize("outcome", [
    {"outcome": "publish_failed", "cost_usd": None, "detail": "unparseable termination message"},
    {"outcome": "agent_error"},
    {"outcome": "agent_error", "cost_usd": "lots"},
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
    _finish(d, name, datetime.fromtimestamp(NOW - 60, timezone.utc))
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
    _finish(d, d.queue.jobs[0].metadata.name, datetime.fromtimestamp(NOW - 60, timezone.utc))
    d.tick()
    assert notes == []        # at most once: no page we could not record


@pytest.mark.parametrize("annotations", [{}, None, {jobs.INCIDENT_ANNOTATION: "{not json"},
                                         {jobs.INCIDENT_ANNOTATION: "[]"}])
def test_waiting_job_with_a_bad_incident_annotation_is_dropped_not_blocking(annotations):
    from triage.dispatcher import EVENTS
    d, _ = make()
    d.handle_alertmanager(am_payload(am_alert(CRASHLOOP_LABELS)))
    d.handle_alertmanager(am_payload(am_alert(dict(CRASHLOOP_LABELS, alertname="Second"))))
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


def test_forbidden_deployment_read_loses_the_resolve():
    # Why the dispatcher's ClusterRole must grant `get` on deployments (and the other
    # inferred controllers): without it the confirm is a 403, the lookup fails, the
    # resolve is keyed on the Pod, and the open issue is never closed.
    from kubernetes.client.exceptions import ApiException
    def forbidden(name, ns):
        raise ApiException(status=403)
    cluster = FakeCluster(deployment_read=forbidden)
    d, gh, result = _fire_then_resolve_after_replacement(cluster)
    assert result == ["resolved_unmatched"]
    assert gh.calls == []
