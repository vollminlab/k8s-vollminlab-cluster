import difflib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from testsupport import AGENT_OK, APP, CONFIGMAP, REPO_FILES, make_patch, make_policy
from triage import diffguard, leakscan, publish
from triage.incident import Incident, sweep_finding_fingerprint
from triage.result import failure, success

INC = Incident("alertmanager", "KubePodCrashLooping", "demo", "Deployment/demo")
LIVE = {"classes": {"oom-memory-limit": "live"}}
POLICY_PATH = "clusters/vollminlab-cluster/kyverno/kyverno-policies/policies/p.yaml"
HEAD_FILES = dict(REPO_FILES, **{POLICY_PATH: "kind: ClusterPolicy\nmetadata: {name: p}\n"})
CM = APP + "configmap.yaml"
RUN = "1791700000"
GITHUB_URL = "https://github.com/vollminlab/k8s-vollminlab-cluster.git"
MAIN_SHA = "a" * 40

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git binary (CI's test image has none)")


class FakeGitHub:
    def __init__(self, existing=None, log=None):
        self.calls, self.existing, self.log = [], existing or {}, log if log is not None else []

    def create_issue(self, repo, title, body, labels):
        self.calls.append(("create_issue", title, body, labels))
        self.log.append("create_issue")
        return {"number": 11, "html_url": "https://github.com/vollminlab/triage-reports/issues/11"}

    def comment(self, repo, n, body):
        self.calls.append(("comment", n, body))
        self.log.append("comment")

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

    def report(self):
        """The gate-report comment posted after the gates ran."""
        return next(c[2] for c in self.calls if c[0] == "comment" and "gate-report" in c[2])

    def labels(self):
        return [l for c in self.calls if c[0] in ("create_issue", "add_labels") for l in c[-1]]


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
    """No git binary needed: `git clone` writes HEAD_FILES (plus `plant`) into the
    destination, and the other git calls are answered from an in-memory HEAD and the
    working tree. gitleaks reports nothing; kustomize, kubeconform and kyverno pass.
    Every call is recorded, with its keyword arguments (env, timeout)."""

    def __init__(self, workdir, plant=None, log=None):
        self.workdir, self.repo, self.calls = workdir, workdir / "repo", []
        self.head = {p: t.encode() for p, t in HEAD_FILES.items()}
        self.parent, self.plant, self.log = None, plant or {}, log if log is not None else []
        self.head_parent_sha, self.ahead = MAIN_SHA, "1"

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
        if sub == "clone":
            dest = Path(args[-1])
            for path, text in dict(HEAD_FILES, **self.plant).items():
                p = dest / path
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
            return 0, b""
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
        if sub == "rev-parse":
            spec = args[-1]
            if spec.startswith("HEAD~1"):
                return (0, (self.head_parent_sha + "\n").encode()) if self.parent is not None else (1, b"")
            return 0, (MAIN_SHA + "\n").encode()
        if sub == "rev-list":
            return 0, (self.ahead + "\n").encode()
        return 0, b""                                  # checkout, push

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        self.log.append(cmd[0] if cmd[0] != "git" else f"git {_git_args(cmd)[0]}")
        if cmd[0] == "git":
            rc, out = self.git(_git_args(cmd))
            return subprocess.CompletedProcess(cmd, rc, out, b"")
        if cmd[0] == "gitleaks":
            Path(cmd[cmd.index("--report-path") + 1]).write_text("[]")
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def workdir(tmp_path):
    d = tmp_path / "publish"
    d.mkdir()
    return d


def go(workdir, result, policy=None, existing=None, runner=None, notify=None, cluster_gh=None, **kw):
    gh, runner, notes = FakeGitHub(), runner or Runner(workdir), []
    out = publish.publish_incident(INC, result, existing, policy or make_policy(), gh, "ghs_SECRET_TOKEN",
                                   notify or (lambda t, m, url=None: notes.append((t, m, url))), workdir, runner,
                                   run_id=RUN, cluster_gh=cluster_gh or gh, **kw)
    return out, gh, runner, notes


def pushes(runner):
    return [c for c in runner.calls if c[0][0] == "git" and "push" in c[0]]


def git_calls(runner):
    return [(c, kw) for c, kw in runner.calls if c[0] == "git"]


# --- publish logic: stub git, runs in CI -------------------------------------------

def test_shadow_mode_files_the_issue_then_reports_the_preview(workdir):
    out, gh, runner, notes = go(workdir, success(AGENT_OK, 0.5, []))
    assert gh.kinds() == ["create_issue", "comment", "add_labels"]
    _, title, body, labels = gh.issue()
    assert title == "[KubePodCrashLooping] demo/Deployment/demo"
    assert {f"fp/{INC.fp12}", "class/oom-memory-limit"} <= set(labels)
    assert AGENT_OK["summary"] in body and publish.GATES_PENDING in body
    report = gh.report()
    assert f"fix/triage-oom-memory-limit-{INC.fp12}-{RUN}" in report and "(shadow mode)" in report
    assert "raise memory limit above reported peak" in report
    assert "shadow" in gh.labels()
    assert out == {"outcome": "issue_created", "cost_usd": 0.5, "issue": 11, "pr": None}
    assert len(notes) == 1 and notes[0][2].endswith("/issues/11")
    assert not pushes(runner)


def test_live_mode_opens_the_pr_and_keeps_the_token_out_of_argv(workdir):
    out, gh, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    assert out["pr"].endswith("/pull/1")
    _, repo_name, title, head, base, body = next(c for c in gh.calls if c[0] == "create_pull")
    assert (repo_name, base, head) == ("vollminlab/k8s-vollminlab-cluster", "main",
                                       f"fix/triage-oom-memory-limit-{INC.fp12}-{RUN}")
    assert title == "fix(demo): raise memory limit above reported peak (triage)"
    assert AGENT_OK["summary"] not in body and AGENT_OK["root_cause"] not in body
    [push] = pushes(runner)
    assert push[1]["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert GITHUB_URL in push[0]                                        # never `origin`
    assert all("ghs_SECRET_TOKEN" not in " ".join(c[0]) for c in runner.calls)
    assert "memory: 400Mi" in (workdir / "repo" / CM).read_text()
    assert "pr-opened" in gh.labels()
    assert "**PR opened:**" in gh.report()


def test_the_issue_is_filed_before_any_gate_runs(workdir):
    log = []
    runner = Runner(workdir, log=log)
    gh = FakeGitHub(log=log)
    publish.publish_incident(INC, success(AGENT_OK, 0.5, []), None, make_policy(**LIVE), gh, "t",
                             lambda *a, **k: None, workdir, runner, run_id=RUN, cluster_gh=gh)
    first_git = next(i for i, e in enumerate(log) if e.startswith("git "))
    assert log.index("create_issue") < first_git
    assert log[-1] == "comment"                                      # the report comes last


# --- containment: a fresh clone in publish's own directory -------------------------

def test_publish_clones_main_fresh_from_github_into_its_own_directory(workdir):
    _, _, runner, _ = go(workdir, success(AGENT_OK, 0.5, []))
    clone, kw = git_calls(runner)[0]
    args = _git_args(clone)
    assert args[0] == "clone" and "--filter=blob:none" in args
    assert args[-2:] == [GITHUB_URL, str(workdir / "repo")]
    assert ["--branch", "main"] == args[args.index("--branch"):args.index("--branch") + 2]
    for cmd, _ in runner.calls:                  # nothing under investigate's /work
        assert not any(str(a).startswith("/work") for a in cmd), cmd


def test_publish_refuses_a_checkout_that_already_exists(workdir):
    (workdir / "repo").mkdir()
    _, gh, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    assert "already exists" in gh.report() and not pushes(runner)
    assert not [c for c, _ in git_calls(runner) if _git_args(c)[0] == "clone"]


def test_a_failed_clone_is_a_gate_line_not_a_lost_issue(workdir):
    class NoClone(Runner):
        def git(self, args):
            return (128, b"") if args[0] == "clone" else super().git(args)
    _, gh, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE), runner=NoClone(workdir))
    assert "✗ could not make publish's own clone" in gh.report() and not pushes(runner)


