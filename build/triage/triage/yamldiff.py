"""Leaf-level diff of two YAML documents (multi-doc aware)."""
from dataclasses import dataclass

import yaml


class _Missing:
    def __repr__(self):
        return "MISSING"


MISSING = _Missing()


@dataclass(frozen=True)
class Change:
    path: tuple          # (doc key, key, key, ...); list indexes are strings
    old: object
    new: object


def load_docs(text: str) -> list:
    return [d for d in yaml.safe_load_all(text) if d is not None]


def _doc_key(doc, index: int) -> str:
    if isinstance(doc, dict):
        meta = doc.get("metadata") or {}
        return f"{doc.get('kind', '?')}/{meta.get('name', '?') if isinstance(meta, dict) else '?'}"
    return f"doc{index}"


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
    old = {_doc_key(d, i): d for i, d in enumerate(load_docs(old_text))}
    new = {_doc_key(d, i): d for i, d in enumerate(load_docs(new_text))}
    out = []
    for key in sorted(set(old) | set(new)):
        _walk((key,), old.get(key, MISSING), new.get(key, MISSING), out)
    return out
