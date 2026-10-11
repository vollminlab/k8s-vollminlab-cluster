"""result.json: the investigate -> publish contract."""
import json
from pathlib import Path

import jsonschema

AGENT_SCHEMA = json.loads(Path(__file__).with_name("agent_output.schema.json").read_text())
FAILURE_REASONS = ("max_turns", "budget_cap", "timeout", "api_error", "invalid_output", "setup_failed")
_RELAXED_DROP = ("pattern", "maxLength", "maxItems")


def relaxed_schema(schema=AGENT_SCHEMA):
    """The schema handed to `claude --json-schema`: shapes and enums only.

    Length and pattern limits are enforced afterwards by validate_agent_output, so a
    structured-output implementation that does not support them cannot weaken them.
    """
    if isinstance(schema, dict):
        return {k: relaxed_schema(v) for k, v in schema.items() if k not in _RELAXED_DROP}
    if isinstance(schema, list):
        return [relaxed_schema(v) for v in schema]
    return schema


def validate_agent_output(data) -> list:
    validator = jsonschema.Draft202012Validator(AGENT_SCHEMA)
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors]


def failure(reason: str, detail: str, cost_usd: float = 0.0) -> dict:
    if reason not in FAILURE_REASONS:
        raise ValueError(f"unknown failure reason {reason!r}")
    return {"status": "failed", "failure_reason": reason, "detail": str(detail)[:4000],
            "cost_usd": float(cost_usd), "tool_denials": [],
            "summary": "", "root_cause": "", "evidence": [], "confidence": "low", "class": "none",
            "app_dir": None, "patch": None, "metrics": {}, "injection_suspected": False, "findings": []}


def success(agent_output: dict, cost_usd: float, tool_denials: list) -> dict:
    result = dict(agent_output)
    result.update(status="ok", failure_reason=None, detail="", cost_usd=float(cost_usd),
                  tool_denials=list(tool_denials))
    return result


def load_result(path) -> dict:
    """Read result.json; anything malformed becomes a failure record, never an exception."""
    p = Path(path)
    if not p.exists():
        return failure("invalid_output", "investigate produced no result.json")
    try:
        data = json.loads(p.read_text())
    except ValueError as e:
        return failure("invalid_output", f"result.json is not JSON: {e}")
    if data.get("status") == "failed" and data.get("failure_reason") in FAILURE_REASONS:
        return data
    agent_part = {k: v for k, v in data.items() if k in AGENT_SCHEMA["properties"]}
    errors = validate_agent_output(agent_part)
    if errors:
        return failure("invalid_output", "; ".join(errors), data.get("cost_usd", 0.0))
    return data