def test_every_subprocess_gets_only_the_clean_environment(workdir, monkeypatch):
    for leaky in ("LEAKY_SENTINEL", "GIT_DIR", "GIT_SSH_COMMAND", "PYTHONPATH", "HTTPS_PROXY", "LD_PRELOAD"):
        monkeypatch.setenv(leaky, "x")
    _, _, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    want = publish.clean_env(workdir / "home")
    assert want["HOME"] == str(workdir / "home")
    kinds = set()
    for cmd, kw in runner.calls:
        kinds.add(cmd[0])
        env = dict(kw["env"])
        if "push" in cmd:
            for k in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
                env.pop(k)
        assert env == want, cmd
    assert kinds == {"git", "kustomize", "kubeconform", "kyverno", "gitleaks"}
    assert want["GIT_CONFIG_NOSYSTEM"] == "1" and want["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert want["GIT_TERMINAL_PROMPT"] == "0" and set(want) >= {"PATH", "LANG"}


def test_only_the_push_carries_the_token(workdir):
    _, _, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    for cmd, kw in runner.calls:
        if "push" not in cmd:
            assert "GIT_CONFIG_VALUE_0" not in kw["env"], cmd


def test_every_git_call_disables_hooks_and_the_file_and_ext_transports(workdir):
    _, _, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    gits = [c for c, _ in git_calls(runner)]
    assert gits
    for cmd in gits:
        assert cmd[1:3] == ["-c", "core.hooksPath=/dev/null"], cmd
        assert "protocol.file.allow=never" in cmd and "protocol.ext.allow=never" in cmd, cmd
        assert "protocol.file.allow=always" not in cmd, cmd          # the test hook is off


def test_git_output_is_read_as_bytes_so_newlines_are_not_translated(workdir):
    _, _, runner, _ = go(workdir, success(AGENT_OK, 0.5, []))
    for cmd, kw in git_calls(runner):
        assert not kw.get("text") and not kw.get("universal_newlines"), cmd


def test_head_parent_other_than_origin_main_blocks_the_push(workdir):
    runner = Runner(workdir)
    runner.head_parent_sha = "b" * 40
    _, gh, _, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE), runner=runner)
    assert not pushes(runner) and "create_pull" not in gh.kinds()
    assert "not exactly one commit on origin/main" in gh.report()


def test_more_than_one_commit_ahead_blocks_the_push(workdir):
    runner = Runner(workdir)
    runner.ahead = "2"
    _, gh, _, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE), runner=runner)
    assert not pushes(runner) and "not exactly one commit on origin/main" in gh.report()


# --- the deadline, exceptions, and notify ------------------------------------------

def test_subprocess_timeouts_are_cut_to_the_time_left(workdir):
    now = [1000.0]
    _, _, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE),
                         deadline=1050.0, clock=lambda: now[0])
    assert runner.calls
    for cmd, kw in runner.calls:
        assert 0 < kw["timeout"] <= 50, cmd


def test_an_exhausted_deadline_still_files_the_issue_and_a_report(workdir):
    out, gh, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE),
                            deadline=0.0, clock=lambda: 1.0)
    assert runner.calls == []                       # nothing was started past the deadline
    assert out["outcome"] == "issue_created" and not pushes(runner)
    assert "leakscan-failed" in gh.issue()[3]       # the scan could not run either
    assert "time budget ran out" in gh.report()


def test_a_gate_exception_still_yields_the_issue_and_a_report(workdir, monkeypatch):
    def boom(*a, **kw):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(diffguard, "check", boom)
    out, gh, runner, notes = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    assert out["outcome"] == "issue_created" and out["pr"] is None
    assert gh.kinds()[0] == "create_issue"
    assert "✗ publish stopped while running the gates, so no PR: RecursionError" in gh.report()
    assert not pushes(runner) and len(notes) == 1


def test_a_validation_timeout_still_yields_the_issue(workdir):
    class Hangs(Runner):
        def __call__(self, cmd, **kw):
            if cmd[0] == "kubeconform":
                raise subprocess.TimeoutExpired(cmd, kw["timeout"])
            return super().__call__(cmd, **kw)
    out, gh, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE), runner=Hangs(workdir))
    assert out["outcome"] == "issue_created" and not pushes(runner)
    assert "✗ validation: kubeconform" in gh.report()


def test_a_leak_scan_timeout_in_the_gates_still_yields_the_issue(workdir, monkeypatch):
    real = leakscan.scan

    def scan(texts, run):
        if "patch" in texts:
            raise subprocess.TimeoutExpired(["gitleaks"], 1)
        return real(texts, run)
    monkeypatch.setattr(leakscan, "scan", scan)
    out, gh, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    assert out["outcome"] == "issue_created" and not pushes(runner)
    assert "✗ publish stopped while running the gates, so no PR: TimeoutExpired" in gh.report()
    assert "would have been opened" in gh.report()          # the preview survives the exception


