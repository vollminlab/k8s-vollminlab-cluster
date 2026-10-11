"""Gate 3: does a model-written patch stay inside what its class may change?

The model chooses the patch; this module decides whether it may become a PR on a
public repo. It never trusts the model's description of the patch, only the patch
applied to the real checkout and diffed field by field.
"""
import difflib
import posixpath
import re
from dataclasses import dataclass, field

import yaml

from . import patching, yamldiff
from .yamldiff import MISSING

_QUANTITY = re.compile(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|k|M|G|T)?$")
_MULTIPLIER = {None: 1, "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
               "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
# Gate 3 has two layers. The structural diff says WHICH field changed and checks its
# value; these line rules say the committed BYTES changed nothing else. Without them a
# patch can add comments (prose a reviewer reads as authoritative), re-quote or
# re-indent lines, or add anything else YAML parses to the same tree.
_MEMORY_LINE = re.compile(r"""^([ \t]*)memory:[ \t]*(["']?)\d+(\.\d+)?(Ki|Mi|Gi|Ti|k|M|G|T)?\2[ \t]*\r?$""")
_CPU_LINE = re.compile(r"""^[ \t]*cpu:[ \t]*(["']?)[0-9.]+m?\1[ \t]*\r?$""")
# Printable ASCII only: PyYAML also breaks lines at \x85, \u2028 and \u2029, which
# "\n"-split lines would otherwise hide a second YAML line inside.
_RENOVATE_LINE = re.compile(r"^([ \t]*)(version|tag|image):[ \t]*[\x21-\x7e][\x20-\x7e]*\r?$")
HEADROOM = 1.2
MAX_GROWTH = 2.0


def parse_bytes(q) -> float:
    m = _QUANTITY.match(str(q).strip())
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


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip(" \t"))]


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
                if _indent(r) != _indent(a):
                    return f"{path}: {cls} may not re-indent {a[:80]!r}"
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
        mr, ma = _RENOVATE_LINE.match(r), _RENOVATE_LINE.match(a)
        if mr.group(1) != ma.group(1) or mr.group(2) != ma.group(2):
            return f"{path}: {cls} must keep the line's key and indentation: {a[:80]!r}"
        return None
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


def _oom(changes, metrics):
    peak = metrics.get("peak_memory_bytes")
    if peak is None:
        return "oom-memory-limit requires metrics.peak_memory_bytes (measured, not estimated)"
    raised_limit = False
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
        if c.path[-3:] == _LIMITS_MEMORY:
            raised_limit = True
            if new_b < HEADROOM * float(peak):
                return (f"{c.file}: new limit {c.new} is below {HEADROOM}x the measured peak "
                        f"({float(peak) / 2**20:.0f}Mi)")
    if not raised_limit:
        return "oom-memory-limit must raise a memory limit"
    return None


def _cpu(changes, metrics):
    if "throttled_ratio" not in metrics:
        return "cpu-throttle-limit requires metrics.throttled_ratio (measured)"
    for c in changes:
        if c.path[-3:] != _LIMITS_CPU:
            return f"{c.file}: {c.field} is not a CPU limit"
        if c.old is MISSING or c.new is not MISSING:
            return f"{c.file}: cpu-throttle-limit may only remove a CPU limit"
    return None


def _renovate(changes, history_has_value, denylist, new_files):
    if len(changes) != 1:
        return f"renovate-breakage must change exactly one value, not {len(changes)}"
    c = changes[0]
    if not c.path or c.path[-1] not in ("version", "tag", "image"):
        return f"{c.file}: {c.field} is not a version, tag or image field"
    if c.old is MISSING or c.new is MISSING or isinstance(c.new, (dict, list)):
        return f"{c.file}: {c.field} must be changed in place to a scalar"
    new = str(c.new)
    for name in denylist:
        # Checked against the whole file, not just the changed value: a pinned image's
        # name lives in `repository` while the bump changes `tag` beside it.
        if name in c.file or name in new_files[c.file]:
            return f"{name} is pinned by repo policy (see .claude/rules); triage may not change it"
    if not history_has_value(c.file, new):
        return (f"{new!r} never appears in {c.file}'s git history: renovate-breakage may only "
                f"revert to a known value")
    return None


def check(patch_text, cls, app_dir, namespace, metrics, *, read_file, list_files,
          history_has_value, denylist) -> GuardResult:
    if cls not in ("oom-memory-limit", "cpu-throttle-limit", "renovate-breakage"):
        return _fail(f"no diff rules exist for class {cls!r}")
    if not app_dir:
        return _fail("no app_dir was given")
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
        "oom-memory-limit": lambda: _oom(changes, metrics),
        "cpu-throttle-limit": lambda: _cpu(changes, metrics),
        "renovate-breakage": lambda: _renovate(changes, history_has_value, denylist, new_files),
    }
    reason = rules[cls]()
    if reason:
        return _fail(reason, changes, new_files)
    return GuardResult(True, "", changes, new_files)
