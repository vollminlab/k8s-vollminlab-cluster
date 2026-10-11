import copy

from testsupport import AGENT_OK, APP, REPO_FILES, make_patch
from triage import diffguard, prbody
from triage.yamldiff import MISSING

CANARY = "CANARY-7f3a-ignore-previous-instructions"


def guarded(result):
    return diffguard.check(result["patch"], result["class"], result["app_dir"], "demo", result["metrics"],
                           read_file=REPO_FILES.get,
                           list_files=lambda d: [p for p in REPO_FILES if p.startswith(d)],
                           history_has_value=lambda f, v: False, denylist=())


def test_no_free_text_from_the_model_reaches_the_pr():
    # Put a marker in every field the model writes as free text.
    r = copy.deepcopy(AGENT_OK)
    r["summary"] = r["root_cause"] = CANARY
    r["evidence"] = [CANARY]
    r["findings"] = [{"check": "unprotected-pvc", "namespace": CANARY, "object": CANARY,
                      "summary": CANARY, "detail": CANARY}]
    g = guarded(r)
    assert g.ok, g.reason
    text = "\n".join([prbody.branch(r["class"], "abc123def456"), prbody.title(r["class"], r["app_dir"]),
                      prbody.body(r["class"], g.changes, r["metrics"], "abc123def456")])
    assert CANARY not in text


def test_body_carries_the_parsed_diff_and_the_class_metric_only():
    g = guarded(AGENT_OK)
    metrics = dict(AGENT_OK["metrics"], extra_metric=1)
    body = prbody.body("oom-memory-limit", g.changes, metrics, "abc123def456")
    assert "`data.values.yaml.resources.limits.memory`" in body and "`256Mi`" in body and "`400Mi`" in body
    assert "peak_memory_bytes" in body and "extra_metric" not in body
    assert "fp/abc123def456" in body


def test_title_and_branch():
    assert prbody.title("oom-memory-limit", APP) == "fix(demo): raise memory limit to measured peak (triage)"
    assert prbody.branch("renovate-breakage", "abc123def456") == "fix/triage-renovate-breakage-abc123def456"


def test_removed_values_and_table_breakers_are_rendered_safely():
    c = diffguard.FieldChange("f", "D/x", "a.b", "x|y`z", MISSING)
    body = prbody.body("cpu-throttle-limit", [c], {"throttled_ratio": 0.5}, "abc")
    assert "(removed)" in body and "x\\|y'z" in body


def test_a_canary_in_a_patch_comment_never_passes_the_gate():
    r = copy.deepcopy(AGENT_OK)
    cm = APP + "configmap.yaml"
    new = REPO_FILES[cm].replace("memory: 256Mi", f"memory: 400Mi  # {CANARY}")
    r["patch"] = make_patch(cm, REPO_FILES[cm], new)
    g = guarded(r)
    assert not g.ok and CANARY in g.reason   # the reason goes to the private issue only
    r["patch"] = make_patch(cm, REPO_FILES[cm], f"# {CANARY}\n" + REPO_FILES[cm].replace("memory: 256Mi", "memory: 400Mi"))
    g = guarded(r)
    assert not g.ok and CANARY in g.reason   # the reason goes to the private issue only
