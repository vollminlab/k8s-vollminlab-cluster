import copy
import json

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