def test_notify_failure_keeps_the_outcome(workdir):
    def broken(*a, **kw):
        raise ConnectionError("pushover down")
    out, gh, _, _ = go(workdir, success(AGENT_OK, 0.5, []), notify=broken)
    assert out["outcome"] == "issue_created" and out["issue"] == 11
    assert "notify failed: ConnectionError" in out["detail"]


def test_a_failed_report_comment_keeps_the_outcome(workdir):
    class NoComment(FakeGitHub):
        def comment(self, repo, n, body):
            raise ConnectionError("github down")
    gh = NoComment()
    out = publish.publish_incident(INC, success(AGENT_OK, 0.5, []), None, make_policy(), gh, "t",
                                   lambda *a, **k: None, workdir, Runner(workdir), run_id=RUN, cluster_gh=gh)
    assert out["outcome"] == "issue_created" and "gate report failed" in out["detail"]


# --- the gates themselves -----------------------------------------------------------

def test_low_confidence_blocks_the_pr_even_in_live_mode(workdir):
    out, gh, _, _ = go(workdir, success(dict(AGENT_OK, confidence="medium"), 0.5, []), policy=make_policy(**LIVE))
    assert "create_pull" not in gh.kinds()
    report = gh.report()
    assert "✗ confidence is medium" in report and "(blocked by a gate)" in report


def test_agent_failure_files_an_agent_error_issue_and_runs_no_gate(workdir):
    out, gh, runner, notes = go(workdir, failure("max_turns", "hit 40", 2.9))
    assert (out["outcome"], out["cost_usd"]) == ("agent_error", 2.9)
    assert gh.kinds() == ["create_issue"]
    _, _, body, labels = gh.issue()
    assert "agent-error" in labels and "## PR gates" not in body
    assert "max_turns" in notes[0][1]
    assert not git_calls(runner)


def test_class_none_runs_no_gate_and_posts_no_report(workdir):
    none = dict(AGENT_OK, **{"class": "none", "patch": None, "app_dir": None})
    _, gh, runner, _ = go(workdir, success(none, 0.5, []))
    assert gh.kinds() == ["create_issue"] and not git_calls(runner)
    assert publish.GATES_PENDING not in gh.issue()[2]


def test_reinvestigation_comments_instead_of_opening_and_does_not_notify(workdir):
    out, gh, _, notes = go(workdir, success(AGENT_OK, 0.5, []), existing=7)
    assert gh.kinds() == ["comment", "add_labels", "comment", "add_labels"]
    assert all(c[1] == 7 for c in gh.calls)
    assert out["outcome"] == "commented" and notes == []


def test_secret_in_the_patch_blocks_the_pr(workdir, monkeypatch):
    real = leakscan.scan
    monkeypatch.setattr(leakscan, "scan", lambda texts, run: {"patch": ["x"]} if "patch" in texts else real(texts, run))
    _, gh, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE))
    assert "create_pull" not in gh.kinds() and not pushes(runner)
    assert "leak scan found a secret" in gh.report()


def test_secret_in_the_issue_is_redacted(workdir, monkeypatch):
    monkeypatch.setattr(leakscan, "scan", lambda texts, run: {"text": ["hunter2"]} if "text" in texts else {})
    _, gh, _, _ = go(workdir, success(dict(AGENT_OK, summary="password=hunter2 in env"), 0.5, []))
    _, _, body, labels = gh.issue()
    assert "hunter2" not in body and "[REDACTED]" in body and "redacted" in labels


def test_dirty_checkout_refuses_the_pr_but_files_the_issue(workdir):
    runner = Runner(workdir, plant={APP + "notes.txt": "appeared from nowhere\n"})
    _, gh, _, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE), runner=runner)
    assert "create_pull" not in gh.kinds() and not pushes(runner)
    assert publish.DIRTY in gh.report()
    assert "memory: 256Mi" in (workdir / "repo" / CM).read_text()       # nothing was written


def test_a_failed_guard_never_writes_its_files(workdir):
    bad = dict(AGENT_OK, metrics={"peak_memory_bytes": 390 * 2**20})     # 400Mi < 1.2x peak
    _, gh, _, _ = go(workdir, success(bad, 0.5, []), policy=make_policy(**LIVE))
    assert "✗ diff guard" in gh.report()
    assert (workdir / "repo" / CM).read_text() == CONFIGMAP


def test_validation_failure_blocks_the_pr(workdir):
    class Failing(Runner):
        def __call__(self, cmd, **kw):
            if cmd[0] == "kubeconform":
                self.calls.append((cmd, kw))
                return subprocess.CompletedProcess(cmd, 1, "bad schema", "")
            return super().__call__(cmd, **kw)
    _, gh, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE), runner=Failing(workdir))
    assert "create_pull" not in gh.kinds() and not pushes(runner)
    assert "✗ validation: kubeconform failed" in gh.report()


def test_validation_that_leaves_a_file_behind_blocks_the_pr(workdir):
    class Littering(Runner):
        def __call__(self, cmd, **kw):
            if cmd[0] == "kyverno":
                (self.repo / "stray.yaml").write_text("x: 1\n")
            return super().__call__(cmd, **kw)
    _, gh, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE), runner=Littering(workdir))
    assert "create_pull" not in gh.kinds() and not pushes(runner)
    assert "other than the guarded files" in gh.report()


def test_sweep_files_new_findings_and_skips_recent_ones(workdir):
    f1 = {"check": "unprotected-pvc", "namespace": "a", "object": "PersistentVolumeClaim/x",
          "summary": "s", "detail": "d"}
    f2 = dict(f1, object="PersistentVolumeClaim/y")
    fp2 = sweep_finding_fingerprint("unprotected-pvc", "a", "PersistentVolumeClaim/y")[:12]
    gh = FakeGitHub(existing={fp2: {"number": 3, "updated_at": "2026-10-11T06:00:00Z"}})
    notes = []
    runner = Runner(workdir)
    result = success(dict(AGENT_OK, **{"class": "none", "patch": None, "app_dir": None, "findings": [f1, f2]}), 0.8, [])
    out = publish.publish_sweep(Incident("sweep", "nightly-sweep", "", ""), result, make_policy(), gh,
                                lambda t, m, url=None: notes.append(t), runner,
                                datetime(2026, 10, 11, 8, tzinfo=timezone.utc), workdir=workdir)
    assert gh.kinds() == ["create_issue"]
    assert out == {"outcome": "sweep_ok", "cost_usd": 0.8, "findings": 2, "new": 1, "capped": 0}
    assert notes == ["Triage sweep"]
    assert all(kw["env"] == publish.clean_env(workdir / "home") for _, kw in runner.calls)


