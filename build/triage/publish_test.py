import difflib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from testsupport import AGENT_OK, APP, CONFIGMAP, REPO_FILES, make_patch, make_policy, write_repo
from triage import leakscan, publish
from triage.incident import Incident, sweep_finding_fingerprint
from triage.result import failure, success

INC = Incident("alertmanager", "KubePodCrashLooping", "demo", "Deployment/demo")
LIVE = {"classes": {"oom-memory-limit": "live"}}
POLICY_PATH = "clusters/vollminlab-cluster/kyverno/kyverno-policies/policies/p.yaml"
HEAD_FILES = dict(REPO_FILES, **{POLICY_PATH: "kind: ClusterPolicy\nmetadata: {name: p}\n"})
CM = APP + "configmap.yaml"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git binary (CI's test image has none)")


class FakeGitHub:
    def __init__(self, existing=None):
        self.calls, self.existing = [], existing or {}

    def create_issue(self, repo, title, body, labels):
        self.calls.append(("create_issue", title, body, labels))
        return {"number": 11, "html_url": "https://github.com/vollminlab/triage-reports/issues/11"}

    def comment(self, repo, n, body):
        self.calls.append(("comment", n, body))

    def add_labels(self, repo, n, labels):
        self.calls.append(("add_labels", n, labels))

    def create_pull(self, repo, title, head, base, body):
        self.calls.append(("create_pull", repo, title, head, base, body))
        return {"html_url": "https://github.com/vollminlab/k8s-vollminlab-cluster/pull/1"}

    def find_open_issue(self, repo, fp12):
        return self.existing.get(fp12)

    def kinds(self):
        return [c[0] for c in self.calls]

    def issue(self):
        return next(c for c in self.calls if c[0] == "create_issue")


def _git_args(cmd):
    """The subcommand and its arguments, with the global -c/-C options removed."""
    args, i = [], 1
    while i < len(cmd):
        if cmd[i] in ("-c", "-C"):
            i += 2
            continue
        args = cmd[i:]
        break
    # `git -C repo -c user.name=x commit ...`: -c may also follow -C.
    while args and args[0] == "-c":
        args = args[2:]
    return args


class Runner:
    """No git binary needed: answers publish's git calls from an in-memory HEAD
    (HEAD_FILES) and the working tree under `repo`. gitleaks reports nothing;
    kustomize, kubeconform and kyverno pass. Every call is recorded."""

    def __init__(self, repo):
        self.repo, self.calls = repo, []
        self.head = {p: t.encode() for p, t in HEAD_FILES.items()}
        self.parent = None

    def _status(self) -> bytes:
        out, seen = [], set()
        for p in sorted(self.repo.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(self.repo))
                seen.add(rel)
                if rel not in self.head:
                    out.append(f"?? {rel}")
                elif p.read_bytes() != self.head[rel]:
                    out.append(f" M {rel}")
        out += [f" D {rel}" for rel in sorted(set(self.head) - seen)]
        return "".join(e + "\0" for e in out).encode()

    def _numstat(self) -> bytes:
        out = []
        for path in sorted(self.head):
            old = self.parent.get(path, b"").decode().split("\n")
            new = self.head[path].decode().split("\n")
            if old == new:
                continue
            d = list(difflib.ndiff(old, new))
            out.append(f"{sum(l[0] == '+' for l in d)}\t{sum(l[0] == '-' for l in d)}\t{path}\0")
        return "".join(out).encode()

    def git(self, args):
        sub = args[0]
        if sub == "cat-file":
            rev, path = args[2].split(":", 1)
            tree = self.head if rev == "HEAD" else {}
            return (0, tree[path]) if path in tree else (128, b"")
        if sub == "ls-tree":
            d = args[-1]
            return 0, "".join(p + "\0" for p in sorted(self.head) if p.startswith(d)).encode()
        if sub == "log":
            return 0, b""
        if sub == "status":
            return 0, self._status()
        if sub == "commit":
            self.parent = dict(self.head)
            self.head.update(self.staged)
            return 0, b""
        if sub == "add":
            self.staged = {p: (self.repo / p).read_bytes() for p in args[args.index("--") + 1:]}
            return 0, b""
        if sub == "diff":
            return 0, self._numstat()
        return 0, b""                                  # checkout, push

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if cmd[0] == "git":
            rc, out = self.git(_git_args(cmd))
            return subprocess.CompletedProcess(cmd, rc, out, b"")
        if cmd[0] == "gitleaks":
            Path(cmd[cmd.index("--report-path") + 1]).write_text("[]")
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def repo(tmp_path):
    return write_repo(tmp_path / "repo", HEAD_FILES)


def go(repo, result, policy=None, existing=None, runner=None):
    gh, runner, notes = FakeGitHub(), runner or Runner(repo), []
    out = publish.publish_incident(INC, result, existing, policy or make_policy(), gh, "ghs_SECRET_TOKEN",
                                   lambda t, m, url=None: notes.append((t, m, url)), repo, runner)
    return out, gh, runner, notes


def pushes(runner):
    return [c for c in runner.calls if c[0][0] == "git" and "push" in c[0]]


# --- publish logic: stub git, runs in CI -------------------------------------------

def test_shadow_mode_previews_the_pr_and_never_opens_it(repo):
    out, gh, runner, notes = go(repo, success(AGENT_OK, 0.5, []))
    assert gh.kinds() == ["create_issue"]
    _, title, body, labels = gh.issue()
    assert title == "[KubePodCrashLooping] demo/Deployment/demo"
    assert {"shadow", f"fp/{INC.fp12}", "class/oom-memory-limit"} <= set(labels)
    assert f"fix/triage-oom-memory-limit-{INC.fp12}" in body and "(shadow mode)" in body
    assert "raise memory limit above reported peak" in body
    assert out == {"outcome": "issue_created", "cost_usd": 0.5, "issue": 11, "pr": None}
    assert len(notes) == 1 and notes[0][2].endswith("/issues/11")
    assert not pushes(runner)


def test_live_mode_opens_the_pr_and_keeps_the_token_out_of_argv(repo):
    out, gh, runner, _ = go(repo, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    assert out["pr"].endswith("/pull/1")
    _, repo_name, title, head, base, body = next(c for c in gh.calls if c[0] == "create_pull")
    assert (repo_name, base) == ("vollminlab/k8s-vollminlab-cluster", "main")
    assert title == "fix(demo): raise memory limit above reported peak (triage)"
    assert AGENT_OK["summary"] not in body and AGENT_OK["root_cause"] not in body
    [push] = pushes(runner)
    assert push[1]["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert "https://github.com/vollminlab/k8s-vollminlab-cluster.git" in push[0]   # never `origin`
    assert all("ghs_SECRET_TOKEN" not in " ".join(c[0]) for c in runner.calls)
    assert "memory: 400Mi" in (repo / CM).read_text()
    assert "pr-opened" in gh.issue()[3]


def test_only_the_push_carries_the_token(repo):
    _, _, runner, _ = go(repo, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    for cmd, kw in runner.calls:
        if cmd[0] == "git" and "push" not in cmd:
            assert "GIT_CONFIG_VALUE_0" not in (kw.get("env") or {}), cmd


def test_every_git_call_disables_hooks(repo):
    _, _, runner, _ = go(repo, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    gits = [c for c, _ in runner.calls if c[0] == "git"]
    assert gits
    for cmd in gits:
        assert cmd[1:3] == ["-c", "core.hooksPath=/dev/null"], cmd


def test_git_output_is_read_as_bytes_so_newlines_are_not_translated(repo):
    _, _, runner, _ = go(repo, success(AGENT_OK, 0.5, []))
    for cmd, kw in runner.calls:
        if cmd[0] == "git":
            assert not kw.get("text") and not kw.get("universal_newlines"), cmd


def test_low_confidence_blocks_the_pr_even_in_live_mode(repo):
    out, gh, _, _ = go(repo, success(dict(AGENT_OK, confidence="medium"), 0.5, []), policy=make_policy(**LIVE))
    assert "create_pull" not in gh.kinds()
    body = gh.issue()[2]
    assert "✗ confidence is medium" in body and "(blocked by a gate)" in body


def test_agent_failure_files_an_agent_error_issue(repo):
    out, gh, _, notes = go(repo, failure("max_turns", "hit 40", 2.9))
    assert (out["outcome"], out["cost_usd"]) == ("agent_error", 2.9)
    _, _, body, labels = gh.issue()
    assert "agent-error" in labels and "## PR gates" not in body
    assert "max_turns" in notes[0][1]


def test_reinvestigation_comments_instead_of_opening_and_does_not_notify(repo):
    out, gh, _, notes = go(repo, success(AGENT_OK, 0.5, []), existing=7)
    assert gh.kinds() == ["comment", "add_labels"]
    assert out["outcome"] == "commented" and notes == []


def test_secret_in_the_patch_blocks_the_pr(repo, monkeypatch):
    real = leakscan.scan
    monkeypatch.setattr(leakscan, "scan", lambda texts, run: {"patch": ["x"]} if "patch" in texts else real(texts, run))
    _, gh, runner, _ = go(repo, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    assert "create_pull" not in gh.kinds() and not pushes(runner)
    assert "leak scan found a secret" in gh.issue()[2]


def test_secret_in_the_issue_is_redacted(repo, monkeypatch):
    monkeypatch.setattr(leakscan, "scan", lambda texts, run: {"text": ["hunter2"]} if "text" in texts else {})
    _, gh, _, _ = go(repo, success(dict(AGENT_OK, summary="password=hunter2 in env"), 0.5, []))
    _, _, body, labels = gh.issue()
    assert "hunter2" not in body and "[REDACTED]" in body and "redacted" in labels


def test_dirty_checkout_refuses_the_pr_but_files_the_issue(repo):
    (repo / APP / "notes.txt").write_text("left behind by the investigation\n")
    _, gh, runner, _ = go(repo, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    assert "create_pull" not in gh.kinds() and not pushes(runner)
    body = gh.issue()[2]
    assert "✗ checkout was modified by the investigation; refusing to build a PR from it" in body
    assert "memory: 256Mi" in (repo / CM).read_text()           # nothing was written


def test_a_failed_guard_never_writes_its_files(repo):
    bad = dict(AGENT_OK, metrics={"peak_memory_bytes": 390 * 2**20})     # 400Mi < 1.2x peak
    _, gh, _, _ = go(repo, success(bad, 0.5, []), policy=make_policy(**LIVE))
    assert "✗ diff guard" in gh.issue()[2]
    assert (repo / CM).read_text() == CONFIGMAP


def test_validation_failure_blocks_the_pr(repo):
    class Failing(Runner):
        def __call__(self, cmd, **kw):
            if cmd[0] == "kubeconform":
                self.calls.append((cmd, kw))
                return subprocess.CompletedProcess(cmd, 1, "bad schema", "")
            return super().__call__(cmd, **kw)
    _, gh, runner, _ = go(repo, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE), runner=Failing(repo))
    assert "create_pull" not in gh.kinds() and not pushes(runner)
    assert "✗ validation: kubeconform failed" in gh.issue()[2]


def test_validation_that_leaves_a_file_behind_blocks_the_pr(repo):
    class Littering(Runner):
        def __call__(self, cmd, **kw):
            if cmd[0] == "kyverno":
                (self.repo / "stray.yaml").write_text("x: 1\n")
            return super().__call__(cmd, **kw)
    _, gh, runner, _ = go(repo, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE), runner=Littering(repo))
    assert "create_pull" not in gh.kinds() and not pushes(runner)
    assert "other than the guarded files" in gh.issue()[2]


def test_sweep_files_new_findings_and_skips_recent_ones():
    f1 = {"check": "unprotected-pvc", "namespace": "a", "object": "PersistentVolumeClaim/x",
          "summary": "s", "detail": "d"}
    f2 = dict(f1, object="PersistentVolumeClaim/y")
    fp2 = sweep_finding_fingerprint("unprotected-pvc", "a", "PersistentVolumeClaim/y")[:12]
    gh = FakeGitHub(existing={fp2: {"number": 3, "updated_at": "2026-10-11T06:00:00Z"}})
    notes = []
    result = success(dict(AGENT_OK, **{"class": "none", "patch": None, "app_dir": None, "findings": [f1, f2]}), 0.8, [])
    out = publish.publish_sweep(Incident("sweep", "nightly-sweep", "", ""), result, make_policy(), gh,
                                lambda t, m, url=None: notes.append(t), Runner(Path("/nonexistent")),
                                datetime(2026, 10, 11, 8, tzinfo=timezone.utc))
    assert gh.kinds() == ["create_issue"]
    assert out == {"outcome": "sweep_ok", "cost_usd": 0.8, "findings": 2, "new": 1}
    assert notes == ["Triage sweep"]


def test_main_always_writes_a_termination_message(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "TERMINATION_LOG", tmp_path / "termination-log")
    monkeypatch.setattr(publish, "WORK", tmp_path)
    monkeypatch.delenv("TRIAGE_INCIDENT", raising=False)
    assert publish.main() == 0
    assert json.loads((tmp_path / "termination-log").read_text())["outcome"] == "publish_failed"


def test_main_reports_an_estimated_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "TERMINATION_LOG", tmp_path / "termination-log")
    monkeypatch.setattr(publish, "WORK", tmp_path)
    monkeypatch.setattr(publish, "load_policy", lambda: make_policy())
    monkeypatch.setenv("TRIAGE_INCIDENT", INC.to_json())
    (tmp_path / "result.json").write_text(json.dumps(failure("timeout", "killed", 3.0, cost_estimated=True)))
    assert publish.main() == 0                 # fails later (no Pushover secret), still exits 0
    summary = json.loads((tmp_path / "termination-log").read_text())
    assert summary["outcome"] == "publish_failed"
    assert (summary["cost_usd"], summary["cost_estimated"]) == (3.0, True)


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -float("inf")])
def test_termination_text_never_emits_nan_or_infinity(cost):
    # Python's json.dumps would write the bare tokens NaN/Infinity, which are not JSON.
    text = publish.termination_text({"outcome": "issue_created", "cost_usd": cost, "detail": "x"})
    parsed = json.loads(text, parse_constant=lambda c: pytest.fail(f"non-JSON constant {c}"))
    assert parsed["outcome"] == "issue_created" and parsed["cost_usd"] is None


def test_termination_text_is_valid_json_within_the_kubelet_limit():
    text = publish.termination_text({"outcome": "x", "cost_usd": 1.0, "detail": "é" * 5000, "pr": "y" * 5000})
    assert len(text.encode()) <= 3500
    assert json.loads(text)["outcome"] == "x" and json.loads(text)["cost_usd"] == 1.0


# --- real git: skipped where there is no git binary (CI's test image) ----------------

class RealGit:
    """git goes to the real binary, except push; every other tool is stubbed."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if cmd[0] == "git":
            if "push" in cmd:
                return subprocess.CompletedProcess(cmd, 0, b"", b"")
            # Isolated from this host's ~/.gitconfig, as the image is.
            env = dict(kw.pop("env", None) or os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
            return subprocess.run(cmd, env=env, **kw)
        if cmd[0] == "gitleaks":
            Path(cmd[cmd.index("--report-path") + 1]).write_bytes(b"[]")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _sh(repo, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def real_repo(root: Path, files=HEAD_FILES) -> Path:
    root.mkdir(parents=True)
    for path, text in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
    _sh(root, "init", "-q", "-b", "main")
    _sh(root, "config", "core.autocrlf", "false")
    _sh(root, "add", "--", *files)
    _sh(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return root


def real_go(repo, result, policy=None):
    gh, runner = FakeGitHub(), RealGit()
    out = publish.publish_incident(INC, result, None, policy or make_policy(**LIVE), gh, "ghs_SECRET_TOKEN",
                                   lambda *a, **k: None, repo, runner)
    return out, gh, runner


@needs_git
def test_real_git_live_mode_commits_exactly_the_guarded_change(tmp_path):
    repo = real_repo(tmp_path / "repo")
    out, gh, runner = real_go(repo, success(AGENT_OK, 0.5, []))
    assert out["pr"].endswith("/pull/1"), gh.issue()[2]
    files = _sh(repo, "diff", "--name-only", "HEAD~1", "HEAD").stdout.decode().split()
    assert files == [CM]
    assert _sh(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip() == f"fix/triage-oom-memory-limit-{INC.fp12}"
    assert len(pushes(runner)) == 1


@needs_git
def test_real_git_dirty_checkout_refuses_the_pr(tmp_path):
    repo = real_repo(tmp_path / "repo")
    (repo / "leftover.txt").write_text("x")          # untracked
    _, gh, runner = real_go(repo, success(AGENT_OK, 0.5, []))
    assert "create_pull" not in gh.kinds() and not pushes(runner)
    assert "✗ checkout was modified by the investigation" in gh.issue()[2]


@needs_git
def test_real_git_ignored_file_also_counts_as_dirty(tmp_path):
    repo = real_repo(tmp_path / "repo")
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("*.cache\n")
    (repo / "x.cache").write_text("x")
    _, gh, runner = real_go(repo, success(AGENT_OK, 0.5, []))
    assert not pushes(runner)
    assert "✗ checkout was modified by the investigation" in gh.issue()[2]


@needs_git
def test_real_git_baseline_is_read_from_head_not_the_working_tree(tmp_path):
    repo = real_repo(tmp_path / "repo")
    # The investigation rewrote the file so the patch would apply to it and not to HEAD
    # (and planted an untracked manifest claiming the namespace). The guard must judge
    # the patch against HEAD; the tree being dirty still blocks the PR.
    tampered = CONFIGMAP.replace("memory: 256Mi", "memory: 64Mi")
    (repo / CM).write_text(tampered)
    (repo / APP / "planted.yaml").write_text("metadata: {namespace: demo}\n")
    agent = dict(AGENT_OK, patch=make_patch(CM, tampered, tampered.replace("memory: 64Mi", "memory: 400Mi")))
    _, gh, runner = real_go(repo, success(agent, 0.5, []))
    body = gh.issue()[2]
    assert "✗ diff guard: the patch does not apply" in body
    assert (repo / CM).read_text() == tampered          # never overwritten
    assert publish.read_head(repo, CM, runner) == CONFIGMAP
    assert publish.read_head(repo, APP + "planted.yaml", runner) is None
    assert APP + "planted.yaml" not in publish.list_head(repo, APP, runner)


@needs_git
def test_real_git_crlf_file_keeps_its_crlf_bytes(tmp_path):
    crlf = CONFIGMAP.replace("\n", "\r\n")
    repo = real_repo(tmp_path / "repo", dict(HEAD_FILES, **{CM: crlf}))
    raised = crlf.replace("memory: 256Mi", "memory: 400Mi")
    agent = dict(AGENT_OK, patch=make_patch(CM, crlf, raised))
    out, gh, _ = real_go(repo, success(agent, 0.5, []))
    assert out["pr"], gh.issue()[2]
    assert (repo / CM).read_bytes() == raised.encode()
    assert _sh(repo, "cat-file", "blob", f"HEAD:{CM}").stdout == raised.encode()


@needs_git
def test_real_git_hooks_never_run(tmp_path):
    repo = real_repo(tmp_path / "repo")
    marker = tmp_path / "hook-ran"
    hooks = repo / ".git" / "hooks"
    for name in ("pre-commit", "commit-msg", "post-commit", "post-checkout", "pre-push", "reference-transaction"):
        (hooks / name).write_text(f"#!/bin/sh\necho {name} >> '{marker}'\n")
        (hooks / name).chmod(0o755)
    _sh(repo, "hook", "run", "pre-commit")             # the planted hooks really are live
    assert marker.read_text() == "pre-commit\n"
    marker.unlink()
    out, gh, _ = real_go(repo, success(AGENT_OK, 0.5, []))
    assert out["pr"], gh.issue()[2]
    assert not marker.exists()


def _clean_filter(repo, command):
    # A filter defined in .git/config runs on `git add`: the committed blob is then
    # not the file the gates checked. Nothing shows in `git status`.
    _sh(repo, "config", "filter.evil.clean", command)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("*.yaml filter=evil\n")


@needs_git
def test_real_git_numstat_mismatch_blocks_the_push(tmp_path):
    repo = real_repo(tmp_path / "repo")
    _clean_filter(repo, "awk '{print} /400Mi/ {print \"    injected: true\"}'")   # +1 line
    _, gh, runner = real_go(repo, success(AGENT_OK, 0.5, []))
    assert not pushes(runner) and "create_pull" not in gh.kinds()
    assert "does not match the guarded change (numstat" in gh.issue()[2]


@needs_git
def test_real_git_same_line_count_but_different_bytes_blocks_the_push(tmp_path):
    repo = real_repo(tmp_path / "repo")
    _clean_filter(repo, "sed s/400Mi/999Mi/")
    _, gh, runner = real_go(repo, success(AGENT_OK, 0.5, []))
    assert not pushes(runner) and "create_pull" not in gh.kinds()
    assert "differs from what the gates checked" in gh.issue()[2]


@needs_git
def test_real_git_file_history_is_newest_first_and_byte_exact(tmp_path):
    repo = real_repo(tmp_path / "repo")
    for v in ("1.5.0\r", "1.6.0"):
        (repo / CM).write_bytes(CONFIGMAP.replace("tag: 1.4.0", f"tag: {v}").encode())
        _sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", v)
    (repo / "gone.yaml").write_text("a: 1\n")
    _sh(repo, "add", "gone.yaml")
    _sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "add")
    _sh(repo, "rm", "-q", "gone.yaml")
    _sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "rm")
    texts = publish.file_history(repo, CM, RealGit())
    assert [("1.6.0" in t, "1.5.0\r\n" in t, "1.4.0" in t) for t in texts] == [
        (True, False, False), (False, True, False), (False, False, True)]
    assert publish.file_history(repo, "gone.yaml", RealGit()) == ["a: 1\n"]    # deleting revision skipped
