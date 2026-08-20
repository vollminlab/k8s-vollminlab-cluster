#!/usr/bin/env python3
"""Validate each HelmRelease's REAL values against its chart's values.schema.json.

Why this exists
---------------
This replaces the `tarioch/flux-check-hook` pre-commit hook, which built its values
file from `spec.values` -- inline values only. This repo forbids inline values
(.claude/rules/flux.md); all 52 HelmReleases use `valuesFrom` pointing at a ConfigMap.
So the old hook wrote an EMPTY values file, linted each chart against its own defaults,
and printed "Passed" for every change. It never validated a single value here, and on
2026-08-19 it gave a false green on a values-schema error that CI then caught.

`helm lint` does validate values.schema.json -- it just has to be handed the real
values. That is the whole fix.

Usage
-----
    scripts/check-helm-values.py [helmrelease.yaml ...]

With no arguments, checks every HelmRelease in the repo. pre-commit passes changed
files; a file that is not a HelmRelease is ignored, EXCEPT that a changed
configmap.yaml is mapped to the HelmRelease beside it -- that is where the values
actually live, and not doing so was the old hook's second blind spot.

Exit codes: 0 all good, 1 a chart rejected its values or a resolution failed.
"""
import glob
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("check-helm-values: PyYAML is required (pip install pyyaml)")

REPO = Path(__file__).resolve().parent.parent
CLUSTER = REPO / "clusters" / "vollminlab-cluster"

errors: list[str] = []
notes: list[str] = []
linted = 0
skipped = 0


def load_all(path):
    try:
        with open(path) as fh:
            return [d for d in yaml.safe_load_all(fh) if d]
    except Exception as exc:
        errors.append(f"{path}: cannot parse YAML -- {exc}")
        return []


def build_source_index():
    """name -> (kind, url, tag) for every HelmRepository / OCIRepository in the repo."""
    index = {}
    for path in glob.glob(str(CLUSTER / "flux-system" / "repositories" / "*.yaml")):
        for doc in load_all(path):
            kind = doc.get("kind")
            if kind not in ("HelmRepository", "OCIRepository"):
                continue
            spec = doc.get("spec", {})
            index[doc["metadata"]["name"]] = (
                kind,
                spec.get("url"),
                (spec.get("ref") or {}).get("tag"),
            )
    return index


def deep_merge(base, over):
    """Merge `over` onto `base`, matching Helm's precedence for later valuesFrom."""
    if not isinstance(base, dict) or not isinstance(over, dict):
        return over
    out = dict(base)
    for key, val in over.items():
        out[key] = deep_merge(out.get(key), val) if key in out else val
    return out


def resolve_values(hr, hr_path):
    """Merge every valuesFrom ConfigMap, in order, then any inline spec.values.

    Returns (values, secret_refs_skipped). Secret-backed values live in 1Password via
    ESO and are not in git, so they cannot be resolved here -- reported, never guessed.
    """
    spec = hr.get("spec", {})
    values = {}
    secrets = []
    for ref in spec.get("valuesFrom", []) or []:
        if ref.get("kind") == "Secret":
            secrets.append(ref.get("name"))
            continue
        if ref.get("kind") != "ConfigMap":
            notes.append(f"{hr_path}: unhandled valuesFrom kind {ref.get('kind')!r}")
            continue
        name = ref.get("name")
        key = ref.get("valuesKey", "values.yaml")
        cm = find_configmap(name, hr_path)
        if cm is None:
            errors.append(f"{hr_path}: valuesFrom ConfigMap {name!r} not found in repo")
            continue
        raw = (cm.get("data") or {}).get(key)
        if raw is None:
            errors.append(f"{hr_path}: ConfigMap {name!r} has no key {key!r}")
            continue
        try:
            values = deep_merge(values, yaml.safe_load(raw) or {})
        except Exception as exc:
            errors.append(f"{hr_path}: ConfigMap {name!r} key {key!r} is not valid YAML -- {exc}")
    if "values" in spec:
        values = deep_merge(values, spec["values"] or {})
    return values, secrets


_cm_cache = None


def find_configmap(name, hr_path):
    """Prefer a ConfigMap in the HelmRelease's own directory, else search the cluster tree."""
    global _cm_cache
    local = Path(hr_path).parent
    for path in sorted(local.glob("*.yaml")):
        for doc in load_all(path):
            if doc.get("kind") == "ConfigMap" and doc.get("metadata", {}).get("name") == name:
                return doc
    if _cm_cache is None:
        _cm_cache = {}
        for path in glob.glob(str(CLUSTER / "**" / "*.yaml"), recursive=True):
            for doc in load_all(path):
                if doc.get("kind") == "ConfigMap":
                    _cm_cache.setdefault(doc.get("metadata", {}).get("name"), doc)
    return _cm_cache.get(name)