def test_sweep_files_at_most_ten_new_issues_and_counts_the_rest_as_capped(workdir):
    assert publish.MAX_NEW_SWEEP_ISSUES == 10
    findings = [{"check": "unprotected-pvc", "namespace": "a", "object": f"PersistentVolumeClaim/p{i}",
                 "summary": "s", "detail": "d"} for i in range(13)]
    stale = dict(findings[0], object="PersistentVolumeClaim/old")
    fp_old = sweep_finding_fingerprint("unprotected-pvc", "a", "PersistentVolumeClaim/old")[:12]
    gh = FakeGitHub(existing={fp_old: {"number": 3, "updated_at": "2026-10-01T06:00:00Z"}})
    notes = []
    result = success(dict(AGENT_OK, **{"class": "none", "patch": None, "app_dir": None,
                                       "findings": findings + [stale]}), 0.8, [])
    out = publish.publish_sweep(Incident("sweep", "nightly-sweep", "", ""), result, make_policy(), gh,
                                lambda t, m, url=None: notes.append(m), Runner(workdir),
                                datetime(2026, 10, 11, 8, tzinfo=timezone.utc), workdir=workdir)
    assert gh.kinds().count("create_issue") == 10
    assert gh.kinds().count("comment") == 1               # an existing issue is not a new one
    assert out == {"outcome": "sweep_ok", "cost_usd": 0.8, "findings": 14, "new": 10, "capped": 3}
    assert len(notes) == 1 and "10 new finding(s)" in notes[0]
    assert "3 more new finding(s) not filed" in notes[0].splitlines()[-1]


def test_sweep_under_the_cap_reports_zero_capped(workdir):
    f1 = {"check": "unprotected-pvc", "namespace": "a", "object": "PersistentVolumeClaim/x",
          "summary": "s", "detail": "d"}
    notes = []
    result = success(dict(AGENT_OK, **{"class": "none", "patch": None, "app_dir": None, "findings": [f1]}), 0.8, [])
    out = publish.publish_sweep(Incident("sweep", "nightly-sweep", "", ""), result, make_policy(), FakeGitHub(),
                                lambda t, m, url=None: notes.append(m), Runner(workdir),
                                datetime(2026, 10, 11, 8, tzinfo=timezone.utc), workdir=workdir)
    assert out["capped"] == 0 and "not filed" not in notes[0]


def test_sweep_notify_failure_keeps_the_outcome(workdir):
    f1 = {"check": "unprotected-pvc", "namespace": "a", "object": "PersistentVolumeClaim/x",
          "summary": "s", "detail": "d"}
    result = success(dict(AGENT_OK, **{"class": "none", "patch": None, "app_dir": None, "findings": [f1]}), 0.8, [])

    def broken(*a, **k):
        raise ConnectionError("down")
    out = publish.publish_sweep(Incident("sweep", "nightly-sweep", "", ""), result, make_policy(), FakeGitHub(),
                                broken, Runner(workdir), datetime(2026, 10, 11, 8, tzinfo=timezone.utc),
                                workdir=workdir)
    assert out["outcome"] == "sweep_ok"


# --- GitHub's 65,536-character body limit ----------------------------------------------

GITHUB_BODY_LIMIT = 65536


def test_fit_truncates_with_a_marker_and_leaves_short_text_alone():
    assert publish.MAX_BODY_CHARS == 60000
    assert publish._fit("short") == "short"
    text = "a" * 100000
    out = publish._fit(text)
    assert len(out) <= publish.MAX_BODY_CHARS
    omitted = len(text) - out.index("\n\n[truncated by triage:")
    assert out.endswith(f"\n\n[truncated by triage: {omitted} characters omitted]")
    assert publish._fit("b" * publish.MAX_BODY_CHARS) == "b" * publish.MAX_BODY_CHARS


