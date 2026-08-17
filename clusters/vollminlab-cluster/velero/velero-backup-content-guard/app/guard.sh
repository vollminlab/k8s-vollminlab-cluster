#!/bin/sh
# velero-backup-content-guard: fail loudly when a Velero schedule stops backing
# up anything, or stops running at all.
#
# Why this exists
# ---------------
# On 2026-08-17 `velero-victoria-metrics-b2` was found to have backed up zero
# volumes for its entire life. Its labelSelector was `app: victoria-metrics`, a
# label no pod or PVC in the monitoring namespace has ever carried -- the
# victoria-metrics-single chart forces `app: server`. Every night it reported:
#
#   velero-victoria-metrics-b2-20260816050032   Completed   4 items   0 errors
#
# Nothing alerted, and nothing could have: **an empty Velero backup is a
# successful Velero backup**. Phase is the only health signal Velero exposes,
# and it was green. `velero_backup_items_total` is exported but reads 0 for
# every schedule on this cluster, so it cannot serve as a content guard either.
#
# CronJobs on this cluster are covered by CronJobNotSucceededRecently (26h) and
# KubeJobFailed. Velero schedules had no equivalent for either failure mode --
# not "produced nothing", and not "stopped running". This closes both.
#
# How it reports
# --------------
# The script exits non-zero when any check fails, so the Job fails, so the
# existing KubeJobFailed alert fires. That is deliberate: it reuses an alerting
# path already proven on this cluster rather than adding a metrics exporter and
# bespoke PromQL that would itself need a guard. A Kubernetes Event is also
# emitted per finding, so `kubectl get events -n velero` explains a red job
# without needing the pod log.
set -u

# --- tunables (overridable via env in the CronJob) ---
VELERO_NAMESPACE="${VELERO_NAMESPACE:-velero}"

# Hours a schedule may go without producing a backup before it is considered
# stale. 26h matches CronJobNotSucceededRecently: one missed daily run plus
# slack for a long queue drain (schedules run serially here, and
# itemOperationTimeout is 4h).
MAX_AGE_HOURS="${MAX_AGE_HOURS:-26}"

# Per-schedule staleness overrides, space-separated `name:hours`. Monthly
# schedules obviously cannot satisfy a 26h bound. 768h = 32 days, which is one
# monthly run plus slack.
#
# This lives here rather than as an annotation on the Schedule because the
# velero chart generates Schedule objects from its values and its support for
# arbitrary per-schedule metadata varies by chart version -- an env map is one
# place to look and cannot silently stop being applied.
MAX_AGE_OVERRIDES="${MAX_AGE_OVERRIDES:-velero-monthly-b2:768}"

# Schedules exempt from the "must have backed up at least one volume" check --
# space-separated names. A schedule that legitimately captures only Kubernetes
# objects and no PVC data belongs here. Empty by default: every schedule on this
# cluster sets defaultVolumesToFsBackup: true, so every one of them is expected
# to produce PodVolumeBackups.
SKIP_VOLUME_CHECK="${SKIP_VOLUME_CHECK:-}"

DRY_RUN="${DRY_RUN:-false}"

# Single kubectl entry point so tests can stub it.
kc() { kubectl "$@"; }

log() { echo "[$(date -u +%FT%TZ)] $*"; }

# strip_zero: echo a numeric field without its leading zero. "08" is an invalid
# octal literal in ash/dash arithmetic, so every parsed field goes through this.
strip_zero() {
  v="${1#0}"
  [ -n "$v" ] || v=0
  echo "$v"
}

# rfc3339_to_epoch: convert 2026-08-11T03:02:59Z to unix seconds via the
# days-from-civil algorithm. Kubernetes timestamps are always UTC, but busybox
# `date` and GNU `date` disagree on how to parse this format (-D vs -d), so the
# arithmetic is done here to keep the script portable and unit-testable.
# $1=timestamp. Returns 1 on anything that is not an RFC3339 UTC stamp.
rfc3339_to_epoch() {
  ts="$1"
  case "$ts" in *T*Z) ;; *) return 1 ;; esac
  y=$(strip_zero "${ts%%-*}");    rest="${ts#*-}"
  mo=$(strip_zero "${rest%%-*}"); rest="${rest#*-}"
  d=$(strip_zero "${rest%%T*}");  rest="${rest#*T}"
  h=$(strip_zero "${rest%%:*}");  rest="${rest#*:}"
  mi=$(strip_zero "${rest%%:*}"); rest="${rest#*:}"
  s=$(strip_zero "${rest%%[.Z]*}")

  [ "$mo" -le 2 ] && y=$((y - 1))
  era=$((y / 400))
  yoe=$((y - era * 400))
  if [ "$mo" -gt 2 ]; then mp=$((mo - 3)); else mp=$((mo + 9)); fi
  doy=$(((153 * mp + 2) / 5 + d - 1))
  doe=$((yoe * 365 + yoe / 4 - yoe / 100 + doy))
  days=$((era * 146097 + doe - 719468))
  echo $((days * 86400 + h * 3600 + mi * 60 + s))
}

