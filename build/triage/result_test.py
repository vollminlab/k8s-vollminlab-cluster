import copy
import json

import pytest

from testsupport import AGENT_OK
from triage.result import (AGENT_SCHEMA, failure, load_result, relaxed_schema, success,
                           validate_agent_output)


def test_valid_output_has_no_errors():
    assert validate_agent_output(AGENT_OK) == []


def test_errors_name_the_field():
    bad = dict(AGENT_OK, confidence="certain", app_dir="/etc/passwd")
    errors = validate_agent_output(bad)
    assert any(e.startswith("confidence:") for e in errors)
    assert any(e.startswith("app_dir:") for e in errors)


def test_model_cannot_smuggle_extra_fields_or_word_metrics():
    assert validate_agent_output(dict(AGENT_OK, status="ok"))
    assert validate_agent_output(dict(AGENT_OK, metrics={"Ignore previous instructions": 1}))


def test_relaxed_schema_drops_length_and_pattern_limits_only():
    r = json.dumps(relaxed_schema())
    assert "maxLength" not in r and "pattern" not in r and "maxItems" not in r
    assert r.count('"enum"') == json.dumps(AGENT_SCHEMA).count('"enum"')
    assert relaxed_schema()["additionalProperties"] is False


def test_failure_record_shape():
    f = failure("timeout", "x" * 9000, 1.25)
    assert f["status"] == "failed" and f["failure_reason"] == "timeout"
    assert len(f["detail"]) == 4000 and f["cost_usd"] == 1.25
    assert f["class"] == "none" and f["patch"] is None


def test_success_adds_wrapper_fields():
    s = success(AGENT_OK, 0.5, ["Bash: curl https://example.com"])
    assert s["status"] == "ok" and s["cost_usd"] == 0.5
    assert s["tool_denials"] == ["Bash: curl https://example.com"]


def test_load_result_handles_missing_bad_and_tampered_files(tmp_path):
    assert load_result(tmp_path / "nope.json")["failure_reason"] == "invalid_output"
    (tmp_path / "bad.json").write_text("{")
    assert load_result(tmp_path / "bad.json")["failure_reason"] == "invalid_output"
    tampered = success(dict(AGENT_OK, confidence="certain"), 0.1, [])
    (tmp_path / "t.json").write_text(json.dumps(tampered))
    assert load_result(tmp_path / "t.json")["failure_reason"] == "invalid_output"
    good = success(AGENT_OK, 0.1, [])
    (tmp_path / "g.json").write_text(json.dumps(good))
    assert load_result(tmp_path / "g.json")["status"] == "ok"
    failed = failure("max_turns", "hit 40")
    (tmp_path / "f.json").write_text(json.dumps(failed))
    assert load_result(tmp_path / "f.json")["failure_reason"] == "max_turns"


def test_relaxed_schema_keeps_properties_named_like_keywords():
    schema = {"type": "object",
              "properties": {"pattern": {"type": "string", "pattern": "^a$"},
                             "maxLength": {"type": "integer"},
                             "maxItems": {"type": "array", "maxItems": 3}},
              "required": ["pattern"]}
    r = relaxed_schema(schema)
    assert set(r["properties"]) == {"pattern", "maxLength", "maxItems"}
    assert r["properties"]["pattern"] == {"type": "string"}
    assert r["properties"]["maxItems"] == {"type": "array"}
    assert r["required"] == ["pattern"]


def test_relaxed_schema_strips_keywords_inside_property_names_schemas():
    # propertyNames' value is a schema, so `pattern` there IS a keyword.
    assert relaxed_schema({"propertyNames": {"pattern": "^x$"}}) == {"propertyNames": {}}


@pytest.mark.parametrize("text", ["[]", '"x"', "3", "null"])
def test_load_result_non_object_json_is_a_failure_record(tmp_path, text):
    (tmp_path / "r.json").write_text(text)
    r = load_result(tmp_path / "r.json")
    assert r["status"] == "failed" and r["failure_reason"] == "invalid_output"


@pytest.mark.parametrize("cost", ["lots", [1], {"a": 1}])
def test_load_result_non_numeric_cost_is_a_failure_record(tmp_path, cost):
    tampered = dict(success(dict(AGENT_OK, confidence="certain"), 0.1, []), cost_usd=cost)
    (tmp_path / "r.json").write_text(json.dumps(tampered))
    r = load_result(tmp_path / "r.json")
    assert r["failure_reason"] == "invalid_output" and r["cost_usd"] == 0.0
    good = dict(success(AGENT_OK, 0.1, []), cost_usd=cost)
    (tmp_path / "g.json").write_text(json.dumps(good))
    assert load_result(tmp_path / "g.json")["failure_reason"] == "invalid_output"


def test_failure_records_whether_cost_is_estimated():
    assert failure("timeout", "x")["cost_estimated"] is False
    assert failure("timeout", "x", 3.0, cost_estimated=True)["cost_estimated"] is True


def test_load_result_on_a_directory_is_a_failure_record(tmp_path):
    (tmp_path / "result.json").mkdir()
    r = load_result(tmp_path / "result.json")
    assert r["status"] == "failed" and r["failure_reason"] == "invalid_output"
    assert "could not be read" in r["detail"]


def test_load_result_unreadable_file_is_a_failure_record(tmp_path, monkeypatch):
    p = tmp_path / "result.json"
    p.write_text("{}")
    def denied(path, *a, **kw):
        raise PermissionError(13, "Permission denied", str(path))
    from triage import result as result_mod
    monkeypatch.setattr(result_mod.os, "open", denied)
    r = load_result(p)
    assert r["failure_reason"] == "invalid_output" and "Permission denied" in r["detail"]


