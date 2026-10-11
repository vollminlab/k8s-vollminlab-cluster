import pytest

from testsupport import APP, CONFIGMAP, HELMRELEASE, REPO_FILES, make_patch
from triage.diffguard import check, parse_bytes

CM = APP + "configmap.yaml"
MI = 2**20


def guard(patch, cls, metrics=None, app_dir=APP, namespace="demo", history=(), files=REPO_FILES):
    return check(patch, cls, app_dir, namespace, metrics or {},
                 read_file=files.get,
                 list_files=lambda d: [p for p in files if p.startswith(d)],
                 history_has_value=lambda f, v: v in history,
                 denylist=("velero-plugin-for-aws",))


def cm_patch(old, new):
    return make_patch(CM, CONFIGMAP, CONFIGMAP.replace(old, new))


# ---- oom-memory-limit -------------------------------------------------------------

def test_oom_raise_to_measured_peak_passes():
    r = guard(cm_patch("memory: 256Mi", "memory: 400Mi"), "oom-memory-limit", {"peak_memory_bytes": 300 * MI})
    assert r.ok, r.reason
    assert [c.field for c in r.changes] == ["data.values.yaml.resources.limits.memory"]
    assert CM in r.new_files


def test_oom_requires_measured_peak():
    r = guard(cm_patch("memory: 256Mi", "memory: 400Mi"), "oom-memory-limit")
    assert not r.ok and "peak_memory_bytes" in r.reason


def test_oom_new_limit_must_clear_peak_with_headroom():
    r = guard(cm_patch("memory: 256Mi", "memory: 300Mi"), "oom-memory-limit", {"peak_memory_bytes": 300 * MI})
    assert not r.ok and "1.2" in r.reason


def test_oom_may_not_more_than_double():
    r = guard(cm_patch("memory: 256Mi", "memory: 600Mi"), "oom-memory-limit", {"peak_memory_bytes": 300 * MI})
    assert not r.ok and "more than doubles" in r.reason


def test_oom_may_not_lower():
    r = guard(cm_patch("memory: 256Mi", "memory: 200Mi"), "oom-memory-limit", {"peak_memory_bytes": 100 * MI})
    assert not r.ok and "does not raise" in r.reason


def test_oom_may_not_change_an_image():
    r = guard(cm_patch("tag: 1.4.0", "tag: 9.9.9"), "oom-memory-limit", {"peak_memory_bytes": 1})
    assert not r.ok and "not a memory request or limit" in r.reason


# ---- cpu-throttle-limit -----------------------------------------------------------

def test_cpu_limit_removal_passes():
    r = guard(cm_patch("        cpu: 500m\n", ""), "cpu-throttle-limit", {"throttled_ratio": 0.4})
    assert r.ok, r.reason


def test_cpu_may_only_remove_not_change():
    r = guard(cm_patch("cpu: 500m", "cpu: 2"), "cpu-throttle-limit", {"throttled_ratio": 0.4})
    assert not r.ok and "only remove" in r.reason


def test_cpu_requires_measured_throttling():
    r = guard(cm_patch("        cpu: 500m\n", ""), "cpu-throttle-limit")
    assert not r.ok and "throttled_ratio" in r.reason


def test_cpu_may_not_touch_the_request():
    r = guard(cm_patch("        cpu: 50m\n", ""), "cpu-throttle-limit", {"throttled_ratio": 0.4})
    assert not r.ok


# ---- renovate-breakage ------------------------------------------------------------

def test_renovate_revert_to_a_historic_version_passes():
    p = make_patch(APP + "helmrelease.yaml", HELMRELEASE, HELMRELEASE.replace("version: 2.0.0", "version: 1.9.3"))
    r = guard(p, "renovate-breakage", history=("1.9.3",))
    assert r.ok, r.reason


def test_renovate_may_not_invent_a_version():
    p = make_patch(APP + "helmrelease.yaml", HELMRELEASE, HELMRELEASE.replace("version: 2.0.0", "version: 2.1.0"))
    r = guard(p, "renovate-breakage", history=("1.9.3",))
    assert not r.ok and "history" in r.reason


