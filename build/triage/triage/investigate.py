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
import time
from dataclasses import asdict
from pathlib import Path

from .incident import Incident
from .policy import load_policy
from .result import failure, relaxed_schema, success, validate_agent_output

PROMPTS = Path(__file__).with_name("prompts")
# Private to this container: the checkout, HOME and scratch. Nothing here is ever
# read by publish.
WORK = Path("/work")
# The only path publish reads: its own volume, mounted read-only there.
RESULT = Path("/result/result.json")
REPO_URL = "https://github.com/{repo}.git"
CLONE_TIMEOUT = 300
# Seconds of the Job's activeDeadlineSeconds left for publish after claude.
PUBLISH_RESERVE_SECONDS = 300
# activeDeadlineSeconds counts from the Job starting, not this process: pod scheduling
# and the image pull happen before investigate() is ever called.
STARTUP_MARGIN_SECONDS = 120
# Not worth starting claude with less than this left.
MIN_CLAUDE_SECONDS = 60


def render_prompt(inc: Incident, policy) -> str:
    if inc.source == "sweep":
        return (PROMPTS / "sweep.md").read_text().replace("{{CHECKLIST}}", policy.sweep_checklist)
    # The incident is untrusted (label values come from workloads). Escaping < and >
    # keeps it valid JSON while making it impossible to close the <incident> block.
    data = json.dumps(asdict(inc), indent=2, sort_keys=True).replace("<", "\\u003c").replace(">", "\\u003e")
    return (PROMPTS / "incident.md").read_text().replace("{{INCIDENT}}", data)


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


def _cost(out: dict):
    v = out.get("total_cost_usd")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def interpret(stdout: str, stderr: str, per_run_cap: float) -> dict:
    """Map the CLI's JSON to a result record. Wherever the CLI's cost is unknown on a
    failure, the run is charged `per_run_cap` (cost_estimated) so the daily budget
    sees it: a budget_cap run spent the cap by definition, and a max_turns or
    api_error run very likely spent real money. A reported cost always wins."""
    try:
        out = json.loads(stdout)
    except ValueError:
        out = None
    if not isinstance(out, dict):
        return failure("invalid_output",
                       f"claude did not print a JSON object. stdout={stdout[:1500]!r} stderr={stderr[-1500:]!r}",
                       per_run_cap, cost_estimated=True)
    known = _cost(out)

    def failed(reason, detail):
        if known is None:
            return failure(reason, detail, per_run_cap, cost_estimated=True)
        return failure(reason, detail, known)

    subtype = out.get("subtype")
    errors = "; ".join(str(e) for e in out.get("errors") or [])
    if subtype == "error_max_turns":
        return failed("max_turns", errors)
    if subtype == "error_max_budget_usd":
        return failed("budget_cap", errors)
    if out.get("is_error") or subtype != "success":
        return failed("api_error", f"subtype={subtype} api_error_status={out.get('api_error_status')} "
                                   f"errors={errors} result={str(out.get('result'))[:500]}")
    structured = out.get("structured_output")
    problems = validate_agent_output(structured)
    if problems:
        return failed("invalid_output", "; ".join(problems))
    return success(structured, known or 0.0, _denials(out))


def investigate(inc: Incident, policy, work: Path = WORK, run=subprocess.run, clock=time.monotonic) -> dict:
    started = clock()
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
            return failure("setup_failed",
                           f".claude/{name} exists in the checkout; refusing to run, because "
                           f"claude -p would execute its hooks without a trust prompt")
    # HOME (/work/home in the Job) must exist and be writable: the root filesystem is
    # read-only, and claude and kubectl both write their config and caches under it.
    (work / "home").mkdir(exist_ok=True)
    (work / "scratch").mkdir(exist_ok=True)
    elapsed = clock() - started
    budget = int(policy.job_deadline_seconds - PUBLISH_RESERVE_SECONDS - STARTUP_MARGIN_SECONDS - elapsed)
    if budget <= MIN_CLAUDE_SECONDS:
        return failure("timeout", f"only {budget}s of the Job deadline left after {elapsed:.0f}s of setup; "
                                  f"claude not started")
    try:
        cp = run(claude_command(render_prompt(inc, policy), policy),
                 cwd=str(repo), capture_output=True, text=True, timeout=budget)
    except subprocess.TimeoutExpired:
        # Killed mid-run: the CLI never reported its cost, and it very likely spent some.
        return failure("timeout", f"claude did not finish within {budget}s",
                       policy.per_run_budget_usd, cost_estimated=True)
    except OSError as e:
        return failure("setup_failed", f"could not start claude: {e}")
    return interpret(cp.stdout, cp.stderr, policy.per_run_budget_usd)


def write_result(path: Path, text: str) -> None:
    """Write via a temporary file and rename, so a symlink or FIFO planted at the path
    during the run is replaced rather than written through (publish then refuses
    anything that is not a regular file)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def main() -> int:
    try:
        inc = Incident.from_json(os.environ["TRIAGE_INCIDENT"])
        result = investigate(inc, load_policy(), work=WORK)
    except Exception as e:
        result = failure("setup_failed", f"{type(e).__name__}: {e}")
    text = json.dumps(result, indent=2)
    try:
        write_result(RESULT, text)
    except Exception as e:
        # Still exit 0: publish then finds no result.json and files that as an
        # agent-error; the record itself survives in this container's log.
        print(f"investigate: could not write result.json ({type(e).__name__}: {e}); result follows",
              file=sys.stderr)
        print(text, file=sys.stderr)
    print(f"investigate: status={result['status']} reason={result['failure_reason']} "
          f"cost=${result['cost_usd']:.2f} denials={len(result.get('tool_denials', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
