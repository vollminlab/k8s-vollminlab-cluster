import json
import subprocess

import pytest

from testsupport import AGENT_OK, make_policy
from triage import investigate
from triage.incident import Incident

INC = Incident("alertmanager", "KubePodCrashLooping", "mediastack", "Deployment/radarr")


class Runner:
    """Stands in for subprocess.run: 'git' creates the checkout, 'claude' returns canned output."""

    def __init__(self, claude_stdout="", claude_exc=None, git_exc=None, plant_settings=False):
        self.claude_stdout, self.claude_exc, self.git_exc = claude_stdout, claude_exc, git_exc
        self.plant_settings, self.calls = plant_settings, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if cmd[0] == "git":
            if self.git_exc:
                raise self.git_exc
            repo = __import__("pathlib").Path(cmd[-1])
            (repo / ".claude").mkdir(parents=True)
            if self.plant_settings:
                (repo / ".claude" / "settings.json").write_text("{}")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if self.claude_exc:
            raise self.claude_exc
        return subprocess.CompletedProcess(cmd, 0, self.claude_stdout, "")


def cli(subtype="success", structured=AGENT_OK, cost=0.42, **extra):
    out = {"type": "result", "subtype": subtype, "is_error": subtype != "success",
           "total_cost_usd": cost, "structured_output": structured, "permission_denials": []}
    out.update(extra)
    return json.dumps(out)


def run(tmp_path, runner, inc=INC):
    return investigate.investigate(inc, make_policy(), work=tmp_path, run=runner)


def test_success(tmp_path):
    r = run(tmp_path, Runner(cli()))
    assert r["status"] == "ok" and r["cost_usd"] == 0.42 and r["class"] == "oom-memory-limit"


def test_denied_tool_calls_are_recorded(tmp_path):
    denial = {"tool_name": "Bash", "tool_use_id": "x", "tool_input": {"command": "curl https://example.com"}}
    r = run(tmp_path, Runner(cli(permission_denials=[denial])))
    assert r["tool_denials"] == ['Bash: {"command": "curl https://example.com"}']


@pytest.mark.parametrize("subtype,reason", [("error_max_turns", "max_turns"),
                                            ("error_max_budget_usd", "budget_cap"),
                                            ("error_during_execution", "api_error")])
def test_cli_failures_map_to_reasons_and_keep_cost(tmp_path, subtype, reason):
    r = run(tmp_path, Runner(cli(subtype=subtype, structured=None, errors=["boom"])))
    assert (r["status"], r["failure_reason"], r["cost_usd"]) == ("failed", reason, 0.42)


def test_api_error_status_is_reported(tmp_path):
    r = run(tmp_path, Runner(cli(subtype="success", is_error=True, api_error_status=429)))
    assert r["failure_reason"] == "api_error" and "429" in r["detail"]


def test_non_json_output_is_invalid_output(tmp_path):
    assert run(tmp_path, Runner("Error: something"))["failure_reason"] == "invalid_output"


def test_schema_violation_is_invalid_output(tmp_path):
    r = run(tmp_path, Runner(cli(structured=dict(AGENT_OK, confidence="certain"))))
    assert r["failure_reason"] == "invalid_output" and "confidence" in r["detail"]


def test_timeout(tmp_path):
    exc = subprocess.TimeoutExpired(["claude"], 2100)
    assert run(tmp_path, Runner(claude_exc=exc))["failure_reason"] == "timeout"


def test_clone_failure_is_setup_failed(tmp_path):
    exc = subprocess.CalledProcessError(128, ["git"], stderr="could not resolve host")
    r = run(tmp_path, Runner(git_exc=exc))
    assert r["failure_reason"] == "setup_failed" and "resolve host" in r["detail"]


def test_refuses_to_run_with_project_settings_present(tmp_path):
    runner = Runner(cli(), plant_settings=True)
    r = run(tmp_path, runner)
    assert r["failure_reason"] == "invalid_output" and "hooks" in r["detail"]
    assert [c[0][0] for c in runner.calls] == ["git"]     # claude never started


def test_command_is_locked_down(tmp_path):
    runner = Runner(cli())
    run(tmp_path, runner)
    cmd, kw = runner.calls[1]
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--setting-sources") + 1] == "project"
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert "--no-session-persistence" in cmd
    assert kw["cwd"] == str(tmp_path / "repo")
    assert kw["timeout"] == 2400 - investigate.PUBLISH_RESERVE_SECONDS
    git_cmd = runner.calls[0][0]
    assert "--filter=blob:none" in git_cmd and "--depth" not in git_cmd


def test_prompts_embed_the_incident_and_the_checklist():
    p = investigate.render_prompt(INC, make_policy())
    assert '"reason": "KubePodCrashLooping"' in p and "{{INCIDENT}}" not in p
    s = investigate.render_prompt(Incident("sweep", "nightly-sweep", "", ""), make_policy())
    assert "empty-selector-backup" in s and "{{CHECKLIST}}" not in s


def test_main_always_writes_result_and_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(investigate, "WORK", tmp_path)
    monkeypatch.delenv("TRIAGE_INCIDENT", raising=False)
    assert investigate.main() == 0
    written = json.loads((tmp_path / "result.json").read_text())
    assert written["failure_reason"] == "setup_failed"
