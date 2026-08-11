#!/bin/sh
# velero-pvb-healer: clear the node-agent datapath-slot leak that freezes a
# PodVolumeBackup in `Prepared` forever.
#
# In Velero v1.18's micro-service data mover, a PVB is run by a per-PVB exposer
# pod that waits for node-agent to flip the PVB to InProgress. node-agent only
# does that after it creates a datapath routine for the PVB. When the in-memory
# datapath slot leaks, every attempt returns ConcurrentLimitExceed -- which is
# logged at Debug only -- and the controller just requeues on a 5s cadence. Both
# sides then wait forever with no error emitted anywhere.
#
# Because loadConcurrency is pinned at 1 on this cluster (worker VMDKs share one
# datastore), a single leaked slot wedges every remaining PVB on that node. FSB
# is head-of-line blocked in backupper.go, so the whole backup stalls until
# itemOperationTimeout expires 4h later and the run lands PartiallyFailed.
#
# The state is in-memory only: deleting the node-agent pod on that node releases
# it and the frozen PVB completes within seconds. That is exactly what this
# script automates.
set -u

# --- tunables (overridable via env in the CronJob) ---
PVB_NAMESPACE="${PVB_NAMESPACE:-velero}"
STALL_SECONDS="${STALL_SECONDS:-900}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-3600}"
NODE_AGENT_SELECTOR="${NODE_AGENT_SELECTOR:-name=node-agent}"
DRY_RUN="${DRY_RUN:-false}"

ANN_LAST="pvb-healer.vollminlab.com/last-healed"
# jsonpath-escaped annotation key (dots escaped)
JP_LAST='pvb-healer\.vollminlab\.com/last-healed'

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

  # civil_from_days, shifted so the year starts in March (Feb is then last)
  [ "$mo" -le 2 ] && y=$((y - 1))
  era=$((y / 400))
  yoe=$((y - era * 400))
  if [ "$mo" -gt 2 ]; then mp=$((mo - 3)); else mp=$((mo + 9)); fi
  doy=$(((153 * mp + 2) / 5 + d - 1))
  doe=$((yoe * 365 + yoe / 4 - yoe / 100 + doy))
  days=$((era * 146097 + doe - 719468))
  echo $((days * 86400 + h * 3600 + mi * 60 + s))
}

# in_cooldown: exit 0 if (now - last) < cooldown. $1=last(may be empty) $2=now $3=cooldown
in_cooldown() {
  last="$1"; now="$2"; cd="$3"
  [ -n "$last" ] || return 1
  delta=$((now - last))
  [ "$delta" -lt "$cd" ]
}

# pvb_rows: one line per PodVolumeBackup -- "name phase node acceptedTimestamp".
# custom-columns rather than jsonpath because it renders a missing field as
# <none> instead of an empty string, which would shift the columns apart under
# word splitting. The node comes from .spec.node: .status.node exists in the CRD
# but is not populated in v1.18.1.
pvb_rows() {
  kc get podvolumebackups.velero.io -n "$PVB_NAMESPACE" --no-headers \
    -o custom-columns='NAME:.metadata.name,PHASE:.status.phase,NODE:.spec.node,ACCEPTED:.status.acceptedTimestamp' \
    2>/dev/null
}

# busy_nodes: echo the nodes holding a legitimate datapath slot right now. A PVB
# in InProgress means the slot on that node is genuinely in use, so any sibling
# PVB sitting at Prepared there is just queued behind it -- normal with
# loadConcurrency=1, and NOT a leak. $1=rows
busy_nodes() {
  echo "$1" | while read -r _name phase node _accepted; do
    [ "$phase" = "InProgress" ] || continue
    echo "$node"
  done
}

# node_is_busy: $1=node $2=space/newline separated busy node list
node_is_busy() {
  for b in $2; do
    [ "$b" = "$1" ] && return 0
  done
  return 1
}

