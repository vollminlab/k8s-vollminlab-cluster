#!/bin/sh
# Unit tests for heal.sh. Run: sh heal_test.sh
# Sources heal.sh with HEAL_TEST=1 so main() does not run, then stubs kc,
# pvb_rows, heal_node and date per-test.
set -u
HERE=$(dirname "$0")
HEAL_TEST=1 . "$HERE/heal.sh"

FAILS=0
assert_eq() { # $1=actual $2=expected $3=msg
  if [ "$1" = "$2" ]; then printf 'ok   - %s\n' "$3"
  else printf 'FAIL - %s (got [%s] want [%s])\n' "$3" "$1" "$2"; FAILS=$((FAILS+1)); fi
}
assert_rc() { # $1=actual_rc $2=expected_rc $3=msg
  if [ "$1" = "$2" ]; then printf 'ok   - %s\n' "$3"
  else printf 'FAIL - %s (rc got [%s] want [%s])\n' "$3" "$1" "$2"; FAILS=$((FAILS+1)); fi
}

# --- strip_zero ---
assert_eq "$(strip_zero 08)" "8" "strip_zero drops the octal-poisoning zero"
assert_eq "$(strip_zero 00)" "0" "strip_zero of 00 is 0"
assert_eq "$(strip_zero 2026)" "2026" "strip_zero leaves a 4-digit year alone"

# --- rfc3339_to_epoch (cross-checked against `date -u -d <ts> +%s`) ---
assert_eq "$(rfc3339_to_epoch 1970-01-01T00:00:00Z)" "0" "epoch zero"
assert_eq "$(rfc3339_to_epoch 2026-01-01T00:00:00Z)" "1767225600" "new year 2026"
assert_eq "$(rfc3339_to_epoch 2026-08-11T03:02:59Z)" "1786417379" "the stall that started this"
assert_eq "$(rfc3339_to_epoch 2024-02-29T12:00:00Z)" "1709208000" "leap day"
assert_eq "$(rfc3339_to_epoch 2026-08-11T03:02:59.123456Z)" "1786417379" "fractional seconds ignored"
rfc3339_to_epoch "<none>"; assert_rc "$?" "1" "rejects <none>"
rfc3339_to_epoch ""; assert_rc "$?" "1" "rejects empty"

# --- in_cooldown ---
in_cooldown "100" "150" "60"; assert_rc "$?" "0" "in_cooldown true when delta<cooldown"
in_cooldown "100" "200" "60"; assert_rc "$?" "1" "in_cooldown false when delta>cooldown"
in_cooldown "" "200" "60";    assert_rc "$?" "1" "in_cooldown false when never healed"

# --- busy_nodes / node_is_busy ---
ROWS_MIXED='pvb-a   Prepared     k8sworker03   2026-08-11T02:40:00Z
pvb-b   InProgress   k8sworker03   2026-08-11T02:55:00Z
pvb-c   Completed    k8sworker01   2026-08-11T02:10:00Z'
assert_eq "$(busy_nodes "$ROWS_MIXED")" "k8sworker03" "busy_nodes finds the InProgress node"
node_is_busy k8sworker03 "$(busy_nodes "$ROWS_MIXED")"; assert_rc "$?" "0" "node_is_busy true for the busy node"
node_is_busy k8sworker01 "$(busy_nodes "$ROWS_MIXED")"; assert_rc "$?" "1" "node_is_busy false for an idle node"

# --- find_and_heal_one: stub the clock, the row source and the action ---
# 2026-08-11T03:02:59Z. Fixtures are dated relative to it:
#   02:40:00Z -> 1379s old (stalled, > STALL_SECONDS=900)
#   03:00:00Z ->  179s old (fresh)
NOW_EPOCH=1786417379
date() {
  case "$*" in
    "-u +%s") echo "$NOW_EPOCH" ;;
    *) echo "2026-08-11T03:02:59Z" ;;
  esac
}

HEALED=""
heal_node() { HEALED="$1 $2 $3"; }

LAST_HEALED_COOL=""
kc() {
  case "$*" in
    *pvb-cool*annotations*) echo "$LAST_HEALED_COOL" ;;
    *) echo "" ;;
  esac
}

run_case() { # $1=rows -> sets HEALED
  HEALED=""
  # via a global, not "$1": inside pvb_rows, $1 would be pvb_rows' own argument.
  ROWS="$1"
  pvb_rows() { echo "$ROWS"; }
  find_and_heal_one >/dev/null
}

# 1. Stalled Prepared, nothing running on that node -> the leak. Heal it.
run_case 'pvb-a   Prepared   k8sworker03   2026-08-11T02:40:00Z'
assert_eq "$HEALED" "k8sworker03 pvb-a 1379" "stalled Prepared on an idle node is healed"

# 2. Same PVB, but a sibling is genuinely running on that node. With
#    loadConcurrency=1 that is an ordinary queue wait -- killing node-agent here
#    would abort a healthy in-flight backup. This guard is the whole ballgame.
run_case "$ROWS_MIXED"
assert_eq "$HEALED" "" "Prepared behind an InProgress PVB on the same node is left alone"

# 3. Prepared but only 179s old -> still inside normal setup time.
run_case 'pvb-a   Prepared   k8sworker03   2026-08-11T03:00:00Z'
assert_eq "$HEALED" "" "young Prepared PVB is left alone"

# 4. Stalled, idle node, but healed 10 minutes ago -> cooldown holds, so a PVB
#    that is broken for some other reason cannot loop node-agent every 10 min.
LAST_HEALED_COOL=$((NOW_EPOCH - 600))
run_case 'pvb-cool   Prepared   k8sworker03   2026-08-11T02:40:00Z'
assert_eq "$HEALED" "" "cooldown suppresses a repeat heal"

# 5. Same, but the cooldown has expired.
LAST_HEALED_COOL=$((NOW_EPOCH - 7200))
run_case 'pvb-cool   Prepared   k8sworker03   2026-08-11T02:40:00Z'
assert_eq "$HEALED" "k8sworker03 pvb-cool 1379" "expired cooldown allows a heal"

# 6. Other phases are never touched.
run_case 'pvb-x   Completed   k8sworker03   2026-08-11T02:40:00Z
pvb-y   Canceled    k8sworker02   2026-08-11T02:40:00Z
pvb-z   Accepted    k8sworker01   2026-08-11T02:40:00Z'
assert_eq "$HEALED" "" "Completed/Canceled/Accepted PVBs are ignored"

# 7. Unscheduled PVB (no node yet) is ignored.
run_case 'pvb-n   Prepared   <none>   2026-08-11T02:40:00Z'
assert_eq "$HEALED" "" "PVB without a node is ignored"

if [ "$FAILS" -gt 0 ]; then printf '\n%s test(s) failed\n' "$FAILS"; exit 1; fi
printf '\nall tests passed\n'
