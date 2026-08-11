#!/usr/bin/env bash
#
# Fails when the namespace directories under clusters/vollminlab-cluster/ and the
# "Repository Structure" block in README.md disagree.
#
# Why this exists: the README went stale for four months and nothing noticed. The
# expensive part wasn't the drift itself, it was that a DR reader had no way to
# tell. Versions are handled by pointing at self-maintaining sources instead of
# copying them; structure can't be, so it gets checked here. This fires on the PR
# that adds or removes a namespace — the moment a human is already editing the
# repo layout and should be editing the doc with it.
#
# Usage: scripts/check-readme-structure.sh [path/to/README.md]
# Exits 0 when the two agree, 1 otherwise.

set -euo pipefail

README="${1:-README.md}"
CLUSTER_DIR="clusters/vollminlab-cluster"

[[ -f "$README" ]] || { echo "❌ README not found: $README"; exit 1; }
[[ -d "$CLUSTER_DIR" ]] || { echo "❌ Cluster dir not found: $CLUSTER_DIR"; exit 1; }

# What exists on disk
actual=$(find "$CLUSTER_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)

# What the README documents. The block is a fenced tree; take every line indented
# exactly two spaces between the "clusters/vollminlab-cluster/" line and the next
# unindented line (blank lines and deeper-indented sub-entries are ignored).
# The trailing `|| true` matters: grep exits 1 on no match, which under
# `set -euo pipefail` would kill the script before the empty-block guard below
# could explain what went wrong.
documented=$(
  {
    sed -n '\#^clusters/vollminlab-cluster/#,/^[^[:space:]]/p' "$README" \
      | grep -oE '^  [A-Za-z0-9._-]+/' \
      | tr -d ' /' \
      | sort
  } || true
)

# A renamed heading or deleted block must fail loudly, not pass vacuously.
if [[ -z "$documented" ]]; then
  echo "❌ Could not find the 'clusters/vollminlab-cluster/' tree in $README."
  echo "   The Repository Structure block is what this check validates against —"
  echo "   if it moved or was reformatted, update this script too."
  exit 1
fi

missing=$(comm -23 <(printf '%s\n' "$actual") <(printf '%s\n' "$documented"))
extra=$(comm -13 <(printf '%s\n' "$actual") <(printf '%s\n' "$documented"))

if [[ -z "$missing" && -z "$extra" ]]; then
  echo "✅ README structure block matches all $(printf '%s\n' "$actual" | wc -l) namespace directories"
  exit 0
fi

echo "❌ README.md is out of sync with $CLUSTER_DIR/"
echo

if [[ -n "$missing" ]]; then
  echo "   Directories that exist but are NOT in the README:"
  printf '     %s/\n' $missing
  echo
fi

if [[ -n "$extra" ]]; then
  echo "   Documented in the README but NO LONGER on disk:"
  printf '     %s/\n' $extra
  echo
fi

echo "   Fix: edit the 'Repository Structure' block in $README so it lists each"
echo "   namespace directory once, indented two spaces, with a short description."
exit 1
