import json
from pathlib import Path

HERE = Path(__file__).parent
SETTINGS = json.loads((HERE / "managed-settings.json").read_text())
ALLOW = SETTINGS["permissions"]["allow"]
DENY = SETTINGS["permissions"]["deny"]
IN_CLUSTER = ("http://victoria-metrics-single-server.monitoring.svc:8428/",
              "http://victoria-metrics-lt-server.monitoring.svc:8428/",
              "http://loki.monitoring.svc:3100/")


def test_hooks_disabled_and_nothing_prompts():
    assert SETTINGS["disableAllHooks"] is True
    assert SETTINGS["permissions"]["defaultMode"] == "dontAsk"


def test_required_denies_are_present():
    for rule in ["WebFetch", "WebSearch", "Edit", "Write", "NotebookEdit", "Bash(helm *)",
                 "Bash(kubectl get secret*)", "Bash(kubectl describe secret*)", "Bash(kubectl exec *)",
                 "Bash(kubectl port-forward *)", "Read(//var/run/secrets/**)"]:
        assert rule in DENY, rule


def test_allow_list_is_read_only_and_narrow():
    for rule in ALLOW:
        assert rule.startswith("Bash(") and rule.endswith(")"), rule
        words = rule[5:-1].split()
        assert words[0] in ("kubectl", "flux", "git", "curl"), rule
        if words[0] == "kubectl":
            assert words[1] in ("get", "describe", "logs", "top", "events"), rule
        if words[0] == "flux":
            assert words[1] == "get", rule
        if words[0] == "git":
            assert words[1] in ("log", "show"), rule
        if words[0] == "curl":
            assert words[1] == "-s" and words[2].lstrip("'").startswith(IN_CLUSTER), rule


def test_nothing_that_reads_arbitrary_files_through_bash():
    # cat/grep/jq/head/tail would bypass the Read deny on /var/run/secrets.
    for rule in ALLOW:
        assert not rule.startswith(("Bash(cat", "Bash(grep", "Bash(jq", "Bash(head", "Bash(tail",
                                    "Bash(sh", "Bash(bash", "Bash(python", "Bash(env")), rule


def test_api_key_comes_from_the_mounted_file_not_the_environment():
    assert SETTINGS["apiKeyHelper"] == "/usr/local/bin/triage-api-key"
    helper = (HERE / "triage-api-key").read_text()
    assert "/var/run/secrets/triage/anthropic/api_key" in helper
    assert "ANTHROPIC_API_KEY" not in helper


def test_image_ships_no_project_settings():
    assert not (HERE / ".claude").exists()
