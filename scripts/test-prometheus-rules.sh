#!/usr/bin/env bash
# Validates every PrometheusRule in clusters/ with promtool, then runs the unit tests in
# scripts/prometheus-rule-tests/. Needs promtool and python3 with PyYAML.
#
# Nothing else in CI parses PromQL: kubeconform checks the CRD shape, and the first
# real parse is prometheus-operator's admission webhook at Flux apply time, where one
# rejected object fails the whole Kustomization's dry-run.
#
# Each PrometheusRule's spec is written to <work>/rules/<namespace>-<name>.yaml, so a
# test file names the rules it needs as `rule_files: [rules/<namespace>-<name>.yaml]`.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/rules"

python3 - "$root/clusters" "$work/rules" <<'PY'
import pathlib, sys, yaml
src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
n = 0
for f in sorted(src.rglob("*.yaml")):
    try:
        docs = list(yaml.safe_load_all(f.read_text()))
    except yaml.YAMLError:
        continue  # not ours to judge; the YAML syntax step reports it
    for d in docs:
        if isinstance(d, dict) and d.get("kind") == "PrometheusRule":
            m = d["metadata"]
            name = "%s-%s.yaml" % (m.get("namespace", "default"), m["name"])
            (out / name).write_text(yaml.safe_dump({"groups": d["spec"]["groups"]}, sort_keys=False))
            n += 1
print("extracted %d PrometheusRules" % n)
PY

promtool check rules "$work"/rules/*.yaml
cp "$root"/scripts/prometheus-rule-tests/*.yaml "$work"/
promtool test rules "$work"/*.yaml
