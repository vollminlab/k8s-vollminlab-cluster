import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from testsupport import JOB_TEMPLATE
from triage import jobs
from triage.incident import Incident

TEMPLATE = JOB_TEMPLATE
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
        assert env["TRIAGE_RUN"] == "1791700000"       # publish's per-run branch suffix
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


def _podspec(t):
    return t["spec"]["template"]["spec"]


def test_valid_template_passes_check():
    jobs.check_template(copy.deepcopy(TEMPLATE))


@pytest.mark.parametrize("mutate,message", [
    (lambda t: t["spec"]["template"]["spec"].update(initContainers=[]), "initContainers"),
    (lambda t: t["spec"]["template"]["spec"]["containers"].append({"name": "x"}), "containers"),
    (lambda t: t["spec"]["template"]["spec"].update(restartPolicy="OnFailure"), "restartPolicy"),
    (lambda t: t["spec"]["template"]["spec"].update(serviceAccountName="default"), "serviceAccountName"),
    (lambda t: t["spec"].pop("ttlSecondsAfterFinished"), "ttlSecondsAfterFinished"),
    (lambda t: t["spec"].update(ttlSecondsAfterFinished=3600), "ttlSecondsAfterFinished"),
    # the Anthropic key in publish
    (lambda t: _podspec(t)["containers"][0]["volumeMounts"].append({"name": "anthropic", "mountPath": "/a"}),
     "triage-anthropic-apikey"),
    # the Anthropic key missing from investigate
    (lambda t: _podspec(t)["initContainers"][0].update(volumeMounts=[]), "triage-anthropic-apikey"),
    # GitHub App / Pushover credentials in investigate
    (lambda t: _podspec(t)["initContainers"][0]["volumeMounts"].append({"name": "github", "mountPath": "/g"}),
     "triage-github-app"),
    (lambda t: _podspec(t)["initContainers"][0]["volumeMounts"].append({"name": "pushover", "mountPath": "/p"}),
     "triage-pushover"),
    # the GitHub App credentials missing from publish
    (lambda t: _podspec(t)["containers"][0].update(volumeMounts=[{"name": "pushover", "mountPath": "/p"}]),
     "triage-github-app"),
    # ... including by a second volume of the same secret, or by env
    (lambda t: (_podspec(t)["volumes"].append({"name": "gh2", "secret": {"secretName": "triage-github-app"}}),
                _podspec(t)["initContainers"][0]["volumeMounts"].append({"name": "gh2", "mountPath": "/g"})),
     "triage-github-app"),
    (lambda t: _podspec(t)["initContainers"][0]["env"].append(
        {"name": "T", "valueFrom": {"secretKeyRef": {"name": "triage-pushover", "key": "token"}}}),
     "triage-pushover"),
    (lambda t: _podspec(t)["containers"][0].update(envFrom=[{"secretRef": {"name": "triage-anthropic-apikey"}}]),
     "triage-anthropic-apikey"),
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


def test_job_queue_annotate_patches_metadata_annotations():
    calls = []
    batch = NS(patch_namespaced_job=lambda name, ns, body: calls.append((name, ns, body)))
    jobs.JobQueue(batch).annotate("j1", jobs.CRASH_NOTIFIED_ANNOTATION, "true")
    assert calls == [("j1", "triage", {"metadata": {"annotations": {
        "triage.vollminlab.com/crash-notified": "true"}}})]


def test_read_outcome_unparseable_message_has_unknown_cost():
    bad_core = NS(list_namespaced_pod=lambda ns, label_selector: NS(items=[_pod("publish", "not json")]))
    assert jobs.read_outcome(bad_core, "triage", "j")["cost_usd"] is None