def _max_size_result(tmp_path):
    """The largest record load_result accepts: every agent field at its schema maximum,
    and the rest of the 1 MiB filled with denied tool calls."""
    from triage.result import MAX_RESULT_BYTES, load_result
    agent = dict(AGENT_OK, **{"summary": "s" * 2000, "root_cause": "r" * 8000,
                              "evidence": ["e" * 2000] * 30, "class": "none", "patch": None, "app_dir": None})
    rec = success(agent, 0.5, [])
    room = MAX_RESULT_BYTES - len(json.dumps(rec)) - 1000
    rec["tool_denials"] = ["Bash: " + "d" * 594] * (room // 604)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(rec))
    assert MAX_RESULT_BYTES - 2000 < path.stat().st_size <= MAX_RESULT_BYTES
    loaded = load_result(path)
    assert loaded["status"] == "ok"
    return loaded


def test_a_max_size_result_still_files_exactly_one_issue_within_githubs_limit(workdir, tmp_path):
    out, gh, _, _ = go(workdir, _max_size_result(tmp_path))
    assert gh.kinds().count("create_issue") == 1 and out["outcome"] == "issue_created"
    body = gh.issue()[2]
    assert len(body) <= publish.MAX_BODY_CHARS < GITHUB_BODY_LIMIT
    assert "[truncated by triage:" in body


def test_a_max_size_result_comment_on_an_existing_issue_is_truncated(workdir, tmp_path):
    _, gh, _, _ = go(workdir, _max_size_result(tmp_path), existing=7)
    comments = [c[2] for c in gh.calls if c[0] == "comment"]
    assert len(comments) == 1 and len(comments[0]) <= publish.MAX_BODY_CHARS


def test_truncation_happens_after_redaction(workdir, monkeypatch, tmp_path):
    # A secret past the cut must still be redacted, and the redaction cannot push the
    # body back over the limit.
    monkeypatch.setattr(leakscan, "scan", lambda texts, run: {"text": ["hunter2"]} if "text" in texts else {})
    result = _max_size_result(tmp_path)
    result["root_cause"] = "hunter2 " * 1000
    _, gh, _, _ = go(workdir, result)
    body = gh.issue()[2]
    assert "hunter2" not in body and len(body) <= publish.MAX_BODY_CHARS


def test_an_unscanned_body_keeps_its_warning_when_truncated(workdir, monkeypatch, tmp_path):
    def broken(texts, run):
        raise leakscan.LeakScanError("gitleaks missing")
    monkeypatch.setattr(leakscan, "scan", broken)
    _, gh, _, _ = go(workdir, _max_size_result(tmp_path))
    body = gh.issue()[2]
    assert len(body) <= publish.MAX_BODY_CHARS and body.endswith("this text was posted unscanned.")
    assert "leakscan-failed" in gh.labels()


def test_the_gate_report_comment_is_truncated(workdir, monkeypatch):
    monkeypatch.setattr(publish.issuebody, "render_gate_report", lambda *a, **k: "g" * 200000)
    _, gh, _, _ = go(workdir, success(AGENT_OK, 0.5, []))
    comments = [c[2] for c in gh.calls if c[0] == "comment"]
    assert comments and all(len(c) <= publish.MAX_BODY_CHARS for c in comments)


def test_sweep_bodies_are_truncated(workdir, monkeypatch):
    monkeypatch.setattr(publish.issuebody, "render_finding", lambda f, fp: ("t", "f" * 200000))
    f1 = {"check": "unprotected-pvc", "namespace": "a", "object": "PersistentVolumeClaim/x",
          "summary": "s", "detail": "d"}
    result = success(dict(AGENT_OK, **{"class": "none", "patch": None, "app_dir": None, "findings": [f1]}), 0.8, [])
    gh = FakeGitHub()
    publish.publish_sweep(Incident("sweep", "nightly-sweep", "", ""), result, make_policy(), gh,
                          lambda *a, **k: None, Runner(workdir), datetime(2026, 10, 11, 8, tzinfo=timezone.utc),
                          workdir=workdir)
    assert len(gh.issue()[2]) <= publish.MAX_BODY_CHARS


# --- main ---------------------------------------------------------------------------

def test_main_always_writes_a_termination_message(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "TERMINATION_LOG", tmp_path / "termination-log")
    monkeypatch.setattr(publish, "RESULT", tmp_path / "result.json")
    monkeypatch.setattr(publish, "PUBLISH", tmp_path / "publish")
    monkeypatch.delenv("TRIAGE_INCIDENT", raising=False)
    assert publish.main() == 0
    assert json.loads((tmp_path / "termination-log").read_text())["outcome"] == "publish_failed"


def test_main_reports_an_estimated_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "TERMINATION_LOG", tmp_path / "termination-log")
    monkeypatch.setattr(publish, "RESULT", tmp_path / "result.json")
    monkeypatch.setattr(publish, "PUBLISH", tmp_path / "publish")
    monkeypatch.setattr(publish, "load_policy", lambda: make_policy())
    monkeypatch.setenv("TRIAGE_INCIDENT", INC.to_json())
    (tmp_path / "result.json").write_text(json.dumps(failure("timeout", "killed", 3.0, cost_estimated=True)))
    assert publish.main() == 0                 # fails later (no Pushover secret), still exits 0
    summary = json.loads((tmp_path / "termination-log").read_text())
    assert summary["outcome"] == "publish_failed"
    assert (summary["cost_usd"], summary["cost_estimated"]) == (3.0, True)


def test_main_refuses_to_run_with_the_test_remote_hook_set(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "TERMINATION_LOG", tmp_path / "termination-log")
    monkeypatch.setattr(publish, "_TEST_REMOTE", tmp_path)
    monkeypatch.setattr(publish, "load_policy", lambda: pytest.fail("must refuse before anything else"))
    assert publish.main() == 0
    summary = json.loads((tmp_path / "termination-log").read_text())
    assert summary["outcome"] == "publish_failed" and "test-only remote" in summary["detail"]


def test_issues_use_the_reports_client_and_the_pr_uses_the_cluster_client(workdir):
    cluster = FakeGitHub()
    out, reports, runner, _ = go(workdir, success(AGENT_OK, 0.5, []), policy=make_policy(**LIVE), cluster_gh=cluster)
    assert out["pr"] and cluster.kinds() == ["create_pull"]
    assert "create_pull" not in reports.kinds() and "create_issue" in reports.kinds()
    assert pushes(runner)


ISSUES_PERMS = {"issues": "write", "metadata": "read"}
CLUSTER_PERMS = {"contents": "write", "pull_requests": "write", "metadata": "read"}


def _main_with_fakes(tmp_path, monkeypatch, inc, result):
    minted, clients = [], []

    class FakeApp:
        def token(self, repositories, permissions):
            minted.append((list(repositories), dict(permissions)))
            return f"tok-{len(minted)}"

    class RecordingGitHub(FakeGitHub):
        def __init__(self, provider):
            super().__init__()
            self.token = provider()
            clients.append(self)

    monkeypatch.setattr(publish, "TERMINATION_LOG", tmp_path / "termination-log")
    monkeypatch.setattr(publish, "RESULT", tmp_path / "result.json")
    monkeypatch.setattr(publish, "PUBLISH", tmp_path / "publish")
    (tmp_path / "publish").mkdir()
    monkeypatch.setattr(publish, "load_policy", lambda: make_policy())
    monkeypatch.setattr(publish.GitHubApp, "from_dir", classmethod(lambda cls, *a, **k: FakeApp()))
    monkeypatch.setattr(publish.Pushover, "from_dir", classmethod(
        lambda cls, *a, **k: type("P", (), {"send": lambda self, *a, **k: None})()))
    monkeypatch.setattr(publish, "GitHub", RecordingGitHub)
    monkeypatch.setenv("TRIAGE_INCIDENT", inc.to_json())
    monkeypatch.delenv("TRIAGE_EXISTING_ISSUE", raising=False)
    (tmp_path / "result.json").write_text(json.dumps(result))
    assert publish.main() == 0
    return minted, clients, json.loads((tmp_path / "termination-log").read_text())


def test_main_mints_one_token_per_repository_with_only_its_permissions(tmp_path, monkeypatch):
    minted, clients, summary = _main_with_fakes(tmp_path, monkeypatch, INC, failure("timeout", "killed", 1.0))
    assert summary["outcome"] == "agent_error"
    assert minted == [(["triage-reports"], ISSUES_PERMS), (["k8s-vollminlab-cluster"], CLUSTER_PERMS)]
    for repos, _ in minted:
        assert len(repos) == 1
    issue_clients = [c for c in clients if "create_issue" in c.kinds()]
    assert [c.token for c in issue_clients] == ["tok-1"]           # the issues-only token