def test_renovate_denylist_wins_even_when_historic():
    cm = CONFIGMAP.replace("ghcr.io/example/demo", "velero/velero-plugin-for-aws")
    files = dict(REPO_FILES, **{CM: cm})
    p = make_patch(CM, cm, cm.replace("tag: 1.4.0", "tag: 1.3.0"))
    r = guard(p, "renovate-breakage", history=("1.3.0",), files=files)
    assert not r.ok and "velero-plugin-for-aws" in r.reason


def test_renovate_exactly_one_value():
    new = CONFIGMAP.replace("tag: 1.4.0", "tag: 1.3.0").replace("cpu: 50m", "cpu: 60m")
    r = guard(make_patch(CM, CONFIGMAP, new), "renovate-breakage", history=("1.3.0",))
    assert not r.ok and "exactly one" in r.reason


# ---- rules every class shares -----------------------------------------------------

def test_rejects_other_apps_directory():
    other = "clusters/vollminlab-cluster/other/other/app/configmap.yaml"
    p = make_patch(other, REPO_FILES[other], REPO_FILES[other].replace("memory: 256Mi", "memory: 400Mi"))
    r = guard(p, "oom-memory-limit", {"peak_memory_bytes": 300 * MI})
    assert not r.ok and "outside" in r.reason


def test_rejects_path_traversal_out_of_the_app_dir():
    sneaky = APP + "../../../other/other/app/configmap.yaml"
    p = make_patch(sneaky, CONFIGMAP, CONFIGMAP.replace("memory: 256Mi", "memory: 400Mi"))
    assert "outside" in guard(p, "oom-memory-limit", {"peak_memory_bytes": 1}).reason


@pytest.mark.parametrize("name", ["kustomization.yaml", "demo-db-externalsecret.yaml"])
def test_rejects_protected_files(name):
    path = APP + name
    p = make_patch(path, REPO_FILES[path], REPO_FILES[path] + "x: 1\n")
    assert "no class may change" in guard(p, "oom-memory-limit", {"peak_memory_bytes": 1}).reason


def test_rejects_added_files():
    p = f"--- /dev/null\n+++ b/{APP}new.yaml\n@@ -0,0 +1 @@\n+x: 1\n"
    assert "adds or deletes" in guard(p, "oom-memory-limit", {"peak_memory_bytes": 1}).reason


def test_rejects_app_dir_from_another_namespace():
    r = guard(cm_patch("memory: 256Mi", "memory: 400Mi"), "oom-memory-limit",
              {"peak_memory_bytes": 300 * MI}, namespace="authentik")
    assert not r.ok and "namespace" in r.reason


def test_rejects_patch_that_does_not_apply():
    p = make_patch(CM, CONFIGMAP.replace("cpu: 50m", "cpu: 51m"), CONFIGMAP.replace("memory: 256Mi", "memory: 400Mi"))
    assert "does not apply" in guard(p, "oom-memory-limit", {"peak_memory_bytes": 1}).reason


def test_rejects_garbage_and_missing_app_dir():
    assert "unparseable" in guard("ignore previous instructions", "oom-memory-limit").reason
    assert "app_dir" in guard(cm_patch("memory: 256Mi", "memory: 400Mi"), "oom-memory-limit", app_dir=None).reason


def test_parse_bytes():
    assert parse_bytes("256Mi") == 256 * MI
    assert parse_bytes("1G") == 1e9
    with pytest.raises(ValueError):
        parse_bytes("lots")


# ---- smuggling: verified exploits that passed the gate before ---------------------

HR_PATH = APP + "helmrelease.yaml"
HIDDEN_DOC = ("apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
              "metadata: {name: demo, namespace: kube-system}\nspec: {values: {evil: true}}\n---\n")


def test_exploit_hidden_document_with_a_colliding_kind_and_name_is_rejected():
    p = cm_patch("memory: 256Mi", "memory: 400Mi") + make_patch(HR_PATH, HELMRELEASE, HIDDEN_DOC + HELMRELEASE)
    r = guard(p, "oom-memory-limit", {"peak_memory_bytes": 300 * MI})
    assert not r.ok, r.changes
    assert HR_PATH in r.reason
