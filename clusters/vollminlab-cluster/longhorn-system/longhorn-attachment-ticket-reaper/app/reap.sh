#!/bin/sh
# longhorn-attachment-ticket-reaper
#
# Longhorn leaks `snapshot-controller-*` attachment tickets: the VolumeSnapshot
# and VolumeSnapshotContent that created them get deleted, but the ticket stays
# on the Longhorn VolumeAttachment forever. Each leaked ticket pins the volume
# `attached`, so its engine process can never stop.
#
# That matters because the engine holds the "is rebuilding" flag Longhorn can
# strand. Once that flag goes stale EVERY later rebuild on the volume deadlocks,
# and the only cure is restarting the engine — which needs a full detach, which
# the leaked ticket makes impossible. On 2026-08-26 mediastack/pvc-filebrowser-
# config had SIX leaked tickets and an engine running since 2026-05-17; it
# deadlocked four times over four days and each manual fix (deleting the
# stranded replica) cleared only the symptom.
#
# Scaling the workload to 0 does NOT help: that releases the csi-attacher ticket
# but the snapshot-controller ones remain.
#
# Only snapshot-controller tickets are removed, and only from volumes with no
# VolumeSnapshotContent pointing at them. csi-attacher and every other ticket
# type is left alone, so a volume in use stays attached and no workload is
# disturbed — verified on 4 volumes on 2026-08-26 with zero restarts.
set -eu

NS="${LONGHORN_NAMESPACE:-longhorn-system}"
RECHECK_SECONDS="${RECHECK_SECONDS:-60}"
DRY_RUN="${DRY_RUN:-false}"
WORK="${TMPDIR:-/tmp}/reaper.$$"

kc() { kubectl "$@"; }
log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
cleanup() { rm -f "$WORK".* 2>/dev/null || true; }
trap cleanup EXIT

# live_volumes: volumeHandle of every VolumeSnapshotContent, one per line.
# A volume listed here has a live snapshot object and is never touched.
live_volumes() {
  kc get volumesnapshotcontents.snapshot.storage.k8s.io \
    -o jsonpath='{range .items[*]}{.spec.source.volumeHandle}{"\n"}{end}' 2>/dev/null || true
}

# orphan_candidates > file : lines of "<volume> <ticketID>".
# Also writes the number of VolumeAttachments actually examined to $WORK.count.
#
# That count is the whole point of this function's logging contract. "no orphaned
# tickets found" is ambiguous on its own: it reads identically whether the query
# returned 48 attachments with nothing to do, or returned NOTHING because RBAC
# was wrong and every result was swallowed by `|| true`. velero-pvb-healer has
# the same distinction documented ("no stalled PodVolumeBackups" vs "no
# PodVolumeBackups found") for exactly this reason. Callers MUST report the count
# so a silently-inert reaper is distinguishable from a correctly-idle one.
orphan_candidates() {
  out="$1"
  live_volumes | sort -u > "$WORK".live
  : > "$out"
  kc get volumeattachments.longhorn.io -n "$NS" \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{range .spec.attachmentTickets.*}{.id}{","}{end}{"\n"}{end}' \
    2>/dev/null > "$WORK".att || true
  # `grep -c .` counts NON-BLANK lines, so a query that emits only a newline is
  # still counted as zero. `|| true` (not `|| echo 0`) because grep already
  # PRINTS 0 before exiting 1 — the echo would append a second 0.
  grep -c . "$WORK".att > "$WORK".count 2>/dev/null || true
  while IFS=' ' read -r vol tickets; do
    [ -n "$vol" ] || continue
    # skip any volume that still has a snapshot object
    if grep -qxF "$vol" "$WORK".live 2>/dev/null; then continue; fi
    echo "$tickets" | tr ',' '\n' | while IFS= read -r t; do
      case "$t" in
        snapshot-controller-*) echo "$vol $t" >> "$out" ;;
      esac
    done
  done < "$WORK".att
  sort -u -o "$out" "$out" 2>/dev/null || true
}

emit_event() {
  vol="$1"; ticket="$2"; now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  kc create -n "$NS" -f - >/dev/null 2>&1 <<EOF || true
apiVersion: v1
kind: Event
metadata:
  generateName: attachment-ticket-reaped-
  namespace: $NS
involvedObject:
  apiVersion: longhorn.io/v1beta2
  kind: VolumeAttachment
  name: $vol
  namespace: $NS
reason: AttachmentTicketReaped
message: "Removed orphaned snapshot-controller ticket $ticket; no VolumeSnapshotContent references this volume."
type: Normal
source:
  component: longhorn-attachment-ticket-reaper
firstTimestamp: $now
lastTimestamp: $now
count: 1
EOF
}

reap() {
  vol="$1"; ticket="$2"
  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN: would remove $ticket from $vol"
    return 0
  fi
  patch=$(printf '{"spec":{"attachmentTickets":{"%s":null}}}' "$ticket")
  if kc patch volumeattachments.longhorn.io -n "$NS" "$vol" --type=merge -p "$patch" >/dev/null 2>&1; then
    log "reaped $ticket from $vol"
    emit_event "$vol" "$ticket"
  else
    log "WARN: failed to patch $vol removing $ticket"
  fi
}

main() {
  log "longhorn-attachment-ticket-reaper start (ns=$NS recheck=${RECHECK_SECONDS}s dry_run=$DRY_RUN)"

  orphan_candidates "$WORK".first
  scanned=$(cat "$WORK".count 2>/dev/null || true); scanned=${scanned:-0}
  live=$(grep -c . "$WORK".live 2>/dev/null || true); live=${live:-0}

  # An empty attachment list is NOT the same as nothing to do. Longhorn always
  # has VolumeAttachments while any volume is attached, so zero means the query
  # failed — almost certainly RBAC — and every later check would be vacuous.
  if [ "$scanned" -eq 0 ]; then
    log "ERROR: examined 0 VolumeAttachments in $NS — query returned nothing, check RBAC"
    log "longhorn-attachment-ticket-reaper done"
    return 0
  fi

  if [ ! -s "$WORK".first ]; then
    log "examined $scanned VolumeAttachments ($live with a live VolumeSnapshotContent) — no orphaned snapshot-controller tickets"
    log "longhorn-attachment-ticket-reaper done"
    return 0
  fi
  log "examined $scanned VolumeAttachments ($live with a live VolumeSnapshotContent); candidates on first pass: $(wc -l < "$WORK".first)"

  # Two passes, RECHECK_SECONDS apart. snapshot-controller creates the ticket
  # BEFORE the VolumeSnapshotContent exists, so a single pass would race an
  # in-flight snapshot and cancel it. Only tickets orphaned in BOTH passes are
  # reaped.
  sleep "$RECHECK_SECONDS"
  orphan_candidates "$WORK".second
  comm -12 "$WORK".first "$WORK".second > "$WORK".confirmed 2>/dev/null || true

  if [ ! -s "$WORK".confirmed ]; then
    log "all candidates resolved on recheck (in-flight snapshots) — nothing reaped"
    log "longhorn-attachment-ticket-reaper done"
    return 0
  fi

  while IFS=' ' read -r vol ticket; do
    [ -n "$vol" ] || continue
    reap "$vol" "$ticket"
  done < "$WORK".confirmed
  log "longhorn-attachment-ticket-reaper done"
}

[ -n "${REAP_TEST:-}" ] || main "$@"
