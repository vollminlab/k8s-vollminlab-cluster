#!/bin/sh
# Unit tests for heal.sh. Run: sh heal_test.sh
# Sources heal.sh with HEAL_TEST=1 so main() does not run, then stubs kc per-test.
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

# --- sum_ints ---
assert_eq "$(sum_ints 3 0 5)" "8" "sum_ints adds three ints"
assert_eq "$(sum_ints)" "0" "sum_ints of nothing is 0"
assert_eq "$(sum_ints 7)" "7" "sum_ints of one int"

# --- in_cooldown ---
in_cooldown "100" "150" "60"; assert_rc "$?" "0" "in_cooldown true when delta<cooldown"
in_cooldown "100" "200" "60"; assert_rc "$?" "1" "in_cooldown false when delta>cooldown"
in_cooldown "" "200" "60";    assert_rc "$?" "1" "in_cooldown false when no last-healed"

printf '\n%s failures\n' "$FAILS"
[ "$FAILS" = "0" ]
