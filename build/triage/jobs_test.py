import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from triage import jobs
from triage.incident import Incident

TEMPLATE = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {"namespace": "triage", "labels": {"app": "triage-agent"}},
    "spec": {"template": {"spec": {
        "serviceAccountName": "triage-agent",
        "restartPolicy": "Never",
        "initContainers": [{"name": "investigate", "env": [{"name": "HOME", "value": "/work/home"}]}],
        "containers": [{"name": "publish"}],
    }}},
}
INC = Incident("alertmanager", "KubePodCrashLooping", "mediastack", "Deployment/radarr", am_fingerprints=["f"])


def test_build_job_is_suspended_labelled_and_carries_the_incident():
    job = jobs.build_job(TEMPLATE, INC, existing_issue=None, deadline_seconds=2400, now=1791700000)
    assert job["metadata"]["name"] == f"triage-{INC.fp12}-1791700000"
    assert job["metadata"]["labels"][jobs.FP_LABEL] == INC.fp12
    assert job["metadata"]["labels"][jobs.KIND_LABEL] == "incident"
    assert Incident.from_json(job["metadata"]["annotations"][jobs.INCIDENT_ANNOTATION]) == INC
    assert job["spec"]["suspend"] is True
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] == 2400
    pod = job["spec"]["template"]["spec"]
    for c in pod["initContainers"] + pod["containers"]:
        env = {e["name"]: e["value"] for e in c["env"]}
        assert Incident.from_json(env["TRIAGE_INCIDENT"]) == INC
        assert env["TRIAGE_EXISTING_ISSUE"] == ""
    assert {e["name"] for e in pod["initContainers"][0]["env"]} >= {"HOME"}


def test_build_job_passes_existing_issue_and_does_not_mutate_template():
    before = copy.deepcopy(TEMPLATE)
    job = jobs.build_job(TEMPLATE, INC, existing_issue=42, deadline_seconds=2400, now=1)
    env = {e["name"]: e["value"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["TRIAGE_EXISTING_ISSUE"] == "42"
    assert TEMPLATE == before


def test_sweep_jobs_are_labelled_sweep():
    sweep = Incident("sweep", "nightly-sweep", "", "")
    assert jobs.build_job(TEMPLATE, sweep, None, 2400, 1)["metadata"]["labels"][jobs.KIND_LABEL] == "sweep"


@pytest.mark.parametrize("mutate,message", [
    (lambda t: t["spec"]["template"]["spec"].update(initContainers=[]), "initContainers"),
    (lambda t: t["spec"]["template"]["spec"]["containers"].append({"name": "x"}), "containers"),
    (lambda t: t["spec"]["template"]["spec"].update(restartPolicy="OnFailure"), "restartPolicy"),
    (lambda t: t["spec"]["template"]["spec"].update(serviceAccountName="default"), "serviceAccountName"),
])
def test_check_template_rejects_drift(mutate, message):
    t = copy.deepcopy(TEMPLATE)
    mutate(t)
    with pytest.raises(ValueError, match=message):
        jobs.check_template(t)


def _job(conditions=(), suspend=True, completion=None):
    return NS(metadata=NS(name="j", labels={jobs.FP_LABEL: "abc"}, annotations={}),
              spec=NS(suspend=suspend),
              status=NS(conditions=[NS(type=t, status="True", last_transition_time=ts) for t, ts in conditions],
                        completion_time=completion))


def test_finished_and_finished_at():
    t = datetime(2026, 10, 11, tzinfo=timezone.utc)
    assert not jobs.finished(_job())
    assert jobs.finished(_job([("Complete", t)], completion=t))
    assert jobs.finished_at(_job([("Complete", t)], completion=t)) == t
    assert jobs.finished_at(_job([("Failed", t)])) == t
    assert jobs.fp_of(_job()) == "abc"
    assert jobs.is_suspended(_job()) is True


def _pod(name, message):
    term = NS(message=message) if message is not None else None
    return NS(status=NS(container_statuses=[NS(name=name, state=NS(terminated=term))]))


def test_read_outcome_parses_publish_termination_message():
    core = NS(list_namespaced_pod=lambda ns, label_selector: NS(items=[_pod("publish", json.dumps({"outcome": "issue_created", "cost_usd": 0.4}))]))
    assert jobs.read_outcome(core, "triage", "j") == {"outcome": "issue_created", "cost_usd": 0.4}


def test_read_outcome_none_when_publish_never_ran_and_garbage_is_flagged():
    none_core = NS(list_namespaced_pod=lambda ns, label_selector: NS(items=[_pod("investigate", None)]))
    assert jobs.read_outcome(none_core, "triage", "j") is None
    bad_core = NS(list_namespaced_pod=lambda ns, label_selector: NS(items=[_pod("publish", "not json")]))
    assert jobs.read_outcome(bad_core, "triage", "j")["outcome"] == "publish_failed"