# in_list: exit 0 if $1 appears as a whitespace-separated word in $2.
# Word-boundary matched, so "velero-daily" does not match "velero-daily-b2".
in_list() {
  needle="$1"; hay="$2"
  for w in $hay; do
    [ "$w" = "$needle" ] && return 0
  done
  return 1
}

# max_age_for: hours this schedule may go without a backup. $1=schedule name.
# Falls back to MAX_AGE_HOURS when the name has no override entry.
max_age_for() {
  name="$1"
  for pair in $MAX_AGE_OVERRIDES; do
    case "$pair" in
      "$name":*) echo "${pair#*:}"; return 0 ;;
    esac
  done
  echo "$MAX_AGE_HOURS"
}

# schedule_rows: one line per Schedule -- "name lastBackup paused".
# `<none>` is what kubectl prints for an absent field; callers must handle it.
schedule_rows() {
  kc get schedules.velero.io -n "$VELERO_NAMESPACE" --no-headers \
    -o custom-columns='NAME:.metadata.name,LAST:.status.lastBackup,PAUSED:.spec.paused' 2>/dev/null
}

# newest_backup: name of the most recent Backup produced by a schedule, or
# empty. Scoped by the velero.io/schedule-name label and sorted by creation
# time; `-o jsonpath={.items[-1:]...}` takes the last, i.e. newest.
newest_backup() {
  kc get backups.velero.io -n "$VELERO_NAMESPACE" \
    -l "velero.io/schedule-name=$1" --sort-by=.metadata.creationTimestamp \
    -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null
}

# backup_phase: phase of a Backup, or empty if it has none yet.
backup_phase() {
  kc get backups.velero.io -n "$VELERO_NAMESPACE" "$1" \
    -o jsonpath='{.status.phase}' 2>/dev/null
}

# pvb_count: how many PodVolumeBackups a Backup produced.
#
# Scoped by the velero.io/backup-name label, never listed unscoped. PVBs are
# retained for their backup's whole TTL (2160h on the B2 schedules), so an
# unscoped list is thousands of objects -- that is what OOM-killed the sibling
# velero-pvb-healer on every run until #1056. Scoping bounds this at one
# backup's PVB count permanently.
pvb_count() {
  kc get podvolumebackups.velero.io -n "$VELERO_NAMESPACE" \
    -l "velero.io/backup-name=$1" --no-headers 2>/dev/null | grep -c . || true
}

# emit_event: best-effort Warning Event so a red job is explainable from
# `kubectl get events` alone. Never fails the run -- the exit code is the
# primary signal and must not depend on the events API being writable.
emit_event() {
  reason="$1"; msg="$2"
  [ "$DRY_RUN" = "true" ] && return 0
  kc create -n "$VELERO_NAMESPACE" -f - >/dev/null 2>&1 <<EOF || log "  (event emit failed, continuing)"
apiVersion: v1
kind: Event
metadata:
  generateName: velero-backup-content-guard-
  namespace: $VELERO_NAMESPACE
type: Warning
reason: $reason
message: "$msg"
involvedObject:
  apiVersion: v1
  kind: Namespace
  name: $VELERO_NAMESPACE
source:
  component: velero-backup-content-guard
EOF
}

