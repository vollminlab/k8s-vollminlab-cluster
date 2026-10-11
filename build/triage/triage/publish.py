"""Main container: result.json -> private issue, maybe a PR, and a notification.

There is no LLM in this process. It decides, with plain code, what reaches GitHub.
Contract: always exits 0 and always writes a one-line JSON summary to the
termination log, which the dispatcher reads for outcome and spend accounting.

Containment: the investigate container ran a possibly prompt-injected model, and
this one holds the GitHub App key. So publish reads exactly one thing investigate
wrote, /result/result.json (its own read-only volume, size-capped, parsed as data).
It clones the repo itself, fresh, into /publish, which investigate never mounts;
every subprocess gets a minimal environment built here (never os.environ), with git
system and global config off, hooks off and the file/ext transports refused; and
Python runs isolated (`python -I`), so no user site-packages or .pth file loads.

Inside that clone the old protections still hold, as defence in depth: the diff
guard reads the committed HEAD, a checkout with any modified, untracked or ignored
file never becomes a PR, the commit is compared byte for byte against what the
gates approved, and the branch must be exactly one commit on origin/main.
"""
import base64
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import diffguard, issuebody, leakscan, prbody, validate
from .ghapp import GitHub, GitHubApp
from .incident import Incident, sweep_finding_fingerprint
from .policy import load_policy
from .pushover import Pushover
from .result import load_result

RESULT = Path("/result/result.json")       # the only file read from investigate
PUBLISH = Path("/publish")                 # publish's own emptyDir: its clone and HOME
REPO_URL = "https://github.com/{repo}.git"
TERMINATION_LOG = Path("/dev/termination-log")
TERMINATION_MAX = 3500          # the kubelet keeps 4096 bytes
BOT_NAME = "vollminlab-triage[bot]"
BOT_EMAIL = "vollminlab-triage[bot]@users.noreply.github.com"
HISTORY_DEPTH = 50
CLONE_TIMEOUT = 180
# investigate leaves PUBLISH_RESERVE_SECONDS (300) of the Job deadline for this
# container. Subprocesses get this much of it, which leaves time for the GitHub API
# calls that post the report. TRIAGE_DEADLINE_EPOCH, if set, overrides it.
PUBLISH_BUDGET_SECONDS = 240
API_RESERVE_SECONDS = 60
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
DIRTY = "✗ publish's own checkout was not clean; refusing to build a PR from it"
# GitHub refuses an issue body or comment over 65,536 characters, and a refused
# create_issue would lose the diagnosis outright. Every body is cut to this, after
# redaction, leaving room for GitHub's own counting of what we send.
MAX_BODY_CHARS = 60000
GATES_PENDING = ("## PR gates\n\nRunning. The gate report follows as a comment on this issue; "
                 "if none arrives, publish did not finish.")

# On every git invocation, after the minimal environment (clean_env) has already
# turned off the system and global config. Hooks and fsmonitor are config-driven
# commands; autocrlf would rewrite the bytes the gates approved on `git add`; the
# file and ext transports would let a remote URL read local paths or run a command.
GIT_SAFETY = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false",
              "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never")

# TEST HOOK ONLY. A local bare repository standing in for GitHub, which also allows the
# file transport. Production never sets it; main() refuses to run if it is set.
_TEST_REMOTE = None


class DeadlineExceeded(subprocess.TimeoutExpired):
    """The publish budget ran out before this subprocess could start."""

    def __str__(self):
        return f"publish's time budget ran out before {self.cmd[0]!r} could run"


