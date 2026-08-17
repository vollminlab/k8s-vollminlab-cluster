#!/bin/sh
# Unit tests for guard.sh. Run: sh guard_test.sh
# Must pass under both dash and busybox ash (the image is alpine/kubectl).
#
# Sources guard.sh with GUARD_TEST=1 so main() does not run, then stubs the
# helper functions per test. No cluster required.
set -u
HERE=$(dirname "$0")
GUARD_TEST=1 . "$HERE/guard.sh"

FAILS=0
assert_eq() { # $1=actual $2=expected $3=msg
  if [ "$1" = "$2" ]; then printf 'ok   - %s\n' "$3"
  else printf 'FAIL - %s (got [%s] want [%s])\n' "$3" "$1" "$2"; FAILS=$((FAILS+1)); fi
}
assert_rc() { # $1=actual_rc $2=expected_rc $3=msg
  if [ "$1" = "$2" ]; then printf 'ok   - %s\n' "$3"
  else printf 'FAIL - %s (rc got [%s] want [%s])\n' "$3" "$1" "$2"; FAILS=$((FAILS+1)); fi
}

# Events are not under test; silence them everywhere.
emit_event() { :; }

# --- strip_zero / rfc3339_to_epoch (shared with heal.sh) ---
assert_eq "$(strip_zero 08)" "8" "strip_zero drops the octal-poisoning zero"
assert_eq "$(rfc3339_to_epoch 1970-01-01T00:00:00Z)" "0" "epoch zero"
assert_eq "$(rfc3339_to_epoch 2026-08-17T05:00:32Z)" "1786942832" "the empty backup that started this"
rfc3339_to_epoch "<none>"; assert_rc "$?" "1" "rejects <none>"

# --- in_list ---
in_list "velero-daily-b2" "velero-daily-b2 velero-x"; assert_rc "$?" "0" "in_list finds an exact word"
in_list "velero-daily" "velero-daily-b2";             assert_rc "$?" "1" "in_list does not match a prefix"
in_list "velero-daily-b2" "";                         assert_rc "$?" "1" "in_list on an empty list"

# --- max_age_for ---
MAX_AGE_HOURS=26
MAX_AGE_OVERRIDES="velero-monthly-b2:768"
assert_eq "$(max_age_for velero-daily-b2)"   "26"  "max_age_for falls back to the default"
assert_eq "$(max_age_for velero-monthly-b2)" "768" "max_age_for honours the override"
MAX_AGE_OVERRIDES="a:1 velero-monthly-b2:768 z:9"
assert_eq "$(max_age_for velero-monthly-b2)" "768" "max_age_for finds a mid-list override"
MAX_AGE_OVERRIDES="velero-monthly-b2:768"

# ---------------------------------------------------------------------------
# main() end-to-end, with the cluster stubbed.
# ---------------------------------------------------------------------------
# Fixed "now" so the staleness arithmetic is deterministic.
#   2026-08-17T12:00:00Z = 1786968000  (cross-checked with `date -u -d ... +%s`)
NOW=1786968000
date() { # only ever called as `date -u +%s` or `date -u +%FT%TZ`
  case "${2:-}" in
    +%s) echo "$NOW" ;;
    *)   echo "2026-08-17T12:00:00Z" ;;
  esac
}

# Per-test fixtures, consumed by the stubs below.
SCHEDULES=""   # "name last paused" rows
BACKUPS=""     # "schedule backup phase pvbcount" rows

schedule_rows() { printf '%s\n' "$SCHEDULES"; }
newest_backup() {
  for row in $BACKUPS; do
    s=$(echo "$row" | cut -d, -f1)
    [ "$s" = "$1" ] && { echo "$row" | cut -d, -f2; return 0; }
  done
  echo ""
}
backup_phase() {
  for row in $BACKUPS; do
    b=$(echo "$row" | cut -d, -f2)
    [ "$b" = "$1" ] && { echo "$row" | cut -d, -f3; return 0; }
  done
  echo ""
}
pvb_count() {
  for row in $BACKUPS; do
    b=$(echo "$row" | cut -d, -f2)
    [ "$b" = "$1" ] && { echo "$row" | cut -d, -f4; return 0; }
  done
  echo 0
}

run_main() { out=$(main 2>&1); rc=$?; }

# --- THE REGRESSION TEST: the bug this whole thing exists for ---
# velero-victoria-metrics-b2 completed, on time, 0 errors -- and 0 volumes.
SCHEDULES="velero-victoria-metrics-b2 2026-08-17T05:00:32Z <none>"
BACKUPS="velero-victoria-metrics-b2,velero-victoria-metrics-b2-20260817050032,Completed,0"
run_main
assert_rc "$rc" "1" "a Completed backup with 0 PodVolumeBackups FAILS the guard"
case "$out" in *"captured no volume data"*) r=0 ;; *) r=1 ;; esac
assert_rc "$r" "0" "  ...and says why"

# --- the healthy case ---
SCHEDULES="velero-daily-full 2026-08-17T03:00:43Z <none>"
BACKUPS="velero-daily-full,velero-daily-full-20260817030043,Completed,24"
run_main
assert_rc "$rc" "0" "a Completed backup with PVBs passes"

# --- PartiallyFailed still counts as a run worth inspecting ---
SCHEDULES="velero-daily-b2 2026-08-17T04:00:48Z <none>"
BACKUPS="velero-daily-b2,velero-daily-b2-20260817040048,PartiallyFailed,19"
run_main
assert_rc "$rc" "0" "PartiallyFailed with PVBs passes the content check"
BACKUPS="velero-daily-b2,velero-daily-b2-20260817040048,PartiallyFailed,0"
run_main
assert_rc "$rc" "1" "PartiallyFailed with 0 PVBs still fails"

