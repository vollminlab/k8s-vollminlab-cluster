"""Gate 3: does a model-written patch stay inside what its class may change?

The model chooses the patch; this module decides whether it may become a PR on a
public repo. It never trusts the model's description of the patch, only the patch
applied to the real checkout and diffed field by field.
"""
import posixpath
import re
from dataclasses import dataclass, field

import yaml

from . import patching, yamldiff
from .yamldiff import MISSING

_QUANTITY = re.compile(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|k|M|G|T)?$")
_MULTIPLIER = {None: 1, "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
               "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
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
    field: str
    old: object
    new: object


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
        if not c.field.endswith(("resources.limits.memory", "resources.requests.memory")):
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
        if c.field.endswith("limits.memory"):
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
        if not c.field.endswith("resources.limits.cpu"):
            return f"{c.file}: {c.field} is not a CPU limit"
        if c.old is MISSING or c.new is not MISSING:
            return f"{c.file}: cpu-throttle-limit may only remove a CPU limit"
    return None


def _renovate(changes, history_has_value, denylist, new_files):
    if len(changes) != 1:
        return f"renovate-breakage must change exactly one value, not {len(changes)}"
    c = changes[0]
    if c.field.rsplit(".", 1)[-1] not in ("version", "tag", "image"):
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
    if not app_dir:
        return _fail("no app_dir was given")
    app_dir = app_dir.rstrip("/") + "/"
    try:
        file_patches = patching.parse(patch_text or "")
    except patching.PatchError as e:
        return _fail(f"unparseable patch: {e}")
    changes, new_files = [], {}
    for fp in file_patches:
        if fp.old_path is None or fp.new_path is None:
            return _fail("the patch adds or deletes a file")
        if fp.old_path != fp.new_path:
            return _fail(f"the patch renames {fp.old_path} to {fp.new_path}")
        path = posixpath.normpath(fp.new_path)
        if path != fp.new_path or not path.startswith(app_dir):
            return _fail(f"{fp.new_path} is outside the incident's app directory {app_dir}")
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
        changes += [FieldChange(path, c.path[0], ".".join(c.path[1:]), c.old, c.new) for c in diffs]
        new_files[path] = new
    if not changes:
        return _fail("the patch changes no YAML value")
    if not app_dir_in_namespace(app_dir, namespace, list_files, read_file):
        return _fail(f"{app_dir} declares nothing in namespace {namespace!r}, so it is not this incident's app")
    rules = {
        "oom-memory-limit": lambda: _oom(changes, metrics),
        "cpu-throttle-limit": lambda: _cpu(changes, metrics),
        "renovate-breakage": lambda: _renovate(changes, history_has_value, denylist, new_files),
    }
    if cls not in rules:
        return _fail(f"no diff rules exist for class {cls!r}")
    reason = rules[cls]()
    if reason:
        return _fail(reason, changes, new_files)
    return GuardResult(True, "", changes, new_files)
