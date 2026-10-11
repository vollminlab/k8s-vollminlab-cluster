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


def _inv(t):
    return _podspec(t)["initContainers"][0]


def _pub(t):
    return _podspec(t)["containers"][0]


def _mount(c, name):
    return next(m for m in c["volumeMounts"] if m["name"] == name)


def _set_env(c, name, value):
    c["env"] = [e for e in c["env"] if e["name"] != name] + [{"name": name, "value": value}]


@pytest.mark.parametrize("mutate,message", [
    # the old layout: one emptyDir holding the checkout, HOME and result.json for both
    (lambda t: _pub(t)["volumeMounts"].append({"name": "investigate-work", "mountPath": "/work"}),
     "investigate-work.*shared"),
    # any other emptyDir shared, e.g. a common /tmp
    (lambda t: _pub(t)["volumeMounts"].append({"name": "investigate-tmp", "mountPath": "/scratch"}),
     "investigate-tmp.*shared"),
    # two volumes backed by the same claim are one shared filesystem
    (lambda t: (_podspec(t)["volumes"].extend([
        {"name": "a", "persistentVolumeClaim": {"claimName": "x"}},
        {"name": "b", "persistentVolumeClaim": {"claimName": "x"}}]),
        _inv(t)["volumeMounts"].append({"name": "a", "mountPath": "/a"}),
        _pub(t)["volumeMounts"].append({"name": "b", "mountPath": "/b"})), "persistentVolumeClaim.*shared"),
    # a shared configMap mounted writable in one of them (kubelet makes it read-only
    # anyway, but the template must say so)
    (lambda t: _mount(_pub(t), "policy").pop("readOnly"), "policy"),
    # result writable in publish, or missing from either container
    (lambda t: _mount(_pub(t), "result").pop("readOnly"), "result.*readOnly"),
    (lambda t: _mount(_pub(t), "result").update(readOnly=False), "result.*readOnly"),
    (lambda t: _pub(t).update(volumeMounts=[m for m in _pub(t)["volumeMounts"] if m["name"] != "result"]),
     "result.*both"),
    (lambda t: _inv(t).update(volumeMounts=[m for m in _inv(t)["volumeMounts"] if m["name"] != "result"]),
     "result.*both"),
    (lambda t: _mount(_inv(t), "result").update(readOnly=True), "result.*writable"),
    (lambda t: next(v for v in _podspec(t)["volumes"] if v["name"] == "result").update(
        emptyDir=None, configMap={"name": "x"}), "result.*emptyDir"),
    # publish's HOME on the shared volume, on investigate's volume, on no volume, or unset
    (lambda t: _set_env(_pub(t), "HOME", "/result/home"), "HOME"),
    (lambda t: (_pub(t)["volumeMounts"].append({"name": "investigate-work", "mountPath": "/work"}),
                _set_env(_pub(t), "HOME", "/work/home")), "shared|HOME"),
    (lambda t: _set_env(_pub(t), "HOME", "/home/nonroot"), "HOME"),
    (lambda t: _pub(t).update(env=[e for e in _pub(t)["env"] if e["name"] != "HOME"]), "HOME"),
    (lambda t: (_mount(_pub(t), "publish-work").update(readOnly=True)), "HOME"),
    # python not isolated: user site-packages under a writable HOME would run first
    (lambda t: _pub(t).update(command=["python", "-m", "triage.publish"]), "-I"),
    (lambda t: _inv(t).update(command=["python", "-m", "triage.investigate"]), "-I"),
    (lambda t: _pub(t).pop("command"), "-I"),
])
def test_check_template_enforces_the_containment_split(mutate, message):
    t = copy.deepcopy(TEMPLATE)
    mutate(t)
    with pytest.raises(ValueError, match=message):
        jobs.check_template(t)


@pytest.mark.parametrize("home", ["/publish/../result/home", "/publish/../work/home", "publish/home", "./home", "/publish/a/../../result"])
def test_check_template_judges_publish_home_after_normalisation(home):
    t = copy.deepcopy(TEMPLATE)
    _set_env(_pub(t), "HOME", home)
    with pytest.raises(ValueError, match="HOME"):
        jobs.check_template(t)


@pytest.mark.parametrize("pick", [_pub, _inv])
def test_check_template_rejects_a_duplicate_env_name(pick):
    # the kubelet uses the last entry for a name, the check used to read the first
    t = copy.deepcopy(TEMPLATE)
    pick(t)["env"].append({"name": "HOME", "value": "/result/home"})
    with pytest.raises(ValueError, match="duplicate.*HOME"):
        jobs.check_template(t)