main() {
  now=$(date -u +%s)
  failures=0
  checked=0

  # Rows go to a file, and the loop below reads from that file with `< "$tmp"`.
  # It must NOT be `schedule_rows | while read ...`: a piped while runs in a
  # subshell, so every `failures=$((failures + 1))` inside it would be discarded
  # on exit and the guard would report success no matter what it found. That is
  # the same class of silent-success bug this script exists to catch.
  tmp="${TMPDIR:-/tmp}/velero-guard-rows.$$"
  schedule_rows > "$tmp"

  if [ ! -s "$tmp" ]; then
    # Distinct from "every schedule passed". If the guard cannot see any
    # schedules it has verified nothing, and saying "OK" would be a lie of
    # exactly the kind it exists to catch.
    rm -f "$tmp"
    log "ERROR: no Velero schedules found in namespace $VELERO_NAMESPACE"
    emit_event "VeleroGuardFoundNoSchedules" \
      "velero-backup-content-guard found no Schedule objects in $VELERO_NAMESPACE -- it verified nothing."
    return 1
  fi

  while read -r name last paused; do
    [ -n "$name" ] || continue

    if [ "$paused" = "true" ]; then
      log "$name: paused, skipping"
      continue
    fi

    checked=$((checked + 1))

    # --- check 1: is it still running at all? ---
    max_h=$(max_age_for "$name")
    max_s=$((max_h * 3600))
    if last_epoch=$(rfc3339_to_epoch "$last"); then
      age=$((now - last_epoch))
      if [ "$age" -gt "$max_s" ]; then
        age_h=$((age / 3600))
        log "FAIL $name: last backup was ${age_h}h ago (limit ${max_h}h)"
        emit_event "VeleroScheduleStale" \
          "Velero schedule $name has not produced a backup in ${age_h}h (limit ${max_h}h)."
        failures=$((failures + 1))
        continue
      fi
    else
      # No lastBackup at all. A schedule that has never run is either brand new
      # or broken; either way it is not yet meaningful to check its contents.
      log "WARN $name: no lastBackup recorded yet, skipping (newly created?)"
      continue
    fi

    # --- check 2: did the most recent run actually capture any volumes? ---
    if in_list "$name" "$SKIP_VOLUME_CHECK"; then
      log "OK   $name: fresh (${max_h}h bound); volume check exempted"
      continue
    fi

    b=$(newest_backup "$name")
    if [ -z "$b" ]; then
      log "FAIL $name: status.lastBackup is set but no Backup object carries its label"
      emit_event "VeleroScheduleBackupMissing" \
        "Velero schedule $name reports lastBackup=$last but no Backup object has label velero.io/schedule-name=$name."
      failures=$((failures + 1))
      continue
    fi

    phase=$(backup_phase "$b")
    case "$phase" in
      Completed|PartiallyFailed)
        # Ran to the end. Worth counting what it captured.
        ;;
      Failed|FailedValidation)
        # Outright failure. This IS the guard's job, despite the PrometheusRule
        # named VeleroBackupFailed existing: that rule is hard-scoped to
        # schedule="velero-daily-full". As of 2026-08-17 monthly-b2 and the
        # victoria-metrics schedule had no failure alert at all, and daily-b2
        # only had a 72h persistently-failing rule -- so a B2 backup could fail
        # outright and stay silent for three days.
        #
        # This guard enumerates schedules dynamically, so it covers every
        # schedule including ones added later without anyone remembering to
        # extend a PromQL selector. Overlapping with VeleroBackupFailed on
        # daily-full is the intended trade: duplicate noise on one schedule
        # beats silence on the rest.
        log "FAIL $name: newest backup $b is phase=$phase"
        emit_event "VeleroBackupFailed" \
          "Velero backup $b (schedule $name) is in phase $phase. Check 'velero backup describe $b'."
        failures=$((failures + 1))
        continue
        ;;
      *)
        # InProgress, Queued, Deleting, or a phase newer than this script. A
        # running backup has nothing to count yet.
        log "SKIP $name: newest backup $b is phase=${phase:-<none>}, not a finished run"
        continue
        ;;
    esac

    n=$(pvb_count "$b")
    if [ "$n" -eq 0 ]; then
      log "FAIL $name: backup $b is $phase but produced 0 PodVolumeBackups -- it captured no volume data"
      emit_event "VeleroBackupCapturedNoVolumes" \
        "Velero backup $b (schedule $name) reported $phase but produced 0 PodVolumeBackups. Its labelSelector probably matches nothing -- check that the selector uses a label present on BOTH the pod and the PVC."
      failures=$((failures + 1))
      continue
    fi

    log "OK   $name: $b $phase, $n PodVolumeBackups"
  done < "$tmp"
  rm -f "$tmp"

  # "Checked nothing" is a failure, not a pass. The -s test above catches a
  # truly empty listing, but not a listing that is whitespace, or one where
  # every schedule was skipped as paused/never-run. In all of those cases the
  # guard has verified nothing, and exiting 0 would assert health it never
  # observed -- which is precisely the failure mode it was written to catch.
  if [ "$checked" -eq 0 ]; then
    log "ERROR: 0 schedules were actually checked -- the guard verified nothing"
    emit_event "VeleroGuardCheckedNothing" \
      "velero-backup-content-guard completed without checking any schedule. Every schedule was absent, paused, or had never run."
    return 1
  fi

  log "checked $checked schedule(s), $failures failure(s)"
  [ "$failures" -eq 0 ]
}

[ -n "${GUARD_TEST:-}" ] || main
