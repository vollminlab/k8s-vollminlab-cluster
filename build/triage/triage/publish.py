"""Main container: result.json -> private issue, maybe a PR, and a notification.

There is no LLM in this process. It decides, with plain code, what reaches GitHub.
Contract: always exits 0 and always writes a one-line JSON summary to the
termination log, which the dispatcher reads for outcome and spend accounting.

The checkout under /work/repo was shared with the investigate container, where a
possibly prompt-injected model ran. So nothing here trusts the working tree:
the diff guard reads the committed HEAD, a checkout with any modified, untracked
or ignored file never becomes a PR, git never runs a hook, and the commit is
compared byte for byte against what the gates approved before it is pushed.
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

WORK = Path("/work")
TERMINATION_LOG = Path("/dev/termination-log")
TERMINATION_MAX = 3500          # the kubelet keeps 4096 bytes
BOT_NAME = "vollminlab-triage[bot]"
BOT_EMAIL = "vollminlab-triage[bot]@users.noreply.github.com"
HISTORY_DEPTH = 50
DIRTY = "✗ checkout was modified by the investigation; refusing to build a PR from it"

# On every git invocation. Hooks are files under .git/, which the investigation could
# have written; fsmonitor is a config-driven command run by `git status`; autocrlf
# would rewrite the bytes the gates approved on `git add`.
GIT_SAFETY = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false")


@dataclass
class Gates:
    pr_allowed: bool = False
    shadow: bool = True
    lines: list = field(default_factory=list)
    guard: object = None


class CommitMismatch(Exception):
    """The commit is not exactly the guarded change; its message is the gate line."""


def _git(repo: Path, run, *args, env=None, timeout=120):
    # Bytes, never text=True: universal-newline decoding would turn CRLF into LF.
    return run(["git", *GIT_SAFETY, "-C", str(repo), *args], capture_output=True, timeout=timeout,
               env=env if env is not None else dict(os.environ, GIT_TERMINAL_PROMPT="0"))


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


def evaluate_gates(inc, result, policy, repo: Path, run) -> Gates:
    g = Gates()
    cls = result.get("class", "none")
    if result["status"] != "ok" or cls == "none" or not result.get("patch") or inc.source == "sweep":
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


def _git_env(token: str) -> dict:
    # Through the environment (GIT_CONFIG_*), never argv: the token must not appear
    # in a process listing or in a CalledProcessError that quotes the command.
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_COUNT="1",
                GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}")


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


def open_pr(repo: Path, gh, policy, draft: dict, new_files: dict, token: str, run) -> str:
    def git(*args, env=None):
        cp = _git(repo, run, *args, env=env, timeout=180)
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
    # An explicit URL, not `origin`: the remote in .git/config is checkout state too.
    # Only this call carries the token, so no filter or helper run earlier can see it.
    git("push", "-q", f"https://github.com/{policy.cluster_repo}.git", f"HEAD:refs/heads/{draft['branch']}",
        env=_git_env(token))
    return gh.create_pull(policy.cluster_repo, draft["title"], draft["branch"], "main", draft["body"])["html_url"]


def _scrub(text: str, run) -> tuple:
    """Redact secrets from text bound for the private repo. Returns (text, extra labels)."""
    try:
        hits = leakscan.scan({"text": text}, run)
    except leakscan.LeakScanError as e:
        return text + f"\n\n> Leak scan could not run ({e}); this text was posted unscanned.", ["leakscan-failed"]
    if hits:
        return leakscan.redact(text, hits["text"]), ["redacted"]
    return text, []


def publish_incident(inc, result, existing_issue, policy, gh, token, notify, repo: Path, run, *, run_id: str) -> dict:
    gates = evaluate_gates(inc, result, policy, repo, run)
    cls = result.get("class") or "none"
    pr_url, preview, note = None, None, ""
    if gates.guard is not None and gates.guard.ok:
        draft = {"branch": prbody.branch(cls, inc.fp12, run_id),
                 "title": prbody.title(cls, result["app_dir"]),
                 "body": prbody.body(cls, gates.guard.changes, result.get("metrics") or {}, inc.fp12),
                 "patch": result["patch"]}
        try:
            if leakscan.scan({"pr_body": draft["body"], "patch": draft["patch"]}, run):
                gates.pr_allowed = False
                gates.lines.append("✗ leak scan found a secret in the PR body or patch")
        except leakscan.LeakScanError as e:
            gates.pr_allowed = False
            gates.lines.append(f"✗ leak scan could not run, so no PR: {e}")
        if gates.pr_allowed and not gates.shadow:
            try:
                pr_url = open_pr(repo, gh, policy, draft, gates.guard.new_files, token, run)
            except CommitMismatch as e:
                gates.lines.append(str(e))
            except Exception as e:
                gates.lines.append(f"✗ opening the PR failed: {type(e).__name__}: {str(e)[:300]}")
        if pr_url is None:
            preview = draft
            note = "shadow mode" if gates.shadow else "blocked by a gate"

    body, labels = _scrub(issuebody.render_incident(inc, result, gates.lines, pr_url, preview, note), run)
    labels = [f"fp/{inc.fp12}", f"class/{cls}"] + labels
    if result["status"] != "ok":
        labels.append("agent-error")
    if result.get("injection_suspected"):
        labels.append("injection-suspected")
    if preview is not None and gates.shadow:
        labels.append("shadow")
    if pr_url:
        labels.append("pr-opened")

    if existing_issue:
        gh.comment(policy.reports_repo, existing_issue, body)
        gh.add_labels(policy.reports_repo, existing_issue, labels)
        number, outcome = existing_issue, "commented"
    else:
        title = issuebody.incident_title(inc)
        issue = gh.create_issue(policy.reports_repo, title, body, labels)
        number, outcome = issue["number"], "issue_created"
        headline = (result["summary"].split("\n")[0] if result["status"] == "ok"
                    else f"Investigation failed: {result['failure_reason']}")
        if pr_url:
            headline += f"\nPR opened: {pr_url}"
        notify(f"Triage: {title}", headline, url=issue["html_url"])
    if result["status"] != "ok":
        outcome = "agent_error"
    return {"outcome": outcome, "cost_usd": float(result.get("cost_usd") or 0.0), "issue": number, "pr": pr_url}


def publish_sweep(inc, result, policy, gh, notify, run, now: datetime) -> dict:
    cost = float(result.get("cost_usd") or 0.0)
    if result["status"] != "ok":
        body, labels = _scrub(issuebody.render_incident(inc, result, [], None, None, ""), run)
        issue = gh.create_issue(policy.reports_repo, "[sweep] investigation did not complete", body,
                                [f"fp/{inc.fp12}", "sweep", "agent-error"] + labels)
        notify("Triage sweep failed", f"{result['failure_reason']}: {result.get('detail', '')[:300]}",
               url=issue["html_url"])
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
        notify("Triage sweep", f"{new} new finding(s). {result['summary'][:300]}",
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


def main() -> int:
    summary = {"outcome": "publish_failed", "cost_usd": 0.0}
    notify, result = None, None
    try:
        policy = load_policy()
        inc = Incident.from_json(os.environ["TRIAGE_INCIDENT"])
        existing = os.environ.get("TRIAGE_EXISTING_ISSUE") or None
        result = load_result(WORK / "result.json")
        summary["cost_usd"] = float(result.get("cost_usd") or 0.0)
        notify = Pushover.from_dir().send
        repos = [policy.reports_repo.split("/", 1)[1], policy.cluster_repo.split("/", 1)[1]]
        token = GitHubApp.from_dir().token(
            repos, {"issues": "write", "contents": "write", "pull_requests": "write", "metadata": "read"})
        gh = GitHub(lambda: token)
        if inc.source == "sweep":
            summary = publish_sweep(inc, result, policy, gh, notify, subprocess.run, datetime.now(timezone.utc))
        else:
            summary = publish_incident(inc, result, int(existing) if existing else None, policy, gh, token,
                                       notify, WORK / "repo", subprocess.run, run_id=run_id_from(os.environ))
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
