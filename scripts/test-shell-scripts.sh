#!/usr/bin/env bash
#
# Run every clusters/**/*_test.sh under dash AND busybox ash.
#
# These are the unit tests for the shell CronJobs (healers, reaper, backup content
# guard, credential-age check). Each one stubs kubectl and needs no cluster. They
# existed for months without running anywhere but a laptop, which is how a CronJob
# can be merged, Ready and reconciled while failing on every run (velero-pvb-healer,
# #1055 -> #1056).
#
# Both shells, because the scripts run under busybox ash in their alpine-based images
# but are written and tested locally under dash, and the two differ in ways that bite
# (local, arithmetic, `read` behaviour).
#
# Discovery is by filename, so a new *_test.sh is picked up with no CI change.
#
# Usage: scripts/test-shell-scripts.sh            (busybox from PATH)
#        BUSYBOX=/path/to/busybox scripts/test-shell-scripts.sh
set -euo pipefail

cd "$(dirname "$0")/.."

BUSYBOX="${BUSYBOX:-busybox}"
command -v dash >/dev/null || { echo "dash not found" >&2; exit 1; }
command -v "$BUSYBOX" >/dev/null || { echo "busybox not found (set BUSYBOX=)" >&2; exit 1; }

mapfile -t tests < <(find clusters -name '*_test.sh' | sort)
if (( ${#tests[@]} == 0 )); then
  echo "no *_test.sh found under clusters/" >&2
  exit 1
fi

failed=()
for t in "${tests[@]}"; do
  for shell in dash "$BUSYBOX ash"; do
    # Each test sources its script relative to its own directory.
    if out=$(cd "$(dirname "$t")" && $shell "$(basename "$t")" 2>&1); then
      echo "ok    [$shell] $t"
    else
      echo "FAIL  [$shell] $t"
      printf '%s\n' "$out" | sed 's/^/      /'
      failed+=("[$shell] $t")
    fi
  done
done

if (( ${#failed[@]} )); then
  printf '\n%s failed:\n' "${#failed[@]}" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi
printf '\nall %s tests passed under dash and busybox ash\n' "${#tests[@]}"
