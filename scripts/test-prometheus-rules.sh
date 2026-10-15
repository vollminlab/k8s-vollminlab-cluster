#!/usr/bin/env bash
# Validates every PrometheusRule in clusters/ with promtool, then runs the unit tests in
# scripts/prometheus-rule-tests/. Needs promtool and yq v4 (the CI runner has no PyYAML).
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

n=0
while IFS= read -r f; do
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    ns=${name%%/*} obj=${name#*/}
    NAME="$obj" yq e 'select(.kind == "PrometheusRule" and .metadata.name == strenv(NAME)) | {"groups": .spec.groups}' "$f" \
      > "$work/rules/$ns-$obj.yaml"
    n=$((n + 1))
  done < <(yq e 'select(.kind == "PrometheusRule") | (.metadata.namespace // "default") + "/" + .metadata.name' "$f")
done < <(grep -rlE --include='*.yaml' '^kind: PrometheusRule$' "$root/clusters" | sort)
echo "extracted $n PrometheusRules"
[ "$n" -gt 0 ] || { echo "no PrometheusRules found under clusters/" >&2; exit 1; }

promtool check rules "$work"/rules/*.yaml
cp "$root"/scripts/prometheus-rule-tests/*.yaml "$work"/
promtool test rules "$work"/*.yaml
