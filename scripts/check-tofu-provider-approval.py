#!/usr/bin/env python3
"""Refuse a provider version change on a Terraform module that auto-applies.

Why this exists
---------------
Every Terraform CR here runs `approvePlan: auto` on a 10m interval, so merging a
PR *is* the apply. That is the right behaviour for routine config -- adding a
radarr indexer should not need an approval dance -- but a provider version bump
is a different class of change wearing the same clothes: it can rewrite or
destroy resources the diff never mentions.

CI cannot tell you what it would do. `tofu validate` confirms the configuration
parses; only `tofu plan` shows the effect, and CI does not run one because that
needs each module's provider credentials.

So this guard does not try to judge the change. It enforces that a provider bump
cannot land on a module that would apply it unreviewed: set that module's
`approvePlan` to "" first, let the controller plan, read it, then approve.

Usage
-----
    scripts/check-tofu-provider-approval.py [changed-file ...]

With no arguments, diffs against origin/main. Exit 0 clean, 1 blocked.
"""
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("check-tofu-provider-approval: PyYAML is required (pip install pyyaml)")

REPO = Path(__file__).resolve().parent.parent
CR_GLOB = "clusters/vollminlab-cluster/tofu/*/app/terraform-cr.yaml"

# A provider version can only change through one of these.
VERSION_FILES = {"versions.tf", ".terraform.lock.hcl"}


def changed_files(args):
    if args:
        return [a for a in args if a.strip()]
    for base in ("origin/main", "main"):
        res = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                             cwd=REPO, capture_output=True, text=True)
        if res.returncode == 0:
            return [l for l in res.stdout.splitlines() if l.strip()]
    sys.exit("check-tofu-provider-approval: cannot determine changed files")


def module_of(path):
    """terraform/<module>/versions.tf -> <module>, else None."""
    parts = Path(path).parts
    if len(parts) >= 3 and parts[0] == "terraform" and parts[-1] in VERSION_FILES:
        return parts[1]
    return None


def load_crs():
    """spec.path -> (approvePlan, cr file). Keyed on path because that is what
    actually binds a CR to a module directory; the CR's own name is convention."""
    crs = {}
    for cr_path in sorted(REPO.glob(CR_GLOB)):
        try:
            docs = [d for d in yaml.safe_load_all(open(cr_path)) if d]
        except Exception as exc:
            sys.exit(f"check-tofu-provider-approval: cannot parse {cr_path}: {exc}")
        for doc in docs:
            if doc.get("kind") != "Terraform":
                continue
            spec = doc.get("spec", {})
            path = (spec.get("path") or "").rstrip("/")
            if path.startswith("./"):
                path = path[2:]
            crs[path] = (spec.get("approvePlan"), cr_path.relative_to(REPO))
    return crs


def main():
    files = changed_files(sys.argv[1:])
    touched = sorted({m for m in (module_of(f) for f in files) if m})

    if not touched:
        print("check-tofu-provider-approval: no provider version files changed, nothing to check")
        return 0

    crs = load_crs()
    if not crs:
        # Never pass because the lookup came back empty -- that means the layout moved.
        print("check-tofu-provider-approval: found no Terraform CRs at "
              f"{CR_GLOB} -- refusing to report success", file=sys.stderr)
        return 1

    blocked, unmanaged = [], []
    for module in touched:
        key = f"terraform/{module}"
        if key not in crs:
            unmanaged.append(module)
            continue
        approve, cr_file = crs[key]
        if approve == "auto":
            blocked.append((module, cr_file))
        else:
            print(f"  ok  terraform/{module}: approvePlan={approve!r}, plan will be held for review")

    for module in unmanaged:
        print(f"  note  terraform/{module}: no Terraform CR applies this module, "
              f"so nothing auto-applies it")

    if blocked:
        print()
        print("[BLOCKED] provider version change on a module that auto-applies:")
        for module, cr_file in blocked:
            print(f"    terraform/{module}  ->  {cr_file}  has approvePlan: auto")
        print()
        print("  Merging this would apply the new provider within the 10m interval with")
        print("  no plan reviewed. A provider bump can rewrite or destroy resources the")
        print("  diff never mentions, and CI cannot show you which -- it runs")
        print("  `tofu validate`, never `tofu plan`.")
        print()
        print("  To proceed:")
        print("    1. set that module's approvePlan to \"\" in its terraform-cr.yaml and merge that")
        print("    2. merge this PR -- the controller now plans instead of applying")
        print("    3. read .status.plan.pending and review the plan")
        print("    4. approve by setting approvePlan to that plan id")
        print()
        print(f"check-tofu-provider-approval: {len(blocked)} module(s) blocked")
        return 1

    print(f"check-tofu-provider-approval: {len(touched)} module(s) checked, 0 blocked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
