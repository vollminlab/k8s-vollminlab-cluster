#!/usr/bin/env bash
#
# Doc currency checks.
#
# Runs unconditionally in CI on every PR, with no tooling and no cluster access.
# It only compares directories on disk to the text of two documents.
#
# WHY IT RUNS UNCONDITIONALLY: the drift this exists to catch comes from the
# *other* file changing. Plex was deleted from clusters/ on 2026-05-09 and
# docs/roadmap.md went on describing it as running for 3.5 months. A check
# path-filtered to the docs would never have fired — it would have been inert in
# exactly the case it exists for, which is the same failure as a Kyverno policy
# whose foreach.list resolves to null and reports zero fails because it evaluates
# nothing.
#
# WHAT THIS CANNOT CATCH: prose. Four of the six stale statuses found in the
# 2026-08-22 audit were sentences, not status fields — "deployed alongside Plex",
# a section titled "Tautulli / Plex Metrics Dashboard", "SealedSecret changes
# trigger restarts". No string comparison finds those. Check 3 (staleness) is the
# only defence there, and it is a prompt for a human to re-read, not a test.
#
# Deliberately exact, with no heuristics. A doc check that guesses produces false
# failures, gets bypassed, and then reports green while measuring nothing.

set -euo pipefail

cd "$(dirname "$0")/.."

REFERENCE="docs/cluster-reference.md"
ROADMAP="docs/roadmap.md"
EXEMPT="docs/.cluster-reference-exempt"
RETIRED="docs/.retired-components"

STALE_WARN_DAYS=90
STALE_FAIL_DAYS=180

fail=0
warn=0

err()  { printf '::error::%s\n' "$1"; fail=1; }
note() { printf '::warning::%s\n' "$1"; warn=1; }

# ---------------------------------------------------------------------------
# Check 1 — every app directory is documented in the configuration reference
#
# CLAUDE.md calls cluster-reference.md the "Full configuration reference".
# On 2026-08-22 it was missing 46 of 93 app directories — half the cluster was
# absent from the document every session is pointed at.
#
# The exemption file is a baseline, not an allowlist: it was seeded with those
# 46 so this check passes on the day it lands and fails for anything NEW. It is
# meant to shrink to empty. Do not add to it to silence a failure on a new app —
# document the app instead.
# ---------------------------------------------------------------------------
[[ -f "$REFERENCE" ]] || { err "$REFERENCE is missing"; exit 1; }

undocumented=()
while IFS= read -r dir; do
    name="$(basename "$dir")"
    case "$name" in
        networkpolicies|repositories|flux-kustomizations|secrets|pvcs) continue ;;
    esac
    grep -qiF -- "$name" "$REFERENCE" && continue
    grep -qxF -- "$name" "$EXEMPT" 2>/dev/null && continue
    undocumented+=("$name")
done < <(find clusters -mindepth 3 -maxdepth 3 -type d ! -name app | sort)

if (( ${#undocumented[@]} > 0 )); then
    err "app directories absent from $REFERENCE: ${undocumented[*]}"
    err "add each to $REFERENCE in this PR — see .claude/rules/docs.md"
fi

# ---------------------------------------------------------------------------
# Check 2 — retired components stay retired
#
# Two directions, both exact:
#   a) a retired component must not reappear as a live app directory
#   b) a retired component must not be described in the configuration reference,
#      which documents what IS running
#
# The roadmap is deliberately NOT checked here. It is a historical record as well
# as a plan, and it should be free to describe the Plex era in the past tense.
# ---------------------------------------------------------------------------
if [[ -f "$RETIRED" ]]; then
    while IFS= read -r name; do
        [[ -z "$name" || "$name" == \#* ]] && continue
        if find clusters -mindepth 3 -maxdepth 3 -type d -name "$name" | grep -q .; then
            err "'$name' is listed in $RETIRED but a live app directory exists for it"
        fi
        # Only TABLE ROWS are checked, not prose. The tables in this document are
        # inventory — they assert what exists — so a retired component appearing in
        # one is a factual error. Prose may legitimately discuss history, e.g. the
        # note that bootstrap/sealed-secrets/ is kept as historical DR reference.
        if grep -niF -- "$name" "$REFERENCE" | grep -qE '^[0-9]+:\s*\|'; then
            err "'$name' is retired but still listed in an inventory table in $REFERENCE:"
            grep -niF -- "$name" "$REFERENCE" | grep -E '^[0-9]+:\s*\|' | sed 's/^/    /'
        fi
    done < "$RETIRED"
fi

# ---------------------------------------------------------------------------
# Check 3 — the roadmap has been re-read against reality recently
#
# The only defence against prose drift. Not a test — a prompt to go and look.
# ---------------------------------------------------------------------------
if [[ -f "$ROADMAP" ]]; then
    verified="$(grep -oiE 'Last verified against the live cluster: [0-9]{4}-[0-9]{2}-[0-9]{2}' "$ROADMAP" \
                | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -1 || true)"
    if [[ -z "$verified" ]]; then
        err "$ROADMAP has no 'Last verified against the live cluster: YYYY-MM-DD' line"
    else
        age=$(( ( $(date -u +%s) - $(date -u -d "$verified" +%s) ) / 86400 ))
        if   (( age >= STALE_FAIL_DAYS )); then
            err "$ROADMAP was last verified $age days ago (>= $STALE_FAIL_DAYS). Re-read it against the live cluster and update the date."
        elif (( age >= STALE_WARN_DAYS )); then
            note "$ROADMAP was last verified $age days ago (>= $STALE_WARN_DAYS). Worth a pass soon."
        fi
    fi
fi

if (( fail )); then
    echo "doc currency checks FAILED"
    exit 1
fi
(( warn )) && echo "doc currency checks passed with warnings"
echo "doc currency checks passed"
