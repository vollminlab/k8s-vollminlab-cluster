"""result.json: the investigate -> publish contract."""
import json
from pathlib import Path

import jsonschema

AGENT_SCHEMA = json.loads(Path(__file__).with_name("agent_output.schema.json").read_text())
FAILURE_REASONS = ("max_turns", "budget_cap", "timeout", "api_error", "invalid_output", "setup_failed")
_RELAXED_DROP = ("pattern", "maxLength", "maxItems")


# Keywords whose value maps NAMES to subschemas; their keys are never keywords.
_NAME_MAPS = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")


def relaxed_schema(schema=AGENT_SCHEMA):
    """The schema handed to `claude --json-schema`: shapes and enums only.

    Length and pattern limits are enforced afterwards by validate_agent_output, so a
    structured-output implementation that does not support them cannot weaken them.
    Only keywords are dropped: a property literally named "pattern" survives.
    """
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k in _RELAXED_DROP:
                continue
            if k in _NAME_MAPS and isinstance(v, dict):
                out[k] = {name: relaxed_schema(sub) for name, sub in v.items()}
            else:
                out[k] = relaxed_schema(v)
        return out
    if isinstance(schema, list):
        return [relaxed_schema(v) for v in schema]
    return schema


def validate_agent_output(data) -> list:
    validator = jsonschema.Draft202012Validator(AGENT_SCHEMA)
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors]


def failure(reason: str, detail: str, cost_usd: float = 0.0, cost_estimated: bool = False) -> dict:
    """A failure record. `cost_estimated` marks a cost that the CLI never reported
    (killed or garbled run) and was charged at the per-run cap instead."""
    if reason not in FAILURE_REASONS:
        raise ValueError(f"unknown failure reason {reason!r}")
    return {"status": "failed", "failure_reason": reason, "detail": str(detail)[:4000],
            "cost_usd": float(cost_usd), "cost_estimated": bool(cost_estimated), "tool_denials": [],
            "summary": "", "root_cause": "", "evidence": [], "confidence": "low", "class": "none",
            "app_dir": None, "patch": None, "metrics": {}, "injection_suspected": False, "findings": []}


def success(agent_output: dict, cost_usd: float, tool_denials: list) -> dict:
    result = dict(agent_output)
    result.update(status="ok", failure_reason=None, detail="", cost_usd=float(cost_usd),
                  tool_denials=list(tool_denials))
    return result


def _is_cost(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_result(path) -> dict:
    """Read result.json; anything malformed becomes a failure record, never an exception."""
    p = Path(path)
    if not p.exists():
        return failure("invalid_output", "investigate produced no result.json")
    try:
        data = json.loads(p.read_text())
    except ValueError as e:
        return failure("invalid_output", f"result.json is not JSON: {e}")
    if not isinstance(data, dict):
        return failure("invalid_output", f"result.json is not a JSON object but {type(data).__name__}")
    if not _is_cost(data.get("cost_usd", 0.0)):
        return failure("invalid_output", f"result.json cost_usd is not a number: {data.get('cost_usd')!r}"[:500])
    if data.get("status") == "failed" and data.get("failure_reason") in FAILURE_REASONS:
        return data
    agent_part = {k: v for k, v in data.items() if k in AGENT_SCHEMA["properties"]}
    errors = validate_agent_output(agent_part)
    if errors:
        return failure("invalid_output", "; ".join(errors), data.get("cost_usd", 0.0))
    return data
