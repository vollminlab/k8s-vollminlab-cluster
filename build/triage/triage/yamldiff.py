"""Leaf-level diff of two YAML documents (multi-doc aware)."""
from dataclasses import dataclass

import yaml


class _Missing:
    def __repr__(self):
        return "MISSING"


MISSING = _Missing()


class DiffError(Exception):
    """The two texts cannot be compared document for document."""


@dataclass(frozen=True)
class Change:
    path: tuple          # (doc key, key, key, ...); list indexes are strings
    old: object
    new: object


def load_docs(text: str) -> list:
    return [d for d in yaml.safe_load_all(text) if d is not None]


def doc_key(doc, index: int) -> tuple:
    """(apiVersion, kind, namespace, name) for a mapping; ("#doc", index) otherwise.

    The whole identity, not kind/name alone: two documents that differ only in
    namespace or apiVersion are different objects to Kubernetes, and keying them
    together would let one hide behind the other."""
    if isinstance(doc, dict):
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        return tuple(_ident(v) for v in (doc.get("apiVersion"), doc.get("kind"),
                                         meta.get("namespace"), meta.get("name")))
    return ("#doc", str(index))


def _ident(v):
    return v if v is None or isinstance(v, str) else repr(v)


def keyed_docs(text: str) -> dict:
    """{doc key: document}. Raises DiffError if two documents share a key."""
    out = {}
    for i, d in enumerate(yaml.safe_load_all(text)):
        key = doc_key(d, i)
        if key in out:
            raise DiffError(f"a document identity appears twice: {key}")
        out[key] = d
    return out


def _expand(value):
    """A multi-line string that parses as a mapping or list (Helm values in a
    ConfigMap) is compared structurally, so the guard sees the real field path."""
    if isinstance(value, str) and "\n" in value:
        try:
            parsed = yaml.safe_load(value)
        except yaml.YAMLError:
            return value
        if isinstance(parsed, (dict, list)):
            return parsed
    return value


def _walk(path, a, b, out):
    a, b = _expand(a), _expand(b)
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            _walk(path + (str(k),), a.get(k, MISSING), b.get(k, MISSING), out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _walk(path + (str(i),), x, y, out)
    elif a != b:
        out.append(Change(path, a, b))


def diff(old_text: str, new_text: str) -> list:
    """Leaf changes between two texts with the same documents.

    Raises DiffError if either text has two documents with one identity (twice),
    or if the patch adds or removes a document: only values inside existing
    documents are compared, so a new document must never be silently dropped."""
    old, new = keyed_docs(old_text), keyed_docs(new_text)
    if set(old) != set(new):
        added = sorted(map(str, set(new) - set(old)))
        removed = sorted(map(str, set(old) - set(new)))
        raise DiffError(f"the patch adds or removes a document (added {added}, removed {removed})")
    out = []
    for key in old:
        _walk((key,), old[key], new[key], out)
    return out
