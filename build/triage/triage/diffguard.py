"""Gate 3: does a model-written patch stay inside what its class may change?

The model chooses the patch; this module decides whether it may become a PR on a
public repo. It never trusts the model's description of the patch, only the patch
applied to the real checkout and diffed field by field.
"""
import difflib
import math
import posixpath
import re
from dataclasses import dataclass, field

import yaml

from . import patching, yamldiff
from .yamldiff import MISSING

# Every pattern here is ASCII-only. `\d` (and float()) accept any Unicode decimal digit,
# so `５１２Mi` (fullwidth) or `𝟓𝟏𝟐Mi` (math bold) would pass, render like 512Mi in the
# PR, and be rejected by Kubernetes, freezing the Flux Kustomization.
_QUANTITY = re.compile(r"([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|Ti|k|M|G|T)?", re.ASCII)
_MULTIPLIER = {None: 1, "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
               "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
# Gate 3 has two layers. The structural diff says WHICH field changed and checks its
# value; these line rules say the committed BYTES changed nothing else. Without them a
# patch can add comments (prose a reviewer reads as authoritative), re-quote or
# re-indent lines, or add anything else YAML parses to the same tree.
_MEMORY_LINE = re.compile(r"""^([ \t]*)memory:[ \t]*(["']?)[0-9]+(\.[0-9]+)?(Ki|Mi|Gi|Ti|k|M|G|T)?\2[ \t]*\r?\Z""", re.ASCII)
_CPU_LINE = re.compile(r"""^[ \t]*cpu:[ \t]*(["']?)[0-9.]+m?\1[ \t]*\r?\Z""", re.ASCII)
# Printable ASCII only: PyYAML also breaks lines at \x85, \u2028 and \u2029, which
# "\n"-split lines would otherwise hide a second YAML line inside.
_RENOVATE_LINE = re.compile(r"^([ \t]*)(version|tag|image):[ \t]*[\x21-\x7e][\x20-\x7e]*\r?\Z", re.ASCII)
# The schema enforces this upstream; check() is the boundary, so it checks again.
_APP_DIR = re.compile(r"clusters/vollminlab-cluster/[a-z0-9-]+/[a-z0-9-]+/app/?", re.ASCII)
HEADROOM = 1.2
MAX_GROWTH = 2.0


def parse_bytes(q) -> float:
    if isinstance(q, bool) or not isinstance(q, (str, int)):
        raise ValueError(f"not a memory quantity: {q!r}")
    m = _QUANTITY.fullmatch(str(q))
    if not m:
        raise ValueError(f"not a memory quantity: {q!r}")
    return float(m.group(1)) * _MULTIPLIER[m.group(2)]


@dataclass(frozen=True)
class FieldChange:
    file: str
    doc: tuple           # yamldiff.doc_key: (apiVersion, kind, namespace, name)
    field: str           # dotted, for display only; keys may contain dots
    old: object
    new: object
    path: tuple = ()     # the field's real path within the document; match on this


_LIMITS_MEMORY = ("resources", "limits", "memory")
_REQUESTS_MEMORY = ("resources", "requests", "memory")
_LIMITS_CPU = ("resources", "limits", "cpu")


@dataclass
class GuardResult:
    ok: bool
    reason: str
    changes: list = field(default_factory=list)
    new_files: dict = field(default_factory=dict)


def _fail(reason, changes=None, new_files=None):
    return GuardResult(False, reason, changes or [], new_files or {})


def forbidden_file(path: str) -> bool:
    name = posixpath.basename(path)
    return name == "kustomization.yaml" or name.endswith("-externalsecret.yaml") or "networkpolicy" in name


_KEY_VALUE = re.compile(r"([ \t]*[A-Za-z]+:[ \t]*)(.*?)([ \t]*\r?)", re.ASCII | re.DOTALL)


def _split(line: str):
    """(frame, value): the frame is everything but the value (indentation, key,
    separator, quote style, trailing whitespace and any \r), byte for byte."""
    m = _KEY_VALUE.fullmatch(line)
    if not m:
        return None, None
    v = m.group(2)
    q = v[0] if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0] else ""
    return (m.group(1), q, m.group(3)), (v[1:-1] if q else v)