def chart_ref(hr, hr_path, sources):
    """Return (pull_args, label) for `helm pull`, or None if unresolvable."""
    spec = hr.get("spec", {})
    if "chartRef" in spec:  # OCIRepository: version lives on the repo CR, not the HR
        name = spec["chartRef"].get("name")
        entry = sources.get(name)
        if not entry:
            errors.append(f"{hr_path}: chartRef {name!r} has no matching repository CR")
            return None
        kind, url, tag = entry
        if not tag:
            errors.append(f"{hr_path}: OCIRepository {name!r} has no spec.ref.tag")
            return None
        return ([url, "--version", tag], f"{url}:{tag}")
    chart = spec.get("chart", {}).get("spec", {})
    ref = chart.get("sourceRef", {})
    entry = sources.get(ref.get("name"))
    if not entry:
        errors.append(f"{hr_path}: sourceRef {ref.get('name')!r} has no matching repository CR")
        return None
    _kind, url, _tag = entry
    return (
        ["--repo", url, "--version", chart["version"], chart["chart"]],
        f"{chart['chart']}@{chart['version']}",
    )


def check(hr_path, sources, cache):
    global linted, skipped
    for hr in load_all(hr_path):
        if hr.get("kind") != "HelmRelease":
            continue
        name = hr.get("metadata", {}).get("name", "?")
        ref = chart_ref(hr, hr_path, sources)
        if ref is None:
            continue
        pull_args, label = ref
        values, secrets = resolve_values(hr, hr_path)

        # The old hook's exact failure mode: a HelmRelease that declares valuesFrom but
        # resolves to nothing means the resolver is broken. Never let that read as a pass.
        if not values and (hr.get("spec", {}).get("valuesFrom") and not secrets):
            errors.append(
                f"{hr_path}: {name} declares valuesFrom but resolved to EMPTY values -- "
                f"refusing to lint against chart defaults and call it a pass"
            )
            continue
        if secrets:
            notes.append(f"{name}: Secret-backed values not resolvable from git ({', '.join(secrets)})")

        with tempfile.TemporaryDirectory() as tmp:
            tgz = cache.get(label)
            if tgz is None:
                res = subprocess.run(["helm", "pull", *pull_args, "--destination", tmp],
                                     capture_output=True, text=True)
                if res.returncode != 0:
                    errors.append(f"{hr_path}: helm pull failed for {label}\n{res.stdout}{res.stderr}")
                    continue
                found = list(Path(tmp).glob("*.tgz"))
                if not found:
                    errors.append(f"{hr_path}: helm pull produced no chart for {label}")
                    continue
                tgz = str(Path(CACHE_DIR) / found[0].name)
                os.replace(found[0], tgz)
                cache[label] = tgz
            vfile = Path(tmp) / "values.yaml"
            vfile.write_text(yaml.safe_dump(values))
            res = subprocess.run(["helm", "lint", "-f", str(vfile), tgz],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                errors.append(f"{name} ({hr_path}) values rejected by {label}:\n{res.stdout}{res.stderr}")
            else:
                linted += 1
                print(f"  ok  {name:34s} {label}")


CACHE_DIR = tempfile.mkdtemp(prefix="helm-values-check-")


def main():
    args = sys.argv[1:]
    targets = set()
    if args:
        for arg in args:
            p = Path(arg)
            if p.name == "helmrelease.yaml":
                targets.add(str(p))
            else:
                # values live in configmap.yaml -- map it back to its HelmRelease.
                sibling = p.parent / "helmrelease.yaml"
                if sibling.exists():
                    targets.add(str(sibling))
    else:
        targets = set(glob.glob(str(CLUSTER / "**" / "helmrelease.yaml"), recursive=True))

    if not targets:
        print("check-helm-values: no HelmRelease affected by these files, nothing to do")
        return 0

    sources = build_source_index()
    if not sources:
        print("check-helm-values: found no HelmRepository/OCIRepository CRs -- "
              "the repository layout must have moved; refusing to report success", file=sys.stderr)
        return 1

    cache: dict = {}
    for path in sorted(targets):
        check(path, sources, cache)

    for note in notes:
        print(f"  note  {note}")
    if errors:
        print()
        for err in errors:
            print(f"[ERROR] {err}")
        print(f"\ncheck-helm-values: {linted} chart(s) validated, {len(errors)} problem(s)")
        return 1
    print(f"check-helm-values: {linted} chart(s) validated against real values, 0 problems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
