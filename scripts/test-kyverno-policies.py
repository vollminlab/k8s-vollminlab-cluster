#!/usr/bin/env python3
"""Run the Kyverno policy tests in tests/kyverno/ and refuse to pass vacuously.

Why this exists
---------------
Until 2026-08-19 three ClusterPolicies here were inert: their foreach.list was not
rooted at request.object, resolved to null, and the rule emitted no result at all
(#1104, #1109). Nothing noticed, because there were no policy tests.

`kyverno test` on its own would not have noticed either. Run against an inert copy
of require-resources, it reports every row as "Pass", including the rows that
expected a FAIL, with the reason "Excluded" -- a rule that produced no result
satisfies any expectation. Measured with CLI v1.19.0 on 2026-10-27. A genuine skip
also reports "Excluded", so the CLI cannot tell "correctly skipped" from "evaluated
nothing".

So this wrapper adds the three checks that make the tests mean something:

1. An "Excluded" row is a failure unless the test declared that row `result: skip`.
2. Every hand-written rule needs at least one row with reason "Ok", i.e. a result the
   engine actually produced. A validate rule needs an Ok pass AND an Ok fail: a rule
   that can only admit, or only reject, is broken. An unconditional `deny: {}` needs
   only the Ok fail. A mutate rule needs an Ok pass with patchedResources.
3. Every ClusterPolicy/Policy under clusters/**/kyverno/*/policies/ has a test.

Fixture names must be unique within a test: the CLI matches expected results by
namespace/name and ignores `kind`, so a Pod and a Deployment that share a name each
pick up the other's expectations.

Usage: scripts/test-kyverno-policies.py [kyverno-binary]   (default: kyverno)
Exit codes: 0 all good, 1 a test failed or a check above did not hold.
"""
import json
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("test-kyverno-policies: PyYAML is required (scripts/ci-ensure-pyyaml.sh)")

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests" / "kyverno"
POLICY_GLOB = "clusters/vollminlab-cluster/kyverno/*/policies/*.yaml"


def load_policies():
    """{policy name: {rule name: kind}} for every policy in the repo.

    kind is "validate", "deny-all" (an unconditional `deny: {}`, which can only ever
    fail, so it has no pass case to test) or "mutate".
    """
    policies = {}
    for path in sorted(REPO.glob(POLICY_GLOB)):
        for doc in yaml.safe_load_all(path.read_text()):
            if not doc or doc.get("kind") not in ("ClusterPolicy", "Policy"):
                continue
            rules = {}
            for rule in doc["spec"]["rules"]:
                validate = rule.get("validate")
                if validate is None:
                    rules[rule["name"]] = "mutate"
                elif validate.get("deny") == {}:
                    rules[rule["name"]] = "deny-all"
                else:
                    rules[rule["name"]] = "validate"
            policies[doc["metadata"]["name"]] = rules
    return policies


def expectations(test_file):
    """{(policy, rule, "ns/name"): expected result} as declared in kyverno-test.yaml."""
    want = {}
    for r in yaml.safe_load(test_file.read_text()).get("results", []):
        for res in r["resources"]:
            want[(r["policy"], r["rule"], res)] = r["result"]
    return want


def resource_key(row_resource):
    # "apps/v1/Deployment/ns/name", "v1/Pod/ns/name", "v1/Namespace//name"
    parts = row_resource.split("/")
    ns, name = parts[-2], parts[-1]
    return f"{ns}/{name}" if ns else name


def run(kyverno, test_dir):
    proc = subprocess.run([kyverno, "test", str(test_dir), "-o", "json", "--remove-color"],
                          capture_output=True, text=True)
    out = proc.stdout
    start, end = out.find("\n["), out.rfind("]")
    if start < 0 or end < 0:
        return proc.returncode, None, out + proc.stderr
    return proc.returncode, json.loads(out[start:end + 1]), out


def main():
    kyverno = sys.argv[1] if len(sys.argv) > 1 else "kyverno"
    policies = load_policies()
    errors = []
    # {(policy, rule): set of expected results that produced an Ok row}
    evaluated = {}
    declared = set()   # policies some kyverno-test.yaml names

    test_files = sorted(TESTS.glob("*/kyverno-test.yaml"))
    if not test_files:
        sys.exit(f"no tests found under {TESTS.relative_to(REPO)}")

    for test_file in test_files:
        name = test_file.parent.name
        want = expectations(test_file)
        declared.update(policy for policy, _, _ in want)
        code, rows, raw = run(kyverno, test_file.parent)
        if rows is None:
            errors.append(f"{name}: kyverno test produced no results (exit {code}):\n{raw}")
            continue
        if code != 0:
            errors.append(f"{name}: kyverno test exited {code}")
        for row in rows:
            key = (row["POLICY"], row["RULE"], resource_key(row["RESOURCE"]))
            expected = want.get(key)
            where = f"{name}: {row['POLICY']}/{row['RULE']} on {row['RESOURCE']}"
            if row["RESULT"] != "Pass":
                errors.append(f"{where}: {row['RESULT']} ({row['REASON']})")
            elif row["REASON"] == "Excluded" and expected != "skip":
                errors.append(f"{where}: expected {expected} but the rule produced no result "
                              "(inert rule, or a fixture its match block never selects)")
            elif row["REASON"] == "Ok":
                evaluated.setdefault(key[:2], set()).add(expected)
        print(f"{'ok  ' if code == 0 else 'FAIL'} {name} ({len(rows)} results)")

    need_by_kind = {"validate": {"pass", "fail"}, "deny-all": {"fail"}, "mutate": {"pass"}}
    for policy, rules in sorted(policies.items()):
        if policy not in declared:
            errors.append(f"{policy}: no test in {TESTS.relative_to(REPO)}/")
            continue
        for rule, kind in sorted(rules.items()):
            got = evaluated.get((policy, rule), set())
            need = need_by_kind[kind]
            if not need <= got:
                errors.append(f"{policy}/{rule}: needs an evaluated {' and '.join(sorted(need))} "
                              f"case, has {sorted(got) or 'none'}")

    if errors:
        print(f"\n{len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"\nall {len(policies)} policies tested, every rule evaluated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