# --- staleness ---
# 2026-08-15T05:00:00Z is 55h before NOW, past the 26h default.
SCHEDULES="velero-daily-full 2026-08-15T05:00:00Z <none>"
BACKUPS="velero-daily-full,velero-daily-full-20260815050000,Completed,24"
run_main
assert_rc "$rc" "1" "a schedule that stopped running fails on staleness"
case "$out" in *"55h ago"*) r=0 ;; *) r=1 ;; esac
assert_rc "$r" "0" "  ...and reports the real age"

# monthly is allowed to be old
SCHEDULES="velero-monthly-b2 2026-08-01T06:00:16Z <none>"
BACKUPS="velero-monthly-b2,velero-monthly-b2-20260801060016,Completed,88"
run_main
assert_rc "$rc" "0" "the monthly override exempts a 16-day-old backup"

# ...but not arbitrarily old: 768h = 32d, so 2026-06-01 is stale even for monthly
SCHEDULES="velero-monthly-b2 2026-06-01T06:00:16Z <none>"
BACKUPS="velero-monthly-b2,velero-monthly-b2-20260601060016,Completed,88"
run_main
assert_rc "$rc" "1" "the monthly override is a bound, not an exemption"

# --- paused / never-run schedules are not failures ---
# A paused schedule alongside a healthy one is simply skipped. Note the paused
# one is deliberately ancient: pausing must suppress the staleness check too.
SCHEDULES="velero-daily-full 2026-08-17T03:00:43Z <none>
velero-paused-thing 2026-06-01T05:00:00Z true"
BACKUPS="velero-daily-full,velero-daily-full-1,Completed,24"
run_main
assert_rc "$rc" "0" "a paused schedule is skipped, not failed"
case "$out" in *"checked 1 schedule(s)"*) r=0 ;; *) r=1 ;; esac
assert_rc "$r" "0" "  ...and does not count toward the checked total"

# But if EVERY schedule is paused, nothing is being backed up at all — the
# guard must not report success for a cluster with no live backups.
SCHEDULES="velero-daily-full 2026-08-17T03:00:43Z true"
BACKUPS="velero-daily-full,velero-daily-full-1,Completed,24"
run_main
assert_rc "$rc" "1" "all schedules paused fails — the guard verified nothing"

SCHEDULES="velero-brand-new <none> <none>"
BACKUPS=""
run_main
assert_rc "$rc" "0" "a schedule that has never run is skipped, not failed"

# --- in-flight backups are not judged ---
SCHEDULES="velero-daily-full 2026-08-17T03:00:43Z <none>"
BACKUPS="velero-daily-full,velero-daily-full-20260817030043,InProgress,0"
run_main
assert_rc "$rc" "0" "an InProgress backup is skipped, not failed for having 0 PVBs"
BACKUPS="velero-daily-full,velero-daily-full-20260817030043,Queued,0"
run_main
assert_rc "$rc" "0" "a Queued backup is skipped too"

# --- outright failures are caught here, not left to the daily-full-only rule ---
# This is the live 2026-08-17 case: daily-b2 Failed on a B2 x-amz-tagging error
# and no PrometheusRule would have fired on it for 72h.
SCHEDULES="velero-daily-b2 2026-08-17T04:00:48Z <none>"
BACKUPS="velero-daily-b2,velero-daily-b2-20260817040048,Failed,19"
run_main
assert_rc "$rc" "1" "a Failed backup fails the guard even with PVBs present"
case "$out" in *"phase=Failed"*) r=0 ;; *) r=1 ;; esac
assert_rc "$r" "0" "  ...and names the phase"
BACKUPS="velero-daily-b2,velero-daily-b2-20260817040048,FailedValidation,0"
run_main
assert_rc "$rc" "1" "FailedValidation fails too"

# --- volume-check exemption ---
SCHEDULES="velero-objects-only 2026-08-17T05:00:00Z <none>"
BACKUPS="velero-objects-only,velero-objects-only-20260817050000,Completed,0"
SKIP_VOLUME_CHECK="velero-objects-only"
run_main
assert_rc "$rc" "0" "SKIP_VOLUME_CHECK exempts a deliberately volume-less schedule"
SKIP_VOLUME_CHECK=""
run_main
assert_rc "$rc" "1" "  ...and removing it re-arms the check"

# --- lastBackup set but the Backup object is gone ---
SCHEDULES="velero-daily-full 2026-08-17T03:00:43Z <none>"
BACKUPS=""
run_main
assert_rc "$rc" "1" "lastBackup with no matching Backup object fails"

# --- seeing no schedules at all is a failure, not a pass ---
SCHEDULES=""
BACKUPS=""
run_main
assert_rc "$rc" "1" "an empty schedule list fails rather than reporting success"

# --- multiple schedules: one bad poisons the run, and all are still checked ---
SCHEDULES="velero-daily-full 2026-08-17T03:00:43Z <none>
velero-victoria-metrics-b2 2026-08-17T05:00:32Z <none>
velero-daily-b2 2026-08-17T04:00:48Z <none>"
BACKUPS="velero-daily-full,velero-daily-full-1,Completed,24
velero-victoria-metrics-b2,velero-vm-1,Completed,0
velero-daily-b2,velero-daily-b2-1,Completed,19"
run_main
assert_rc "$rc" "1" "one empty schedule fails a run of three"
case "$out" in *"checked 3 schedule(s), 1 failure(s)"*) r=0 ;; *) r=1 ;; esac
assert_rc "$r" "0" "  ...and the counters survive the loop (no subshell)"

printf '\n'
if [ "$FAILS" -eq 0 ]; then printf 'all tests passed\n'; else printf '%s test(s) failed\n' "$FAILS"; fi
[ "$FAILS" -eq 0 ]
