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
    def denied(self, *a, **kw):
        raise PermissionError(13, "Permission denied", str(self))
    monkeypatch.setattr(type(p), "read_text", denied)
    r = load_result(p)
    assert r["failure_reason"] == "invalid_output" and "Permission denied" in r["detail"]


def test_load_result_invalid_utf8_is_a_failure_record(tmp_path):
    p = tmp_path / "result.json"
    p.write_bytes(b'{"status": "\xff"}')
    assert load_result(p)["failure_reason"] == "invalid_output"