# emit_event: best-effort core/v1 Event on the PVB. Never fails the run.
# $1=pvb $2=reason $3=type(Normal|Warning) $4=message
emit_event() {
  epvb="$1"; ereason="$2"; etype="$3"; emsg="$4"
  euid=$(kc get podvolumebackups.velero.io "$epvb" -n "$PVB_NAMESPACE" -o jsonpath='{.metadata.uid}' 2>/dev/null)
  ets=$(date -u +%FT%TZ)
  ename="${epvb}.$(date -u +%s)"
  kc create -f - >/dev/null 2>&1 <<EOF || log "WARN: event emit failed for $epvb"
apiVersion: v1
kind: Event
metadata:
  name: ${ename}
  namespace: ${PVB_NAMESPACE}
involvedObject:
  apiVersion: velero.io/v1
  kind: PodVolumeBackup
  name: ${epvb}
  namespace: ${PVB_NAMESPACE}
  uid: ${euid}
reason: ${ereason}
message: "${emsg}"
type: ${etype}
source:
  component: velero-pvb-healer
firstTimestamp: ${ets}
lastTimestamp: ${ets}
count: 1
EOF
}

# heal_node: release the leaked slot by restarting node-agent on that node.
# $1=node $2=pvb $3=age_seconds
heal_node() {
  node="$1"; pvb="$2"; age="$3"
  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN: would delete node-agent on $node (pvb=$pvb stalled ${age}s)"
    return 0
  fi
  # Stamp the cooldown BEFORE deleting, so a healer pod that dies mid-heal still
  # leaves a record and cannot loop on the same PVB every 10 minutes.
  kc annotate podvolumebackups.velero.io "$pvb" -n "$PVB_NAMESPACE" \
    "$ANN_LAST=$(date -u +%s)" --overwrite
  log "healing $node: deleting node-agent (pvb=$pvb stalled ${age}s)"
  kc delete pod -n "$PVB_NAMESPACE" -l "$NODE_AGENT_SELECTOR" \
    --field-selector "spec.nodeName=$node"
  emit_event "$pvb" HealedStalledPodVolumeBackup Normal \
    "PVB stalled in Prepared for ${age}s with no InProgress PVB on $node; restarted node-agent to release the leaked datapath slot"
}

# find_and_heal_one: heal at most ONE node per run. Two nodes leaking at once is
# not a case that has been seen, and FSB is head-of-line blocked on a single pod
# anyway, so the second one can wait for the next tick.
find_and_heal_one() {
  rows=$(pvb_rows)
  [ -n "$rows" ] || { log "no PodVolumeBackups found"; return 0; }
  busy=$(busy_nodes "$rows")
  now=$(date -u +%s)
  healed=0

  # Fed by here-doc, not a pipe: a `while` on the right of a pipe runs in a
  # subshell, and $healed would not survive the loop.
  while read -r name phase node accepted; do
    [ "$phase" = "Prepared" ] || continue
    [ "$node" != "<none>" ] || continue
    [ "$accepted" != "<none>" ] || continue

    accepted_epoch=$(rfc3339_to_epoch "$accepted") || continue
    age=$((now - accepted_epoch))
    [ "$age" -ge "$STALL_SECONDS" ] || continue

    if node_is_busy "$node" "$busy"; then
      log "skip $name: $node has a PVB InProgress (queued, not leaked)"
      continue
    fi

    last=$(kc get podvolumebackups.velero.io "$name" -n "$PVB_NAMESPACE" \
      -o jsonpath="{.metadata.annotations.$JP_LAST}" 2>/dev/null)
    if in_cooldown "$last" "$now" "$COOLDOWN_SECONDS"; then
      log "skip $name: in cooldown (last-healed=$last)"
      continue
    fi

    heal_node "$node" "$name" "$age"
    healed=1
    break
  done <<EOF
$rows
EOF

  [ "$healed" = 1 ] || log "no stalled PodVolumeBackups"
}

main() {
  log "velero-pvb-healer start (ns=$PVB_NAMESPACE stall=${STALL_SECONDS}s dry_run=$DRY_RUN)"
  find_and_heal_one
  log "velero-pvb-healer done"
}

[ -n "${HEAL_TEST:-}" ] || main "$@"