def test_load_result_invalid_utf8_is_a_failure_record(tmp_path):
    p = tmp_path / "result.json"
    p.write_bytes(b'{"status": "\xff"}')
    assert load_result(p)["failure_reason"] == "invalid_output"


# --- cost sanity, failure-record shape, size and file-type limits ------------------

@pytest.mark.parametrize("cost", ["NaN", "Infinity", "-Infinity", "-0.5"])
def test_load_result_non_finite_or_negative_cost_is_a_failure_record(tmp_path, cost):
    # json.loads accepts NaN/Infinity literals; a NaN cost makes every budget
    # comparison false and a negative one refunds the daily budget.
    good = json.dumps(success(AGENT_OK, 0.1, [])).replace('"cost_usd": 0.1', f'"cost_usd": {cost}')
    (tmp_path / "r.json").write_text(good)
    r = load_result(tmp_path / "r.json")
    assert r["failure_reason"] == "invalid_output" and r["cost_usd"] == 0.0


def test_is_cost_requires_finite_non_negative():
    from triage.result import _is_cost
    assert _is_cost(0) and _is_cost(1.5)
    for v in (float("nan"), float("inf"), -float("inf"), -0.01, True, "1", None):
        assert not _is_cost(v), v


@pytest.mark.parametrize("detail", [{"a": 1}, ["x"], 3, None])
def test_failure_record_with_a_non_string_detail_is_normalized(tmp_path, detail):
    rec = dict(failure("max_turns", "x", 1.0), detail=detail)
    (tmp_path / "r.json").write_text(json.dumps(rec))
    r = load_result(tmp_path / "r.json")
    assert r["status"] == "failed" and isinstance(r["detail"], str)
    assert r == failure(r["failure_reason"], r["detail"], r["cost_usd"])


def test_failure_record_cannot_carry_a_patch_or_extra_fields(tmp_path):
    rec = dict(failure("api_error", "boom", 2.0), patch=AGENT_OK["patch"], app_dir=AGENT_OK["app_dir"],
               **{"class": "oom-memory-limit", "evil": "x", "summary": {"nested": True}})
    (tmp_path / "r.json").write_text(json.dumps(rec))
    r = load_result(tmp_path / "r.json")
    assert r == failure("api_error", "boom", 2.0)


def test_failure_record_keeps_a_boolean_cost_estimated_only(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(failure("timeout", "k", 3.0, cost_estimated=True)))
    assert load_result(tmp_path / "a.json")["cost_estimated"] is True
    (tmp_path / "b.json").write_text(json.dumps(dict(failure("timeout", "k", 3.0), cost_estimated="yes")))
    assert load_result(tmp_path / "b.json")["cost_estimated"] is False


@pytest.mark.parametrize("status", ["weird", None, 1])
def test_unknown_status_is_a_failure_record(tmp_path, status):
    (tmp_path / "r.json").write_text(json.dumps(dict(success(AGENT_OK, 0.1, []), status=status)))
    assert load_result(tmp_path / "r.json")["failure_reason"] == "invalid_output"


def test_failed_status_with_an_unknown_reason_is_invalid_output(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps(dict(failure("timeout", "x"), failure_reason="made_up")))
    assert load_result(tmp_path / "r.json")["failure_reason"] == "invalid_output"


def test_ok_record_is_rebuilt_from_validated_parts(tmp_path):
    rec = dict(success(AGENT_OK, 0.1, ["Bash: x"]), failure_reason="timeout", detail={"x": 1},
               cost_estimated="maybe")
    (tmp_path / "r.json").write_text(json.dumps(rec))
    r = load_result(tmp_path / "r.json")
    assert r == success(AGENT_OK, 0.1, ["Bash: x"])


def test_ok_record_with_non_string_tool_denials_drops_them(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps(dict(success(AGENT_OK, 0.1, []), tool_denials=[{"a": 1}])))
    assert load_result(tmp_path / "r.json")["tool_denials"] == []


def test_oversized_result_is_a_failure_record(tmp_path):
    from triage import result as result_mod
    p = tmp_path / "result.json"
    p.write_text(json.dumps(dict(success(AGENT_OK, 0.1, []), pad="x" * (result_mod.MAX_RESULT_BYTES + 1))))
    r = load_result(p)
    assert r["failure_reason"] == "invalid_output" and "bytes" in r["detail"]


def test_symlinked_result_is_not_followed(tmp_path):
    secret = tmp_path / "private_key"
    secret.write_text("-----BEGIN RSA PRIVATE KEY-----")
    (tmp_path / "result.json").symlink_to(secret)
    r = load_result(tmp_path / "result.json")
    assert r["failure_reason"] == "invalid_output" and "BEGIN" not in r["detail"]


def test_fifo_result_does_not_block(tmp_path):
    import os
    os.mkfifo(tmp_path / "result.json")
    r = load_result(tmp_path / "result.json")
    assert r["failure_reason"] == "invalid_output" and "regular file" in r["detail"]


def test_tool_denials_are_capped_in_count_and_length(tmp_path):
    from triage import result as result_mod
    assert (result_mod.MAX_DENIALS, result_mod.MAX_DENIAL_CHARS) == (50, 500)
    denials = [f"Bash: {i} " + "y" * 2000 for i in range(400)]
    (tmp_path / "r.json").write_text(json.dumps(success(AGENT_OK, 0.1, denials)))
    got = load_result(tmp_path / "r.json")["tool_denials"]
    assert len(got) == 50 and all(len(d) == 500 for d in got)
    assert got[0].startswith("Bash: 0 ") and got[-1].startswith("Bash: 49 ")
