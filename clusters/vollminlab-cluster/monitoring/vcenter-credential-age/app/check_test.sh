#!/bin/sh
# Pure-shell tests for check.sh. No cluster, no credentials, no network.
# Must pass under BOTH dash and busybox ash (the image is alpine).
#   sh check_test.sh   /   dash check_test.sh   /   busybox ash check_test.sh
set -u
DIR=$(dirname "$0")
SCRIPT="$DIR/check.sh"
pass=0; fail=0

# 2026-08-18T00:00:00Z = 1787011200
run() { # desc, expected_rc, NOW_EPOCH, ROTATED_AT, [expect_substring]
  desc=$1; want=$2; now=$3; rot=$4; needle=${5:-}
  out=$(ACCOUNT=test ROTATED_AT="$rot" LIFETIME_DAYS=90 WARN_DAYS=14 NOW_EPOCH="$now" \
        sh "$SCRIPT" 2>&1); got=$?
  ok=1
  [ "$got" = "$want" ] || ok=0
  if [ -n "$needle" ]; then
    echo "$out" | grep -q "$needle" || ok=0
  fi
  if [ "$ok" = 1 ]; then pass=$((pass+1)); echo "  PASS  $desc"
  else fail=$((fail+1)); echo "  FAIL  $desc (rc=$got want=$want)"; echo "$out" | sed 's/^/        /'; fi
}

echo "check.sh tests"
# rotated today -> 90 days left -> OK
run "fresh rotation exits 0"            0 1787011200 2026-08-18 "days_left=90"
# 75 days later -> 15 left -> still above warn(14)
run "15 days left still OK"             0 $((1787011200 + 75*86400)) 2026-08-18 "days_left=15"
# 76 days later -> 14 left -> at threshold -> alert
run "14 days left alerts"               1 $((1787011200 + 76*86400)) 2026-08-18 "EXPIRING in 14 days"
# past expiry
run "expired alerts and says so"        1 $((1787011200 + 95*86400)) 2026-08-18 "EXPIRED 5 days ago"
# unparseable date -> rc 2, not a false OK
run "bad date exits 2 (not 0)"          2 1787011200 "not-a-date" "could not parse"

# missing env must fail loudly rather than default to OK
out=$(ACCOUNT=test LIFETIME_DAYS=90 WARN_DAYS=14 sh "$SCRIPT" 2>&1); rc=$?
if [ "$rc" != 0 ]; then pass=$((pass+1)); echo "  PASS  missing ROTATED_AT is fatal"
else fail=$((fail+1)); echo "  FAIL  missing ROTATED_AT returned 0"; fi

echo "passed=$pass failed=$fail"
[ "$fail" = 0 ]