@pytest.mark.parametrize("mutate", [
    lambda t: _pub(t).update(args=["-c", "import os"]),
    lambda t: _inv(t).update(args=["-c", "import os"]),
    lambda t: _pub(t).update(command=["python", "-I"], args=["-c", "import os"]),
    lambda t: _pub(t).update(command=["python", "-I", "-u", "-m", "triage.publish", "--evil"]),
    lambda t: _inv(t).update(command=["python", "-I", "-c", "import os"]),
    lambda t: _inv(t).update(command=["python", "-I", "-u", "-m", "triage.publish"]),
])
def test_check_template_requires_the_exact_entrypoint_and_no_args(mutate):
    t = copy.deepcopy(TEMPLATE)
    mutate(t)
    with pytest.raises(ValueError, match="entrypoint|args"):
        jobs.check_template(t)


def test_check_template_allows_a_home_below_a_private_mount():
    t = copy.deepcopy(TEMPLATE)
    _set_env(_pub(t), "HOME", "/publish/deeper/home")
    jobs.check_template(t)


def _add_volume(t, volume, container=_pub):
    _podspec(t)["volumes"].append(dict(volume, name="extra"))
    container(t)["volumeMounts"].append({"name": "extra", "mountPath": "/extra"})


@pytest.mark.parametrize("volume", [
    {"hostPath": {"path": "/var/run"}},
    {"persistentVolumeClaim": {"claimName": "x"}},
    {"csi": {"driver": "x"}},
    {"ephemeral": {"volumeClaimTemplate": {"spec": {}}}},
    {"projected": {"sources": [{"configMap": {"name": "x"}}]}},
    {"downwardAPI": {"items": []}},
    {"nfs": {"server": "nas", "path": "/x"}},
    {"iscsi": {"targetPortal": "x", "iqn": "y", "lun": 0}},
    {"image": {"reference": "x"}},
    {},
])
def test_check_template_allows_only_emptydir_configmap_and_secret_volumes(volume):
    t = copy.deepcopy(TEMPLATE)
    _add_volume(t, volume)
    with pytest.raises(ValueError, match="volume 'extra'.*only emptyDir, configMap and secret"):
        jobs.check_template(t)


def test_an_unmounted_disallowed_volume_is_still_rejected():
    t = copy.deepcopy(TEMPLATE)
    _podspec(t)["volumes"].append({"name": "extra", "hostPath": {"path": "/"}})
    with pytest.raises(ValueError, match="volume 'extra'"):
        jobs.check_template(t)


@pytest.mark.parametrize("key", ["subPath", "subPathExpr"])
@pytest.mark.parametrize("container", [_inv, _pub])
def test_check_template_rejects_subpath_mounts(key, container):
    t = copy.deepcopy(TEMPLATE)
    _mount(container(t), "result")[key] = "x"
    with pytest.raises(ValueError, match=key):
        jobs.check_template(t)


@pytest.mark.parametrize("mutate,message", [
    (lambda t: _pub(t).update(envFrom=[{"configMapRef": {"name": "x"}}]), "envFrom"),
    (lambda t: _inv(t).update(envFrom=[{"configMapRef": {"name": "x"}}]), "envFrom"),
    (lambda t: _pub(t)["env"].append({"name": "N", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}}),
     "fieldRef"),
    (lambda t: _inv(t)["env"].append({"name": "M", "valueFrom": {"resourceFieldRef": {"resource": "limits.memory"}}}),
     "resourceFieldRef"),
    (lambda t: _pub(t)["env"].append({"name": "C", "valueFrom": {"configMapKeyRef": {"name": "x", "key": "y"}}}),
     "configMapKeyRef"),
    (lambda t: _podspec(t).update(shareProcessNamespace=True), "shareProcessNamespace"),
    (lambda t: _podspec(t).update(hostPID=True), "hostPID"),
    (lambda t: _podspec(t).update(hostIPC=True), "hostIPC"),
    (lambda t: _podspec(t).update(hostNetwork=True), "hostNetwork"),
    (lambda t: _inv(t).update(securityContext={"privileged": True}), "privileged"),
    (lambda t: _pub(t).update(securityContext={"privileged": True}), "privileged"),
])
def test_check_template_rejects_process_and_env_escapes(mutate, message):
    t = copy.deepcopy(TEMPLATE)
    mutate(t)
    with pytest.raises(ValueError, match=message):
        jobs.check_template(t)


def test_check_template_keeps_literal_env_and_secret_key_refs():
    t = copy.deepcopy(TEMPLATE)
    _inv(t)["env"].append({"name": "K", "valueFrom": {"secretKeyRef": {"name": "triage-anthropic-apikey",
                                                                       "key": "api_key"}}})
    _podspec(t).update(shareProcessNamespace=False, hostPID=False, hostNetwork=False)
    _pub(t)["securityContext"] = {"privileged": False, "readOnlyRootFilesystem": True}
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