def test_a_sweep_mints_only_the_issues_token(tmp_path, monkeypatch):
    result = success(dict(AGENT_OK, **{"class": "none", "patch": None, "app_dir": None, "findings": []}), 0.2, [])
    minted, _, summary = _main_with_fakes(tmp_path, monkeypatch, Incident("sweep", "nightly-sweep", "", ""), result)
    assert summary["outcome"] == "sweep_ok"
    assert minted == [(["triage-reports"], ISSUES_PERMS)]


def test_publish_reads_result_json_from_the_result_volume_only():
    assert publish.RESULT == Path("/result/result.json")
    assert not publish.PUBLISH.is_relative_to(publish.RESULT.parent)
    assert not hasattr(publish, "WORK")


def test_deadline_comes_from_the_environment_or_the_budget():
    assert publish.deadline_from({"TRIAGE_DEADLINE_EPOCH": "2000"}, 1000.0) == 2000 - publish.API_RESERVE_SECONDS
    for bad in ({}, {"TRIAGE_DEADLINE_EPOCH": "soon"}, {"TRIAGE_DEADLINE_EPOCH": "-5"}):
        assert publish.deadline_from(bad, 1000.0) == 1000.0 + publish.PUBLISH_BUDGET_SECONDS


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -float("inf")])
def test_termination_text_never_emits_nan_or_infinity(cost):
    # Python's json.dumps would write the bare tokens NaN/Infinity, which are not JSON.
    text = publish.termination_text({"outcome": "issue_created", "cost_usd": cost, "detail": "x"})
    parsed = json.loads(text, parse_constant=lambda c: pytest.fail(f"non-JSON constant {c}"))
    assert parsed["outcome"] == "issue_created" and parsed["cost_usd"] is None


def test_run_id_comes_from_triage_run_or_the_clock():
    assert publish.run_id_from({"TRIAGE_RUN": "1791700000"}) == "1791700000"
    for bad in ({}, {"TRIAGE_RUN": ""}, {"TRIAGE_RUN": "../x"}, {"TRIAGE_RUN": "x" * 40}):
        assert publish.run_id_from(bad, clock=lambda: 1234.9) == "1234"


def test_termination_text_is_valid_json_within_the_kubelet_limit():
    text = publish.termination_text({"outcome": "x", "cost_usd": 1.0, "detail": "é" * 5000, "pr": "y" * 5000})
    assert len(text.encode()) <= 3500
    assert json.loads(text)["outcome"] == "x" and json.loads(text)["cost_usd"] == 1.0


# --- real git: skipped where there is no git binary (CI's test image) ----------------
#
# A local bare repository stands in for GitHub through publish._TEST_REMOTE, the only
# way the file transport is ever allowed. Pushes to it are real.

ISOLATED = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


class RealGit:
    """git goes to the real binary; every other tool is stubbed. `after_clone(repo)`
    runs once publish's clone exists, to tamper with it as some unknown compromise
    might, so the in-clone protections stay tested."""

    def __init__(self, after_clone=None, on_commit=None):
        self.calls, self.after_clone, self.on_commit = [], after_clone, on_commit

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, dict(kw)))
        if cmd[0] == "git":
            if "push" in cmd and not any(str(a).startswith("file://") for a in cmd):
                return subprocess.CompletedProcess(cmd, 0, b"", b"")
            args = _git_args(cmd)
            if args and args[0] == "commit" and self.on_commit:
                self.on_commit(Path(cmd[cmd.index("-C") + 1]))
            # publish passes its own clean env; direct helper calls get an isolated one.
            env = kw.pop("env", None) or dict(os.environ, **ISOLATED)
            cp = subprocess.run(cmd, env=env, **kw)
            if args and args[0] == "clone" and cp.returncode == 0 and self.after_clone:
                self.after_clone(Path(args[-1]))
            return cp
        if cmd[0] == "gitleaks":
            Path(cmd[cmd.index("--report-path") + 1]).write_bytes(b"[]")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _sh(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          env=dict(os.environ, **ISOLATED))


def _commit(repo, *args):
    return _sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", *args)