def _value_only(path, removed, added, cls):
    """None if `added` is `removed` with its value changed and nothing else, else why.
    A re-quoted or re-terminated line with the same value changes no field, so it
    would otherwise ride along beside a real change."""
    (rf, rv), (af, av) = _split(removed), _split(added)
    if rf is None or af is None or rf != af:
        return f"{path}: {cls} may change only the value, not the formatting, of {added[:80]!r}"
    if rv == av:
        return f"{path}: {cls} must change the value of {added[:80]!r}"
    return None


def line_edits(path: str, old: str, new: str) -> list:
    """[(path, removed lines, added lines)], one entry per changed region."""
    a, b = old.split("\n"), new.split("\n")
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return [(path, a[i1:i2], b[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def _bad(path, line, cls):
    return f"{path}: {cls} may not change the line {line[:80]!r}"


def line_violation(cls: str, edits: list):
    """None if every removed and added line is one `cls` may touch, else the reason."""
    if cls == "oom-memory-limit":
        for path, removed, added in edits:
            for line in added + removed:
                if not _MEMORY_LINE.match(line):
                    return _bad(path, line, cls)
            if len(removed) != len(added):
                return f"{path}: {cls} must replace memory lines one for one"
            for r, a in zip(removed, added):
                reason = _value_only(path, r, a, cls)
                if reason:
                    return reason
        return None
    if cls == "cpu-throttle-limit":
        for path, removed, added in edits:
            if added:
                return _bad(path, added[0], cls)
            for line in removed:
                if not _CPU_LINE.match(line):
                    return _bad(path, line, cls)
        return None
    if cls == "renovate-breakage":
        lines = [(p, l) for p, r, a in edits for l in a + r]
        for path, line in lines:
            if not _RENOVATE_LINE.match(line) or "#" in line:
                return _bad(path, line, cls)
        if len(edits) != 1 or len(edits[0][1]) != 1 or len(edits[0][2]) != 1:
            return f"{cls} must replace exactly one line"
        path, [r], [a] = edits[0]
        return _value_only(path, r, a, cls)
    return f"no line rules exist for class {cls!r}"


def app_dir_in_namespace(app_dir, namespace, list_files, read_file) -> bool:
    """True if some manifest in app_dir targets `namespace`, so the model cannot
    point a real incident at an unrelated app's directory."""
    for path in list_files(app_dir):
        if not path.endswith((".yaml", ".yml")):
            continue
        try:
            docs = yamldiff.load_docs(read_file(path) or "")
        except yaml.YAMLError:
            continue
        for d in docs:
            if not isinstance(d, dict):
                continue
            meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            spec = d.get("spec") if isinstance(d.get("spec"), dict) else {}
            if namespace and namespace in (meta.get("namespace"), spec.get("targetNamespace")):
                return True
    return False


def _number(v) -> bool:
    """A finite int or float. bool is an int subclass and is refused; so are NaN
    and inf, which make every comparison against them false or true."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _oom(changes, metrics, new_files):
    peak = metrics.get("peak_memory_bytes")
    if peak is None:
        return "oom-memory-limit requires metrics.peak_memory_bytes (measured, not estimated)"
    if not _number(peak) or peak <= 0:
        return f"metrics.peak_memory_bytes must be a finite number of bytes above zero, not {peak!r}"
    # Each change belongs to one `resources` block: (file, document, path up to and
    # including "resources"). A request may move only beside its OWN limit, never
    # because some other container's limit was raised in the same patch.
    blocks = {}
    for c in changes:
        if c.path[-3:] not in (_LIMITS_MEMORY, _REQUESTS_MEMORY):
            return f"{c.file}: {c.field} is not a memory request or limit"
        if c.old is MISSING or c.new is MISSING:
            return f"{c.file}: {c.field} was added or removed, not raised"
        try:
            old_b, new_b = parse_bytes(c.old), parse_bytes(c.new)
        except ValueError as e:
            return f"{c.file}: {c.field}: {e}"
        if new_b <= old_b:
            return f"{c.file}: {c.field} {c.old} -> {c.new} does not raise it"
        if new_b > MAX_GROWTH * old_b:
            return f"{c.file}: {c.field} {c.old} -> {c.new} more than doubles it"
        if c.path[-3:] == _LIMITS_MEMORY and new_b < HEADROOM * float(peak):
            return (f"{c.file}: new limit {c.new} is below {HEADROOM}x the measured peak "
                    f"({float(peak) / 2**20:.0f}Mi)")
        blocks.setdefault((c.file, c.doc, c.path[:-2]), []).append(c)
    for (file, doc, prefix), cs in blocks.items():
        where = ".".join(prefix)
        if not any(c.path[-3:] == _LIMITS_MEMORY for c in cs):
            return f"{file}: {where} changes its memory request without raising its own memory limit"
        new_doc = yamldiff.keyed_docs(new_files[file])[doc]
        req = yamldiff.lookup(new_doc, prefix + ("requests", "memory"))
        lim = yamldiff.lookup(new_doc, prefix + ("limits", "memory"))
        if req is not MISSING and lim is not MISSING:
            try:
                if parse_bytes(req) > parse_bytes(lim):
                    return f"{file}: {where}: memory request {req} exceeds its limit {lim}"
            except ValueError as e:
                return f"{file}: {where}: {e}"
    if not blocks:
        return "oom-memory-limit must raise a memory limit"
    return None


def _cpu(changes, metrics):
    if "throttled_ratio" not in metrics:
        return "cpu-throttle-limit requires metrics.throttled_ratio (measured)"
    ratio = metrics["throttled_ratio"]
    if not _number(ratio) or not 0 <= ratio <= 1:
        return f"metrics.throttled_ratio must be a finite fraction in [0, 1], not {ratio!r}"
    for c in changes:
        if c.path[-3:] != _LIMITS_CPU:
            return f"{c.file}: {c.field} is not a CPU limit"
        if c.old is MISSING or c.new is not MISSING:
            return f"{c.file}: cpu-throttle-limit may only remove a CPU limit"
    return None


def _same(a, b) -> bool:
    return type(a) is type(b) and a == b


def _historic(c, added_line, file_history) -> bool:
    """True only if c.new is the value c.file held at the same document and field
    path immediately before the current one, written the same way.

    Walks the history newest first, skipping revisions whose value there still
    equals the current (old) one. The first revision with a different value is
    the only acceptable target: any older one could skip several majors, past
    schema migrations (CNPG, Authentik, Harbor) the older version cannot read.
    If the field did not exist there, or that revision cannot be read, it fails.
    Not a substring search: under `git log -S` semantics `tag: v1` matched
    `apiVersion: v1`, and `1.1` matched `1.10`."""
    want = added_line.strip()
    for text in file_history(c.file) or []:
        try:
            docs = yamldiff.keyed_docs(text)
            past = yamldiff.lookup(docs[c.doc], c.path) if c.doc in docs else MISSING
        except Exception:            # unreadable: it might hide the real previous value
            return False
        if _same(past, c.old):
            continue
        if past is MISSING or isinstance(past, (dict, list)):
            return False
        return _same(past, c.new) and want in (l.strip() for l in text.split("\n"))
    return False


def _renovate(changes, file_history, denylist, new_files, added_line):
    if len(changes) != 1:
        return f"renovate-breakage must change exactly one value, not {len(changes)}"
    c = changes[0]
    if not c.path or c.path[-1] not in ("version", "tag", "image"):
        return f"{c.file}: {c.field} is not a version, tag or image field"
    if c.old is MISSING or c.new is MISSING or isinstance(c.old, (dict, list)) or isinstance(c.new, (dict, list)):
        return f"{c.file}: {c.field} must be a scalar changed in place to a scalar"
    new = str(c.new)
    for name in denylist:
        # Checked against the whole file, not just the changed value: a pinned image's
        # name lives in `repository` while the bump changes `tag` beside it.
        if name in c.file or name in new_files[c.file]:
            return f"{name} is pinned by repo policy (see .claude/rules); triage may not change it"
    if not _historic(c, added_line, file_history):
        return (f"{new!r} is not the value {c.field} held immediately before {c.old!r} in "
                f"{c.file}'s git history: renovate-breakage may only revert one step")
    return None


def check(patch_text, cls, app_dir, namespace, metrics, *, read_file, list_files,
          file_history, denylist) -> GuardResult:
    """Gate 3. Never raises: any exception while parsing or comparing is a gate failure.

    An exception escaping here would crash publish and suppress the issue, so a
    prompt-injected model could hide its own report by making the gate throw
    (PyYAML raises ValueError for `2026-13-45` and RecursionError for deep nesting).

    read_file(path) -> str | None and list_files(dir) -> [path] read the checkout.
    file_history(path) -> [str]: the texts of the file's past revisions, newest
    first, possibly empty (e.g. `git log --format=%H -- <path>` then
    `git show <rev>:<path>` for each). Including the current revision is harmless.
    Used only by renovate-breakage, whose new value must equal, at the same document
    and field path, the value held immediately before the current one."""
    try:
        return _check(patch_text, cls, app_dir, namespace, metrics, read_file=read_file,
                      list_files=list_files, file_history=file_history, denylist=denylist)
    except Exception as e:
        return _fail(f"the diff guard could not evaluate the patch: {type(e).__name__}: {str(e)[:200]}")


def _check(patch_text, cls, app_dir, namespace, metrics, *, read_file, list_files,
           file_history, denylist) -> GuardResult:
    if cls not in ("oom-memory-limit", "cpu-throttle-limit", "renovate-breakage"):
        return _fail(f"no diff rules exist for class {cls!r}")
    if not isinstance(metrics, dict):
        return _fail(f"metrics must be a mapping, not {type(metrics).__name__}")
    if not app_dir:
        return _fail("no app_dir was given")
    if not isinstance(app_dir, str) or not _APP_DIR.fullmatch(app_dir):
        return _fail(f"app_dir {str(app_dir)[:120]!r} is not clusters/vollminlab-cluster/<namespace>/<app>/app/")
    app_dir = app_dir.rstrip("/") + "/"
    try:
        file_patches = patching.parse(patch_text or "")
    except patching.PatchError as e:
        return _fail(f"unparseable patch: {e}")
    changes, new_files, edits = [], {}, []
    for fp in file_patches:
        if fp.old_path is None or fp.new_path is None:
            return _fail("the patch adds or deletes a file")
        if fp.old_path != fp.new_path:
            return _fail(f"the patch renames {fp.old_path} to {fp.new_path}")
        path = posixpath.normpath(fp.new_path)
        if path != fp.new_path or not path.startswith(app_dir):
            return _fail(f"{fp.new_path} is outside the incident's app directory {app_dir}")
        if not path.endswith((".yaml", ".yml")):
            return _fail(f"{path}: only YAML files may be changed")
        if forbidden_file(path):
            return _fail(f"{path}: no class may change this file")
        if path in new_files:
            return _fail(f"{path} appears twice in the patch")
        old = read_file(path)
        if old is None:
            return _fail(f"{path} does not exist in the checkout")
        try:
            new = patching.apply(old, fp)
        except patching.PatchError as e:
            return _fail(f"the patch does not apply: {e}")
        try:
            diffs = yamldiff.diff(old, new)
        except yaml.YAMLError as e:
            return _fail(f"{path} is not valid YAML after the patch: {e}")
        except yamldiff.DiffError as e:
            return _fail(f"{path}: {e}")
        edits += line_edits(path, old, new)
        changes += [FieldChange(path, c.path[0], ".".join(c.path[1:]), c.old, c.new, c.path[1:]) for c in diffs]
        new_files[path] = new
    if not changes:
        return _fail("the patch changes no YAML value")
    reason = line_violation(cls, edits)
    if reason:
        return _fail(reason, changes, new_files)
    if not app_dir_in_namespace(app_dir, namespace, list_files, read_file):
        return _fail(f"{app_dir} declares nothing in namespace {namespace!r}, so it is not this incident's app")
    rules = {
        "oom-memory-limit": lambda: _oom(changes, metrics, new_files),
        "cpu-throttle-limit": lambda: _cpu(changes, metrics),
        "renovate-breakage": lambda: _renovate(changes, file_history, denylist, new_files, edits[0][2][0]),
    }
    reason = rules[cls]()
    if reason:
        return _fail(reason, changes, new_files)
    return GuardResult(True, "", changes, new_files)
