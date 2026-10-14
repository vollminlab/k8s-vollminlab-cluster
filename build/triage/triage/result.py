"""result.json: the investigate -> publish contract."""
import json
import math
import os
import stat
from pathlib import Path

import jsonschema

AGENT_SCHEMA = json.loads(Path(__file__).with_name("agent_output.schema.json").read_text())
FAILURE_REASONS = ("max_turns", "budget_cap", "timeout", "api_error", "invalid_output", "setup_failed")
# result.json is read by publish, which holds the GitHub key; bound what it parses.
MAX_RESULT_BYTES = 1 << 20
# tool_denials is rendered into the issue body; GitHub refuses a body over 65,536
# characters, so an unbounded list could suppress the whole issue.
MAX_DENIALS = 50
MAX_DENIAL_CHARS = 500
# `$schema` goes too: Claude Code's --json-schema validator knows only its default draft
# and rejects a 2020-12 declaration outright ("no schema with key or ref"), so claude
# exits before any API call. validate_agent_output still applies the full 2020-12 schema.
_RELAXED_DROP = ("pattern", "maxLength", "maxItems", "$schema")


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
    """A finite, non-negative number. NaN makes every budget comparison false, inf
    and negatives poison or refund the daily spend, and bool is an int subclass."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0


def _read_capped(path: Path):
    """(bytes, None) or (None, why). The file must be a regular file reached without
    following a symlink: result.json lives on the volume the investigate container
    (where a possibly prompt-injected model ran) could write, so it could have been
    swapped for a link to one of publish's own secrets, or a FIFO that blocks forever."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return None, "investigate produced no result.json"
    except OSError as e:
        return None, f"result.json could not be read: {type(e).__name__}: {e}"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "result.json could not be read: it is not a regular file"
        if st.st_size > MAX_RESULT_BYTES:
            return None, f"result.json is {st.st_size} bytes, over the {MAX_RESULT_BYTES}-byte cap"
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESULT_BYTES:      # grew after fstat
                return None, f"result.json is over the {MAX_RESULT_BYTES}-byte cap"
            chunks.append(chunk)
        return b"".join(chunks), None
    except OSError as e:
        return None, f"result.json could not be read: {type(e).__name__}: {e}"
    finally:
        os.close(fd)


def _normalized_failure(data: dict) -> dict:
    """A fresh failure() built only from the record's validated fields, so a failure
    record can never carry a patch, an app_dir or anything else alongside."""
    detail = data.get("detail")
    if not isinstance(detail, str):
        detail = f"(result.json carried a non-string detail of type {type(detail).__name__})"
    return failure(data["failure_reason"], detail, data.get("cost_usd", 0.0),
                   cost_estimated=data.get("cost_estimated") is True)


def load_result(path) -> dict:
    """Read result.json; anything malformed becomes a failure record, never an exception.

    The returned record is always rebuilt from validated parts: success() over the
    schema-checked agent fields, or failure() over a known reason and a string detail."""
    raw, why = _read_capped(Path(path))
    if raw is None:
        return failure("invalid_output", why)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as e:     # not UTF-8, not JSON, or absurdly nested
        return failure("invalid_output", f"result.json is not JSON: {type(e).__name__}: {str(e)[:300]}")
    if not isinstance(data, dict):
        return failure("invalid_output", f"result.json is not a JSON object but {type(data).__name__}")
    if not _is_cost(data.get("cost_usd", 0.0)):
        return failure("invalid_output",
                       f"result.json cost_usd is not a finite non-negative number: {data.get('cost_usd')!r}"[:500])
    status = data.get("status")
    if status == "failed":
        if data.get("failure_reason") in FAILURE_REASONS:
            return _normalized_failure(data)
        return failure("invalid_output", f"result.json failure_reason {str(data.get('failure_reason'))[:100]!r} "
                                         f"is not one of {FAILURE_REASONS}", data.get("cost_usd", 0.0))
    if status != "ok":
        return failure("invalid_output", f"result.json status {str(status)[:100]!r} is neither ok nor failed",
                       data.get("cost_usd", 0.0))
    agent_part = {k: v for k, v in data.items() if k in AGENT_SCHEMA["properties"]}
    errors = validate_agent_output(agent_part)
    if errors:
        return failure("invalid_output", "; ".join(errors), data.get("cost_usd", 0.0))
    denials = data.get("tool_denials")
    if not (isinstance(denials, list) and all(isinstance(d, str) for d in denials)):
        denials = []
    denials = [d[:MAX_DENIAL_CHARS] for d in denials[:MAX_DENIALS]]
    return success(agent_part, data.get("cost_usd", 0.0), denials)