def clean_env(home: Path) -> dict:
    """The whole environment of every subprocess: nothing inherited from os.environ.
    The GitHub token is added to the push alone, by _git(extra_env=...)."""
    return {"PATH": SAFE_PATH, "HOME": str(home), "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}


class SafeRun:
    """subprocess.run bound to the clean environment and the publish deadline.

    validate.py and leakscan.py receive this as their `run`, so their tools get the
    same environment and their timeouts are cut to the time that is left."""

    def __init__(self, run, env: dict, deadline: float, clock=time.time):
        self.run, self.env, self.deadline, self.clock = run, dict(env), deadline, clock

    def __call__(self, cmd, *, extra_env=None, timeout=120, **kw):
        if "env" in kw:
            raise TypeError("SafeRun sets the environment itself; pass extra_env")
        left = self.deadline - self.clock()
        if left <= 0:
            raise DeadlineExceeded(cmd, 0)
        env = dict(self.env, **(extra_env or {}))
        return self.run(cmd, env=env, timeout=min(timeout, left), **kw)


def clone_url(policy) -> str:
    if _TEST_REMOTE is not None:
        return Path(_TEST_REMOTE).resolve().as_uri()
    return REPO_URL.format(repo=policy.cluster_repo)


@dataclass
class Gates:
    pr_allowed: bool = False
    shadow: bool = True
    lines: list = field(default_factory=list)
    guard: object = None
    repo: Path = None            # publish's own clone, once made
    pr_url: str = None
    preview: dict = None         # the PR that would have been opened
    note: str = ""


class CommitMismatch(Exception):
    """The commit is not exactly the guarded change; its message is the gate line."""


def _git(repo: Path, run, *args, extra_env=None, timeout=120):
    # Bytes, never text=True: universal-newline decoding would turn CRLF into LF.
    config = GIT_SAFETY + (("-c", "protocol.file.allow=always") if _TEST_REMOTE is not None else ())
    kw = {"capture_output": True, "timeout": timeout}
    if extra_env:
        kw["extra_env"] = extra_env
    return run(["git", *config, "-C", str(repo), *args], **kw)


def clone(workdir: Path, policy, run) -> Path:
    """A fresh, blobless clone of main into publish's own volume. Blobless, not
    shallow: renovate-breakage needs the file's history, fetched lazily per blob."""
    repo = workdir / "repo"
    if repo.exists() or repo.is_symlink():
        raise RuntimeError(f"{repo} already exists; publish clones only into an empty directory")
    cp = _git(workdir, run, "clone", "--filter=blob:none", "--single-branch", "--branch", "main", "--no-tags",
              "--quiet", "--", clone_url(policy), str(repo), timeout=CLONE_TIMEOUT)
    if cp.returncode != 0:
        raise RuntimeError(f"git clone exited {cp.returncode}: {cp.stderr.decode('utf-8', 'replace')[-300:]}")
    return repo


def read_head(repo: Path, path: str, run):
    """The file as committed at HEAD, byte-exact UTF-8; None if it is not a file there.

    `cat-file blob` rather than `show`: it refuses a tree (which `show` would list)
    and never applies a textconv driver."""
    cp = _git(repo, run, "cat-file", "blob", f"HEAD:{path}")
    if cp.returncode != 0:
        return None
    return cp.stdout.decode("utf-8")      # undecodable -> raises -> the guard fails closed


def list_head(repo: Path, d: str, run) -> list:
    cp = _git(repo, run, "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", d)
    if cp.returncode != 0:
        return []
    return [p for p in cp.stdout.decode("utf-8").split("\0") if p]


def file_history(repo: Path, path: str, run) -> list:
    """Texts of `path` at its last HISTORY_DEPTH revisions, newest first.

    A revision where the path does not exist (it was deleted there) is skipped. Any
    other unreadable revision ENDS the list: skipping it could skip the value the
    guard must compare against and expose an older one."""
    cp = _git(repo, run, "log", "--format=%H", "-n", str(HISTORY_DEPTH), "--", path)
    if cp.returncode != 0:
        return []
    texts = []
    for rev in cp.stdout.decode("ascii", "replace").split():
        show = _git(repo, run, "cat-file", "blob", f"{rev}:{path}")
        if show.returncode != 0:
            tree = _git(repo, run, "ls-tree", "-z", "--name-only", rev, "--", path)
            if tree.returncode == 0 and not tree.stdout:
                continue
            break
        try:
            texts.append(show.stdout.decode("utf-8"))
        except UnicodeDecodeError:
            break
    return texts


def checkout_status(repo: Path, run):
    """[(XY, path)] for every modified, staged, untracked or ignored path; None if
    git status could not run."""
    cp = _git(repo, run, "status", "--porcelain=v1", "-z", "--ignored", "--untracked-files=all")
    if cp.returncode != 0:
        return None
    tokens, out, i = cp.stdout.decode("utf-8", "replace").split("\0"), [], 0
    while i < len(tokens):
        t = tokens[i]
        i += 1
        if not t:
            continue
        out.append((t[:2], t[3:]))
        if t[0] in "RC":            # a rename or copy carries its source as the next field
            i += 1
    return out


def _write(repo: Path, path: str, text: str):
    p = repo / path
    if p.is_symlink() or not p.resolve().is_relative_to(repo.resolve()):
        raise OSError(f"{path} is not a regular file inside the checkout")
    # newline="": write exactly these characters; never translate \n.
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def gates_apply(inc, result) -> bool:
    return (result["status"] == "ok" and (result.get("class") or "none") != "none" and bool(result.get("patch"))
            and inc.source != "sweep")


def evaluate_gates(inc, result, policy, workdir: Path, run, g: Gates = None) -> Gates:
    """Gate lines accumulate on `g` as they are decided, so a caller that catches an
    exception from here still reports every gate that ran before it."""
    g = g if g is not None else Gates()
    cls = result.get("class", "none")
    if not gates_apply(inc, result):
        return g
    if cls not in policy.classes:
        g.lines.append(f"✗ class `{cls}` is not in triage-policy")
        return g
    g.shadow = not policy.is_live(cls)
    g.lines.append(f"✓ class `{cls}` is allowlisted ({policy.classes[cls]})")
    ok = True
    if result["confidence"] == "high":
        g.lines.append("✓ confidence is high")
    else:
        ok = False
        g.lines.append(f"✗ confidence is {result['confidence']}, not high")
    try:
        g.repo = repo = clone(workdir, policy, run)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        g.lines.append(f"✗ could not make publish's own clone of {policy.cluster_repo}: {str(e)[:300]}")
        return g
    # Before anything is written. The guard still runs on a dirty checkout, because
    # it reads only HEAD, so the private issue shows its verdict either way.
    clean = checkout_status(repo, run) == []
    if not clean:
        ok = False
        g.lines.append(DIRTY)
    g.guard = diffguard.check(
        result["patch"], cls, result.get("app_dir"), inc.namespace, result.get("metrics") or {},
        read_file=lambda p: read_head(repo, p, run), list_files=lambda d: list_head(repo, d, run),
        file_history=lambda p: file_history(repo, p, run), denylist=policy.renovate_denylist)
    if not g.guard.ok:
        g.pr_allowed = False
        g.lines.append(f"✗ diff guard: {g.guard.reason}")
        return g
    g.lines.append("✓ diff guard passed")
    if clean:
        try:
            for path, text in g.guard.new_files.items():
                _write(repo, path, text)
        except OSError as e:
            g.lines.append(f"✗ could not write the guarded files: {e}")
            return g
        err = validate.validate(repo, result["app_dir"], run)
        if err is None:
            g.lines.append("✓ kustomize build, kubeconform and kyverno apply passed")
            after = checkout_status(repo, run)
            want = sorted((" M", p) for p in g.guard.new_files)
            if after is None or sorted(after) != want:
                ok = False
                extra = sorted(p for _, p in (after or [])) if after is not None else "git status failed"
                g.lines.append(f"✗ after validation the checkout differs other than the guarded files: "
                               f"{str(extra)[:300]}")
        else:
            ok = False
            g.lines.append(f"✗ validation: {err[:1500]}")
    g.pr_allowed = ok
    return g


def _token_env(token: str) -> dict:
    # Through the environment (GIT_CONFIG_*), never argv: the token must not appear
    # in a process listing or in a CalledProcessError that quotes the command.
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"}


def _verify_commit(repo: Path, baseline: dict, new_files: dict, run):
    """The commit HEAD~1..HEAD must be exactly the guarded change: the same files,
    the same added/removed line counts, and byte-identical blobs."""
    cp = _git(repo, run, "diff", "--numstat", "-z", "--no-renames", "--no-ext-diff", "--no-textconv",
              "HEAD~1", "HEAD")
    if cp.returncode != 0:
        raise CommitMismatch("✗ could not read back the commit; not pushed")
    got = {}
    for entry in cp.stdout.decode("utf-8", "replace").split("\0"):
        if entry:
            added, removed, path = entry.split("\t", 2)
            got[path] = (added, removed)
    want = {}
    for path, new in new_files.items():
        edits = diffguard.line_edits(path, baseline[path], new)
        want[path] = (str(sum(len(a) for _, _, a in edits)), str(sum(len(r) for _, r, _ in edits)))
    if got != want:
        raise CommitMismatch(f"✗ the commit does not match the guarded change (numstat {str(got)[:300]}, "
                             f"expected {str(want)[:300]}); not pushed")
    for path, new in new_files.items():
        try:
            committed = read_head(repo, path, run)
        except UnicodeDecodeError:
            committed = None
        if committed != new:
            raise CommitMismatch(f"✗ the commit does not match the guarded change ({path} differs from "
                                 f"what the gates checked); not pushed")


def _verify_base(repo: Path, run):
    """HEAD must be exactly one commit on top of the clone's origin/main: nothing but
    the guarded commit may ride along to the public repo."""
    def rev(spec):
        cp = _git(repo, run, "rev-parse", "--verify", "--quiet", f"{spec}^{{commit}}")
        return cp.stdout.decode("ascii", "replace").strip() if cp.returncode == 0 else None

    parent, base = rev("HEAD~1"), rev("refs/remotes/origin/main")
    cp = _git(repo, run, "rev-list", "--count", "refs/remotes/origin/main..HEAD")
    count = cp.stdout.decode("ascii", "replace").strip() if cp.returncode == 0 else None
    if parent is None or base is None or parent != base or count != "1":
        raise CommitMismatch(f"✗ the PR branch is not exactly one commit on origin/main (HEAD~1 {parent}, "
                             f"origin/main {base}, {count} commit(s) ahead); not pushed")


def open_pr(repo: Path, gh, policy, draft: dict, new_files: dict, token: str, run) -> str:
    def git(*args, extra_env=None):
        cp = _git(repo, run, *args, extra_env=extra_env, timeout=180)
        if cp.returncode != 0:
            raise RuntimeError(f"git {args[0] if args[0] != '-c' else 'commit'} exited {cp.returncode}: "
                               f"{cp.stderr.decode('utf-8', 'replace')[-300:]}")
        return cp

    files = sorted(new_files)
    baseline = {p: read_head(repo, p, run) for p in files}
    git("checkout", "-q", "-b", draft["branch"])
    git("add", "--", *files)
    git("-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}", "-c", "commit.gpgsign=false",
        "commit", "-q", "--no-verify", "-m", draft["title"])
    _verify_commit(repo, baseline, new_files, run)
    _verify_base(repo, run)
    # An explicit URL, not `origin`: never a remote read from config. Only this call
    # carries the token.
    git("push", "-q", clone_url(policy), f"HEAD:refs/heads/{draft['branch']}", extra_env=_token_env(token))
    return gh.create_pull(policy.cluster_repo, draft["title"], draft["branch"], "main", draft["body"])["html_url"]


def _fit(text: str, limit: int = MAX_BODY_CHARS) -> str:
    """`text` cut to at most `limit` characters, ending in a marker saying how much went."""
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    while True:
        marker = f"\n\n[truncated by triage: {omitted} characters omitted]"
        keep = limit - len(marker)
        if len(text) - keep == omitted:
            return text[:keep] + marker
        omitted = len(text) - keep


def _scrub(text: str, run) -> tuple:
    """Redact secrets from text bound for the private repo, then cut it to
    MAX_BODY_CHARS. Returns (text, extra labels). A scan that cannot run (including one
    the deadline cut off) never stops the issue."""
    try:
        hits = leakscan.scan({"text": text}, run)
    except Exception as e:
        note = f"\n\n> Leak scan could not run ({str(e)[:300]}); this text was posted unscanned."
        return _fit(text, MAX_BODY_CHARS - len(note)) + note, ["leakscan-failed"]
    if hits:
        return _fit(leakscan.redact(text, hits["text"])), ["redacted"]
    return _fit(text), []


def _gates_and_pr(inc, result, policy, gh, token, workdir: Path, run, run_id: str, g: Gates) -> None:
    evaluate_gates(inc, result, policy, workdir, run, g)
    if g.guard is None or not g.guard.ok:
        return
    cls = result["class"]
    draft = {"branch": prbody.branch(cls, inc.fp12, run_id),
             "title": prbody.title(cls, result["app_dir"]),
             "body": prbody.body(cls, g.guard.changes, result.get("metrics") or {}, inc.fp12),
             "patch": result["patch"]}
    g.preview, g.note = draft, "shadow mode" if g.shadow else "blocked by a gate"
    try:
        if leakscan.scan({"pr_body": draft["body"], "patch": draft["patch"]}, run):
            g.pr_allowed = False
            g.lines.append("✗ leak scan found a secret in the PR body or patch")
    except leakscan.LeakScanError as e:
        g.pr_allowed = False
        g.lines.append(f"✗ leak scan could not run, so no PR: {e}")
    if g.pr_allowed and not g.shadow:
        try:
            g.pr_url = open_pr(g.repo, gh, policy, draft, g.guard.new_files, token, run)
            g.preview = None
        except CommitMismatch as e:
            g.lines.append(str(e))
        except Exception as e:
            g.lines.append(f"✗ opening the PR failed: {type(e).__name__}: {str(e)[:300]}")


def publish_incident(inc, result, existing_issue, policy, gh, token, notify, workdir: Path, run, *,
                     run_id: str, deadline: float = None, clock=time.time) -> dict:
    """Issue first, then the gates, then the gate report as a comment.

    The diagnosis is filed before any gate runs, so nothing the gates do (a crash, a
    hung tool, the Job deadline) can lose it; any exception in the gates becomes a
    gate line in the report."""
    home = workdir / "home"
    home.mkdir(parents=True, exist_ok=True)
    run = SafeRun(run, clean_env(home), clock() + PUBLISH_BUDGET_SECONDS if deadline is None else deadline, clock)
    cls = result.get("class") or "none"
    pending = gates_apply(inc, result)

    text = issuebody.render_incident(inc, result, [], None, None, "")
    body, labels = _scrub(text + ("\n\n" + GATES_PENDING if pending else ""), run)
    labels = [f"fp/{inc.fp12}", f"class/{cls}"] + labels
    if result["status"] != "ok":
        labels.append("agent-error")
    if result.get("injection_suspected"):
        labels.append("injection-suspected")
    title = issuebody.incident_title(inc)
    if existing_issue:
        gh.comment(policy.reports_repo, existing_issue, body)
        gh.add_labels(policy.reports_repo, existing_issue, labels)
        number, outcome, issue_url = existing_issue, "commented", None
    else:
        issue = gh.create_issue(policy.reports_repo, title, body, labels)
        number, outcome, issue_url = issue["number"], "issue_created", issue["html_url"]

    g, problems = Gates(), []
    if pending:
        try:
            _gates_and_pr(inc, result, policy, gh, token, workdir, run, run_id, g)
        except Exception as e:
            g.pr_allowed = False
            g.lines.append(f"✗ publish stopped while running the gates, so no PR: "
                           f"{type(e).__name__}: {str(e)[:300]}")
        report, more = _scrub(issuebody.render_gate_report(inc, g.lines, g.pr_url, g.preview, g.note), run)
        more += (["shadow"] if g.preview is not None and g.shadow else []) + (["pr-opened"] if g.pr_url else [])
        try:
            gh.comment(policy.reports_repo, number, report)
            if more:
                gh.add_labels(policy.reports_repo, number, more)
        except Exception as e:
            problems.append(f"posting the gate report failed: {type(e).__name__}: {e}")

    if outcome == "issue_created":
        headline = (result["summary"].split("\n")[0] if result["status"] == "ok"
                    else f"Investigation failed: {result['failure_reason']}")
        if g.pr_url:
            headline += f"\nPR opened: {g.pr_url}"
        try:
            notify(f"Triage: {title}", headline, url=issue_url)
        except Exception as e:
            problems.append(f"notify failed: {type(e).__name__}: {e}")
    if result["status"] != "ok":
        outcome = "agent_error"
    out = {"outcome": outcome, "cost_usd": float(result.get("cost_usd") or 0.0), "issue": number, "pr": g.pr_url}
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        out["detail"] = "; ".join(problems)[:500]
    return out


def _notify_quietly(notify, *args, **kw):
    try:
        notify(*args, **kw)
    except Exception as e:
        print(f"notify failed: {type(e).__name__}: {e}", file=sys.stderr)


def publish_sweep(inc, result, policy, gh, notify, run, now: datetime, *, workdir: Path = PUBLISH,
                  deadline: float = None, clock=time.time) -> dict:
    run = SafeRun(run, clean_env(workdir / "home"),
                  clock() + PUBLISH_BUDGET_SECONDS if deadline is None else deadline, clock)
    cost = float(result.get("cost_usd") or 0.0)
    if result["status"] != "ok":
        body, labels = _scrub(issuebody.render_incident(inc, result, [], None, None, ""), run)
        issue = gh.create_issue(policy.reports_repo, "[sweep] investigation did not complete", body,
                                [f"fp/{inc.fp12}", "sweep", "agent-error"] + labels)
        _notify_quietly(notify, "Triage sweep failed",
                        f"{result['failure_reason']}: {result.get('detail', '')[:300]}", url=issue["html_url"])
        return {"outcome": "agent_error", "cost_usd": cost, "issue": issue["number"]}
    new = 0
    for f in result["findings"]:
        fp = sweep_finding_fingerprint(f["check"], f["namespace"], f["object"])
        title, body = issuebody.render_finding(f, fp)
        body, labels = _scrub(body, run)
        existing = gh.find_open_issue(policy.reports_repo, fp[:12])
        if existing:
            updated = datetime.fromisoformat(existing["updated_at"].replace("Z", "+00:00"))
            if now - updated >= timedelta(hours=policy.reinvestigate_after_hours):
                gh.comment(policy.reports_repo, existing["number"], body)
            continue
        gh.create_issue(policy.reports_repo, title, body,
                        [f"fp/{fp[:12]}", "sweep", f"check/{f['check']}"] + labels)
        new += 1
    if new:
        _notify_quietly(notify, "Triage sweep", f"{new} new finding(s). {result['summary'][:300]}",
                        url=f"https://github.com/{policy.reports_repo}/issues?q=is%3Aopen+label%3Asweep")
    return {"outcome": "sweep_ok", "cost_usd": cost, "findings": len(result["findings"]), "new": new}


def termination_text(summary: dict) -> str:
    """One line of valid JSON within TERMINATION_MAX bytes. ASCII-escaped, so
    characters are bytes; string fields other than `outcome` are cut until it fits."""
    # A non-finite float would be written as the bare token NaN/Infinity, which is not
    # JSON; None tells the dispatcher the cost is unknown, so it charges the per-run cap.
    s = {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in summary.items()}
    try:
        text = json.dumps(s, allow_nan=False)
        while len(text) > TERMINATION_MAX:
            cuttable = [k for k, v in s.items() if isinstance(v, str) and v and k != "outcome"]
            if not cuttable:
                break
            longest = max(cuttable, key=lambda k: len(json.dumps(s[k])))
            s[longest] = s[longest][: len(s[longest]) // 2]
            text = json.dumps(s, allow_nan=False)
        if len(text) <= TERMINATION_MAX:
            return text
    except (TypeError, ValueError):
        pass
    cost = s.get("cost_usd")
    return json.dumps({"outcome": str(s.get("outcome", "publish_failed"))[:100],
                       "cost_usd": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None},
                      allow_nan=False)


def run_id_from(environ, clock=time.time) -> str:
    """TRIAGE_RUN (the dispatcher's Job-name suffix) if it is ref-safe, else now."""
    rid = environ.get("TRIAGE_RUN", "")
    return rid if prbody._RUN_ID.fullmatch(rid) else str(int(clock()))


def deadline_from(environ, start: float) -> float:
    """When subprocesses must be done: TRIAGE_DEADLINE_EPOCH (the Job's own deadline)
    less time for the GitHub calls, if set and sane; else a fixed budget from start."""
    raw = environ.get("TRIAGE_DEADLINE_EPOCH", "")
    if raw.isascii() and raw.isdigit():
        return float(raw) - API_RESERVE_SECONDS
    return start + PUBLISH_BUDGET_SECONDS


def main() -> int:
    start = time.time()
    summary = {"outcome": "publish_failed", "cost_usd": 0.0}
    notify, result = None, None
    try:
        if _TEST_REMOTE is not None:
            raise RuntimeError("the test-only remote hook is set; refusing to publish")
        policy = load_policy()
        inc = Incident.from_json(os.environ["TRIAGE_INCIDENT"])
        existing = os.environ.get("TRIAGE_EXISTING_ISSUE") or None
        result = load_result(RESULT)
        summary["cost_usd"] = float(result.get("cost_usd") or 0.0)
        notify = Pushover.from_dir().send
        repos = [policy.reports_repo.split("/", 1)[1], policy.cluster_repo.split("/", 1)[1]]
        token = GitHubApp.from_dir().token(
            repos, {"issues": "write", "contents": "write", "pull_requests": "write", "metadata": "read"})
        gh = GitHub(lambda: token)
        deadline = deadline_from(os.environ, start)
        if inc.source == "sweep":
            summary = publish_sweep(inc, result, policy, gh, notify, subprocess.run, datetime.now(timezone.utc),
                                    workdir=PUBLISH, deadline=deadline)
        else:
            summary = publish_incident(inc, result, int(existing) if existing else None, policy, gh, token,
                                       notify, PUBLISH, subprocess.run, run_id=run_id_from(os.environ),
                                       deadline=deadline)
    except Exception as e:
        summary["detail"] = f"{type(e).__name__}: {e}"[:500]
        print(f"publish failed: {summary['detail']}", file=sys.stderr)
        if notify is not None:
            diagnosis = (result or {}).get("summary") or ""
            try:
                notify("Triage publish failed", f"{summary['detail']}\n\nDiagnosis: {diagnosis[:600]}")
            except Exception as e2:
                print(f"pushover also failed: {e2}", file=sys.stderr)
    if result is not None and result.get("cost_estimated"):
        # Charged at the per-run cap because the CLI never reported a cost.
        summary["cost_estimated"] = True
    text = termination_text(summary)
    try:
        TERMINATION_LOG.write_text(text)
    except OSError as e:
        print(f"could not write the termination log: {e}", file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
