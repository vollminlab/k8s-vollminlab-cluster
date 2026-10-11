"""Strict unified-diff parsing and application. No fuzz, no offsets."""
import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")


class PatchError(Exception):
    pass


@dataclass
class FilePatch:
    old_path: object            # str, or None for /dev/null
    new_path: object
    hunks: list = field(default_factory=list)    # [(old_start, ["+x", "-y", " z"])]


def _path(header: str):
    p = header.split("\t")[0].strip()
    if p == "/dev/null":
        return None
    return p[2:] if p.startswith(("a/", "b/")) else p


def parse(text: str) -> list:
    lines = text.splitlines()
    files, cur, i = [], None, 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
                raise PatchError(f"line {i + 1}: '---' header without '+++'")
            cur = FilePatch(_path(line[4:]), _path(lines[i + 1][4:]))
            files.append(cur)
            i += 2
            continue
        m = _HUNK.match(line)
        if m:
            if cur is None:
                raise PatchError(f"line {i + 1}: hunk before any file header")
            body, i = [], i + 1
            while i < len(lines) and not lines[i].startswith(("@@", "--- ", "diff ")):
                l = lines[i]
                if l.startswith("\\"):           # "\ No newline at end of file"
                    i += 1
                    continue
                if l == "":
                    l = " "                       # some generators drop the space on blank context
                if l[0] not in " +-":
                    raise PatchError(f"line {i + 1}: unexpected {l[:20]!r} inside a hunk")
                body.append(l)
                i += 1
            cur.hunks.append((int(m.group(1)), body))
            continue
        i += 1                                    # "diff --git", "index ..." and similar
    if not files:
        raise PatchError("no '--- / +++' file headers found")
    for f in files:
        if not f.hunks:
            raise PatchError(f"{f.new_path or f.old_path}: header without hunks")
    return files


def apply(original: str, fp: FilePatch) -> str:
    src = original.splitlines()
    out, pos = [], 0
    for old_start, body in fp.hunks:
        start = max(old_start - 1, 0)
        if start < pos:
            raise PatchError(f"{fp.new_path}: overlapping hunks")
        out.extend(src[pos:start])
        pos = start
        for l in body:
            tag, text = l[0], l[1:]
            if tag in " -":
                if pos >= len(src) or src[pos] != text:
                    raise PatchError(f"{fp.new_path}: context mismatch at line {pos + 1}")
                if tag == " ":
                    out.append(text)
                pos += 1
            else:
                out.append(text)
    out.extend(src[pos:])
    return "\n".join(out) + ("\n" if original.endswith("\n") else "")