def real_repo(root: Path, files=HEAD_FILES) -> Path:
    root.mkdir(parents=True)
    for path, text in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
    _sh(root, "init", "-q", "-b", "main")
    _sh(root, "config", "core.autocrlf", "false")
    _sh(root, "add", "--", *files)
    _commit(root, "-q", "-m", "init")
    return root


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A bare 'GitHub' holding HEAD_FILES on main; publish clones from and pushes to it."""
    def make(files=HEAD_FILES, history=()):
        src = real_repo(tmp_path / "src", files)
        for path, text in history:
            (src / path).write_bytes(text.encode())
            _commit(src, "-qam", "history")
        bare = tmp_path / "github.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True, capture_output=True,
                       env=dict(os.environ, **ISOLATED))
        monkeypatch.setattr(publish, "_TEST_REMOTE", bare)
        return bare
    return make


def real_go(tmp_path, result, policy=None, runner=None):
    gh, runner = FakeGitHub(), runner or RealGit()
    workdir = tmp_path / "publish"
    workdir.mkdir(exist_ok=True)
    out = publish.publish_incident(INC, result, None, policy or make_policy(**LIVE), gh, "ghs_SECRET_TOKEN",
                                   lambda *a, **k: None, workdir, runner, run_id=RUN, cluster_gh=gh)
    return out, gh, runner, workdir / "repo"


BRANCH = f"fix/triage-oom-memory-limit-{INC.fp12}-{RUN}"


@needs_git
def test_real_git_live_mode_pushes_exactly_one_commit_on_main(tmp_path, remote):
    bare = remote()
    out, gh, runner, repo = real_go(tmp_path, success(AGENT_OK, 0.5, []))
    assert out["pr"].endswith("/pull/1"), gh.report()
    assert _sh(bare, "rev-list", "--count", f"main..{BRANCH}").stdout.decode().strip() == "1"
    assert _sh(bare, "diff", "--name-only", "main", BRANCH).stdout.decode().split() == [CM]
    assert _sh(bare, "cat-file", "blob", f"{BRANCH}:{CM}").stdout.decode() == \
        CONFIGMAP.replace("memory: 256Mi", "memory: 400Mi")
    assert len(pushes(runner)) == 1


def _evil_script(d: Path, marker: Path) -> Path:
    # A script, not an inline command: `;` starts a comment in a .gitconfig file.
    p = d / "evil.sh"
    p.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    p.chmod(0o755)
    return p


def _investigate_checkout(tmp_path, bare, marker):
    """investigate's /work as an injected model could leave it: a planted commit and
    ref, and a .git/config whose filter, insteadOf, sshCommand and fsmonitor would all
    run or redirect something if publish ever used this checkout."""
    work = tmp_path / "work"
    inv = work / "repo"
    subprocess.run(["git", "clone", "-q", str(bare), str(inv)], check=True, capture_output=True,
                   env=dict(os.environ, **ISOLATED))
    (inv / CM).write_text(CONFIGMAP.replace("tag: 1.4.0", "tag: 6.6.6-planted"))
    _commit(inv, "-qam", "planted by the investigation")
    _sh(inv, "update-ref", "refs/remotes/origin/main", "HEAD")
    cmd = f"sh -c 'touch {marker}; cat'"
    for key, value in (("filter.evil.clean", cmd), ("filter.evil.smudge", cmd), ("core.fsmonitor", cmd),
                       ("core.sshCommand", cmd), ("url.file:///nonexistent/.insteadOf", "https://github.com/"),
                       ("http.proxy", "http://127.0.0.1:9"), ("credential.helper", f"!{cmd}")):
        _sh(inv, "config", key, value)
    (inv / ".git" / "info").mkdir(exist_ok=True)
    (inv / ".git" / "info" / "attributes").write_text("* filter=evil\n")
    (work / "home").mkdir()
    script = _evil_script(work / "home", marker)
    (work / "home" / ".gitconfig").write_text(f"[filter \"evil\"]\n\tclean = {script}\n[core]\n\tfsmonitor = {script}\n")
    return work


@needs_git
def test_real_git_a_poisoned_investigate_checkout_has_no_effect(tmp_path, remote):
    bare = remote()
    marker = tmp_path / "PWNED"
    work = _investigate_checkout(tmp_path, bare, marker)
    out, gh, runner, repo = real_go(tmp_path, success(AGENT_OK, 0.5, []))
    assert out["pr"], gh.report()
    assert not marker.exists()
    planted = _sh(work / "repo", "rev-parse", "HEAD").stdout.decode().strip()
    assert _sh(bare, "rev-list", "--count", f"main..{BRANCH}").stdout.decode().strip() == "1"
    assert planted not in _sh(bare, "rev-list", BRANCH).stdout.decode()
    assert "6.6.6-planted" not in _sh(bare, "cat-file", "blob", f"{BRANCH}:{CM}").stdout.decode()
    for cmd, _ in runner.calls:
        assert not any(str(a).startswith(str(work)) for a in cmd), cmd


@needs_git
def test_real_git_publish_home_gitconfig_is_ignored(tmp_path, remote):
    # GIT_CONFIG_GLOBAL=/dev/null: even a .gitconfig in publish's OWN home never loads.
    remote()
    marker = tmp_path / "PWNED"
    home = tmp_path / "publish" / "home"
    home.mkdir(parents=True)
    script = _evil_script(tmp_path, marker)
    (home / ".gitconfig").write_text(
        f"[core]\n\tattributesFile = {home}/attrs\n\tfsmonitor = {script}\n"
        f"[filter \"evil\"]\n\tclean = {script}\n")
    (home / "attrs").write_text("* filter=evil\n")
    # The planted config really is live for a git that reads the global config.
    probe = tmp_path / "probe"
    probe.mkdir()
    subprocess.run(["git", "init", "-q", str(probe)], check=True, env={"HOME": str(home), "PATH": os.environ["PATH"]})
    (probe / "f").write_text("x")
    subprocess.run(["git", "-C", str(probe), "add", "f"], check=True, env={"HOME": str(home), "PATH": os.environ["PATH"]})
    assert marker.exists()
    marker.unlink()
    out, gh, _, _ = real_go(tmp_path, success(AGENT_OK, 0.5, []))
    assert out["pr"], gh.report()
    assert not marker.exists()


@needs_git
def test_real_git_head_parent_not_origin_main_blocks_the_push(tmp_path, remote):
    bare = remote()

    def sneak_in_a_commit(repo):            # runs just before publish's own commit
        (repo / "sneaky.txt").write_text("x")
        _sh(repo, "add", "sneaky.txt")
        _commit(repo, "-qm", "sneaky", "--", "sneaky.txt")       # leaves the staged change staged
    out, gh, runner, _ = real_go(tmp_path, success(AGENT_OK, 0.5, []), runner=RealGit(on_commit=sneak_in_a_commit))
    assert out["pr"] is None and not pushes(runner)
    assert "not exactly one commit on origin/main" in gh.report(), gh.report().split("## The PR")[0]
    assert BRANCH.encode() not in _sh(bare, "branch", "--list").stdout


def test_a_planted_pth_in_a_home_is_not_loaded_under_python_isolation(tmp_path):
    # investigate's HOME is on its own volume, so publish cannot even see it. If it
    # could, `python -I` (the Job's command) would still never load its user site.
    base = getattr(sys, "_base_executable", None) or sys.executable
    home = tmp_path / "work" / "home"
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    usersite = subprocess.run([base, "-c", "import site; print(site.getusersitepackages())"], env=env,
                              capture_output=True, text=True, check=True).stdout.strip()
    assert usersite.startswith(str(home))
    Path(usersite).mkdir(parents=True)
    marker = tmp_path / "PWNED"
    (Path(usersite) / "evil.pth").write_text(f"import pathlib; pathlib.Path({str(marker)!r}).touch()\n")
    probe = [base, "-c", "pass"]
    subprocess.run(probe, env=env, check=True)
    if not marker.exists():
        pytest.skip("this interpreter never enables the user site (e.g. a venv base), so the control fails")
    marker.unlink()
    subprocess.run([base, "-I", *probe[1:]], env=env, check=True)
    assert not marker.exists()


@needs_git
def test_real_git_dirty_clone_refuses_the_pr(tmp_path, remote):
    remote()
    runner = RealGit(after_clone=lambda repo: (repo / "leftover.txt").write_text("x"))
    _, gh, runner, _ = real_go(tmp_path, success(AGENT_OK, 0.5, []), runner=runner)
    assert "create_pull" not in gh.kinds() and not pushes(runner)
    assert publish.DIRTY in gh.report()


@needs_git
def test_real_git_ignored_file_also_counts_as_dirty(tmp_path, remote):
    remote()

    def plant(repo):
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "exclude").write_text("*.cache\n")
        (repo / "x.cache").write_text("x")
    _, gh, runner, _ = real_go(tmp_path, success(AGENT_OK, 0.5, []), runner=RealGit(after_clone=plant))
    assert not pushes(runner)
    assert publish.DIRTY in gh.report()


@needs_git
def test_real_git_baseline_is_read_from_head_not_the_working_tree(tmp_path, remote):
    remote()
    # The working tree is rewritten so the patch would apply to it and not to HEAD
    # (and an untracked manifest claims the namespace). The guard must judge the patch
    # against HEAD; the tree being dirty still blocks the PR.
    tampered = CONFIGMAP.replace("memory: 256Mi", "memory: 64Mi")

    def plant(repo):
        (repo / CM).write_text(tampered)
        (repo / APP / "planted.yaml").write_text("metadata: {namespace: demo}\n")
    agent = dict(AGENT_OK, patch=make_patch(CM, tampered, tampered.replace("memory: 64Mi", "memory: 400Mi")))
    _, gh, runner, repo = real_go(tmp_path, success(agent, 0.5, []), runner=RealGit(after_clone=plant))
    assert "✗ diff guard: the patch does not apply" in gh.report()
    assert (repo / CM).read_text() == tampered          # never overwritten
    assert publish.read_head(repo, CM, runner) == CONFIGMAP
    assert publish.read_head(repo, APP + "planted.yaml", runner) is None
    assert APP + "planted.yaml" not in publish.list_head(repo, APP, runner)


@needs_git
def test_real_git_crlf_file_keeps_its_crlf_bytes(tmp_path, remote):
    crlf = CONFIGMAP.replace("\n", "\r\n")
    bare = remote(dict(HEAD_FILES, **{CM: crlf}))
    raised = crlf.replace("memory: 256Mi", "memory: 400Mi")
    agent = dict(AGENT_OK, patch=make_patch(CM, crlf, raised))
    out, gh, _, repo = real_go(tmp_path, success(agent, 0.5, []))
    assert out["pr"], gh.report()
    assert (repo / CM).read_bytes() == raised.encode()
    assert _sh(bare, "cat-file", "blob", f"{BRANCH}:{CM}").stdout == raised.encode()


@needs_git
def test_real_git_hooks_never_run(tmp_path, remote):
    remote()
    marker = tmp_path / "hook-ran"

    def plant(repo):
        hooks = repo / ".git" / "hooks"
        for name in ("pre-commit", "commit-msg", "post-commit", "post-checkout", "pre-push", "reference-transaction"):
            (hooks / name).write_text(f"#!/bin/sh\necho {name} >> '{marker}'\n")
            (hooks / name).chmod(0o755)
        _sh(repo, "hook", "run", "pre-commit")             # the planted hooks really are live
        assert marker.read_text() == "pre-commit\n"
        marker.unlink()
    out, gh, _, _ = real_go(tmp_path, success(AGENT_OK, 0.5, []), runner=RealGit(after_clone=plant))
    assert out["pr"], gh.report()
    assert not marker.exists()


def _clean_filter(command):
    # A filter defined in .git/config runs on `git add`: the committed blob is then
    # not the file the gates checked. Nothing shows in `git status`.
    def plant(repo):
        _sh(repo, "config", "filter.evil.clean", command)
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text("*.yaml filter=evil\n")
    return plant


@needs_git
def test_real_git_numstat_mismatch_blocks_the_push(tmp_path, remote):
    remote()
    runner = RealGit(after_clone=_clean_filter("awk '{print} /400Mi/ {print \"    injected: true\"}'"))   # +1 line
    _, gh, runner, _ = real_go(tmp_path, success(AGENT_OK, 0.5, []), runner=runner)
    assert not pushes(runner) and "create_pull" not in gh.kinds()
    assert "does not match the guarded change (numstat" in gh.report()


@needs_git
def test_real_git_same_line_count_but_different_bytes_blocks_the_push(tmp_path, remote):
    remote()
    _, gh, runner, _ = real_go(tmp_path, success(AGENT_OK, 0.5, []), runner=RealGit(after_clone=_clean_filter("sed s/400Mi/999Mi/")))
    assert not pushes(runner) and "create_pull" not in gh.kinds()
    assert "differs from what the gates checked" in gh.report()


@needs_git
def test_real_git_file_history_is_newest_first_and_byte_exact(tmp_path):
    repo = real_repo(tmp_path / "repo")
    for v in ("1.5.0\r", "1.6.0"):
        (repo / CM).write_bytes(CONFIGMAP.replace("tag: 1.4.0", f"tag: {v}").encode())
        _commit(repo, "-qam", v)
    (repo / "gone.yaml").write_text("a: 1\n")
    _sh(repo, "add", "gone.yaml")
    _commit(repo, "-qm", "add")
    _sh(repo, "rm", "-q", "gone.yaml")
    _commit(repo, "-qm", "rm")
    texts = publish.file_history(repo, CM, RealGit())
    assert [("1.6.0" in t, "1.5.0\r\n" in t, "1.4.0" in t) for t in texts] == [
        (True, False, False), (False, True, False), (False, False, True)]
    assert publish.file_history(repo, "gone.yaml", RealGit()) == ["a: 1\n"]    # deleting revision skipped


@needs_git
def test_real_git_renovate_history_is_read_through_the_blobless_clone(tmp_path, remote):
    # publish's clone is blobless: each historic blob is fetched lazily. The guard must
    # still see the previous value and accept a one-step revert.
    remote(history=[(CM, CONFIGMAP.replace("tag: 1.4.0", "tag: 1.5.0"))])
    current = CONFIGMAP.replace("tag: 1.4.0", "tag: 1.5.0")
    agent = dict(AGENT_OK, **{"class": "renovate-breakage", "metrics": {},
                              "patch": make_patch(CM, current, current.replace("tag: 1.5.0", "tag: 1.4.0"))})
    out, gh, _, _ = real_go(tmp_path, success(agent, 0.5, []), policy=make_policy(classes={"renovate-breakage": "live"}))
    assert out["pr"], gh.report()
