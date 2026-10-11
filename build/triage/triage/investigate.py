"""Init container: clone the repo, run Claude Code once, ALWAYS leave a result.json.

Contract: this process exits 0 in every case. If it exited non-zero (or the
container died), the publish container would never start and the incident would
vanish silently, so every failure becomes a failure record that publish files as
an `agent-error` issue.
"""
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .incident import Incident
from .policy import load_policy
from .result import failure, relaxed_schema, success, validate_agent_output

PROMPTS = Path(__file__).with_name("prompts")
WORK = Path("/work")
REPO_URL = "https://github.com/{repo}.git"
CLONE_TIMEOUT = 300
# Seconds of the Job's activeDeadlineSeconds left for publish after claude.
PUBLISH_RESERVE_SECONDS = 300


def render_prompt(inc: Incident, policy) -> str:
    if inc.source == "sweep":
        return (PROMPTS / "sweep.md").read_text().replace("{{CHECKLIST}}", policy.sweep_checklist)
    return (PROMPTS / "incident.md").read_text().replace(
        "{{INCIDENT}}", json.dumps(asdict(inc), indent=2, sort_keys=True))


def claude_command(prompt: str, policy) -> list:
    return ["claude", "-p", prompt,
            "--output-format", "json",
            "--json-schema", json.dumps(relaxed_schema()),
            "--max-turns", str(policy.max_turns),
            "--max-budget-usd", str(policy.per_run_budget_usd),
            "--permission-mode", "dontAsk",
            "--model", policy.model,
            # project = CLAUDE.md and .claude/rules from the checkout. user/local are
            # skipped; managed settings (/etc/claude-code) always apply on top.
            "--setting-sources", "project",
            "--no-session-persistence"]


def _denials(out: dict) -> list:
    return [f"{d.get('tool_name')}: {json.dumps(d.get('tool_input'), sort_keys=True)[:300]}"
            for d in out.get("permission_denials") or []]


def interpret(stdout: str, stderr: str) -> dict:
    try:
        out = json.loads(stdout)
    except ValueError:
        out = None
    if not isinstance(out, dict):
        return failure("invalid_output",
                       f"claude did not print a JSON object. stdout={stdout[:1500]!r} stderr={stderr[-1500:]!r}")
    cost = float(out.get("total_cost_usd") or 0.0)
    subtype = out.get("subtype")
    errors = "; ".join(str(e) for e in out.get("errors") or [])
    if subtype == "error_max_turns":
        return failure("max_turns", errors, cost)
    if subtype == "error_max_budget_usd":
        return failure("budget_cap", errors, cost)
    if out.get("is_error") or subtype != "success":
        return failure("api_error", f"subtype={subtype} api_error_status={out.get('api_error_status')} "
                                    f"errors={errors} result={str(out.get('result'))[:500]}", cost)
    structured = out.get("structured_output")
    problems = validate_agent_output(structured)
    if problems:
        return failure("invalid_output", "; ".join(problems), cost)
    return success(structured, cost, _denials(out))


def investigate(inc: Incident, policy, work: Path = WORK, run=subprocess.run) -> dict:
    repo = work / "repo"
    try:
        # Blobless, not shallow: renovate-breakage needs the file's git history.
        run(["git", "clone", "--filter=blob:none", "--quiet",
             REPO_URL.format(repo=policy.cluster_repo), str(repo)],
            check=True, capture_output=True, text=True, timeout=CLONE_TIMEOUT)
    except subprocess.CalledProcessError as e:
        return failure("setup_failed", f"git clone failed: {e.stderr or e}")
    except (subprocess.TimeoutExpired, OSError) as e:
        return failure("setup_failed", f"git clone failed: {e}")
    for name in ("settings.json", "settings.local.json"):
        if (repo / ".claude" / name).exists():
            return failure("invalid_output",
                           f".claude/{name} exists in the checkout; refusing to run, because "
                           f"claude -p would execute its hooks without a trust prompt")
    # HOME (/work/home in the Job) must exist and be writable: the root filesystem is
    # read-only, and claude and kubectl both write their config and caches under it.
    (work / "home").mkdir(exist_ok=True)
    (work / "scratch").mkdir(exist_ok=True)
    budget = policy.job_deadline_seconds - PUBLISH_RESERVE_SECONDS
    try:
        cp = run(claude_command(render_prompt(inc, policy), policy),
                 cwd=str(repo), capture_output=True, text=True, timeout=budget)
    except subprocess.TimeoutExpired:
        return failure("timeout", f"claude did not finish within {budget}s")
    except OSError as e:
        return failure("setup_failed", f"could not start claude: {e}")
    return interpret(cp.stdout, cp.stderr)


def main() -> int:
    try:
        inc = Incident.from_json(os.environ["TRIAGE_INCIDENT"])
        result = investigate(inc, load_policy())
    except Exception as e:
        result = failure("setup_failed", f"{type(e).__name__}: {e}")
    (WORK / "result.json").write_text(json.dumps(result, indent=2))
    print(f"investigate: status={result['status']} reason={result['failure_reason']} "
          f"cost=${result['cost_usd']:.2f} denials={len(result.get('tool_denials', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
