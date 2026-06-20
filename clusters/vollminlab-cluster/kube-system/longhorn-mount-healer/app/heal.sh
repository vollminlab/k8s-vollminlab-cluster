#!/bin/sh
# longhorn-mount-healer: auto-clear storage-induced crashloops by codifying
# the proven scale-0 -> wait-detached -> scale-back runbook.
set -u

# --- tunables (overridable via env in the CronJob) ---
HEAL_NAMESPACES="${HEAL_NAMESPACES:-mediastack monitoring harbor}"
RESTART_THRESHOLD="${RESTART_THRESHOLD:-5}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-21600}"
DETACH_TIMEOUT_SECONDS="${DETACH_TIMEOUT_SECONDS:-180}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-5}"
DRY_RUN="${DRY_RUN:-false}"

ANN_ORIG="mount-healer.vollminlab.com/original-replicas"
ANN_LAST="mount-healer.vollminlab.com/last-healed"
# jsonpath-escaped annotation keys (dots escaped)
JP_ORIG='mount-healer\.vollminlab\.com/original-replicas'
JP_LAST='mount-healer\.vollminlab\.com/last-healed'

# Single kubectl entry point so tests can stub it.
kc() { kubectl "$@"; }

log() { echo "[$(date -u +%FT%TZ)] $*"; }

# sum_ints: echo the sum of all integer args (empty args ignored).
sum_ints() {
  total=0
  for n in "$@"; do
    [ -n "$n" ] || continue
    total=$((total + n))
  done
  echo "$total"
}

# in_cooldown: exit 0 if (now - last) < cooldown. $1=last(may be empty) $2=now $3=cooldown
in_cooldown() {
  last="$1"; now="$2"; cd="$3"
  [ -n "$last" ] || return 1
  delta=$((now - last))
  [ "$delta" -lt "$cd" ]
}

# pod_crashloop_restarts: echo summed restartCount IF a container/init is in
# CrashLoopBackOff, else echo 0. $1=ns $2=pod
pod_crashloop_restarts() {
  ns="$1"; pod="$2"
  reasons=$(kc get pod "$pod" -n "$ns" -o jsonpath='{.status.containerStatuses[*].state.waiting.reason}{.status.initContainerStatuses[*].state.waiting.reason}')
  case "$reasons" in
    *CrashLoopBackOff*) ;;
    *) echo 0; return 0 ;;
  esac
  restarts=$(kc get pod "$pod" -n "$ns" -o jsonpath='{.status.containerStatuses[*].restartCount} {.status.initContainerStatuses[*].restartCount}')
  sum_ints $restarts
}

# longhorn_rwo_volume: echo the PV name of the first longhorn RWO PVC the pod
# mounts, else empty. $1=ns $2=pod
longhorn_rwo_volume() {
  ns="$1"; pod="$2"
  claims=$(kc get pod "$pod" -n "$ns" -o jsonpath='{.spec.volumes[*].persistentVolumeClaim.claimName}')
  for claim in $claims; do
    [ -n "$claim" ] || continue
    sc=$(kc get pvc "$claim" -n "$ns" -o jsonpath='{.spec.storageClassName}')
    case "$sc" in longhorn*) ;; *) continue ;; esac
    modes=$(kc get pvc "$claim" -n "$ns" -o jsonpath='{.spec.accessModes[*]}')
    case " $modes " in *" ReadWriteOnce "*) ;; *) continue ;; esac
    pv=$(kc get pvc "$claim" -n "$ns" -o jsonpath='{.spec.volumeName}')
    [ -n "$pv" ] && { echo "$pv"; return 0; }
  done
}

# owner_deployment: echo the Deployment that owns the pod (via its ReplicaSet),
# else empty. $1=ns $2=pod
owner_deployment() {
  ns="$1"; pod="$2"
  rs=$(kc get pod "$pod" -n "$ns" -o jsonpath='{.metadata.ownerReferences[?(@.kind=="ReplicaSet")].name}')
  [ -n "$rs" ] || return 0
  kc get rs "$rs" -n "$ns" -o jsonpath='{.metadata.ownerReferences[?(@.kind=="Deployment")].name}'
}

# wait_detached: poll the Longhorn volume until state=detached or timeout.
# $1=vol $2=timeout_seconds $3=interval_seconds. exit 0 if detached.
wait_detached() {
  vol="$1"; timeout="$2"; interval="$3"; elapsed=0
  while [ "$elapsed" -lt "$timeout" ]; do
    state=$(kc get volumes.longhorn.io "$vol" -n longhorn-system -o jsonpath='{.status.state}' 2>/dev/null)
    [ "$state" = "detached" ] && return 0
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
  return 1
}

# emit_event: best-effort core/v1 Event on the Deployment. Never fails the run.
# $1=ns $2=deploy $3=reason $4=type(Normal|Warning) $5=message
emit_event() {
  ens="$1"; edep="$2"; ereason="$3"; etype="$4"; emsg="$5"
  euid=$(kc get deploy "$edep" -n "$ens" -o jsonpath='{.metadata.uid}' 2>/dev/null)
  ets=$(date -u +%FT%TZ)
  ename="${edep}.$(date -u +%s)"
  kc create -f - >/dev/null 2>&1 <<EOF || log "WARN: event emit failed for $ens/$edep"
apiVersion: v1
kind: Event
metadata:
  name: ${ename}
  namespace: ${ens}
involvedObject:
  apiVersion: apps/v1
  kind: Deployment
  name: ${edep}
  namespace: ${ens}
  uid: ${euid}
reason: ${ereason}
message: "${emsg}"
type: ${etype}
source:
  component: longhorn-mount-healer
firstTimestamp: ${ets}
lastTimestamp: ${ets}
count: 1
EOF
}

# heal_workload: the runbook. $1=ns $2=deploy $3=vol
heal_workload() {
  ns="$1"; dep="$2"; vol="$3"
  replicas=$(kc get deploy "$dep" -n "$ns" -o jsonpath='{.spec.replicas}')
  if [ -z "$replicas" ] || [ "$replicas" -le 0 ] 2>/dev/null; then
    log "skip $ns/$dep: replicas=$replicas (nothing to heal)"; return 0
  fi
  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN: would heal $ns/$dep (vol=$vol): scale 0 -> wait-detached -> $replicas"; return 0
  fi
  log "healing $ns/$dep (vol=$vol, replicas=$replicas)"
  kc annotate deploy "$dep" -n "$ns" "$ANN_ORIG=$replicas" --overwrite
  kc scale deploy "$dep" -n "$ns" --replicas=0
  if wait_detached "$vol" "$DETACH_TIMEOUT_SECONDS" "$POLL_INTERVAL_SECONDS"; then
    kc scale deploy "$dep" -n "$ns" --replicas="$replicas"
    log "healed $ns/$dep (restored to $replicas)"
    emit_event "$ns" "$dep" HealedStaleMount Normal "Cleared stale Longhorn mount on $vol via scale-0/wait-detached/scale-$replicas"
  else
    kc scale deploy "$dep" -n "$ns" --replicas="$replicas"
    log "WARN: $vol did not detach within ${DETACH_TIMEOUT_SECONDS}s; restored $ns/$dep to $replicas anyway"
    emit_event "$ns" "$dep" HealedStaleMountTimeout Warning "Volume $vol did not detach within ${DETACH_TIMEOUT_SECONDS}s; restored replicas to $replicas, manual check needed"
  fi
  # clear the original-replicas marker (restore done) and stamp cooldown
  kc annotate deploy "$dep" -n "$ns" "${ANN_ORIG}-" >/dev/null 2>&1 || true
  kc annotate deploy "$dep" -n "$ns" "$ANN_LAST=$(date -u +%s)" --overwrite
}

main() {
  log "longhorn-mount-healer placeholder main"
}

[ -n "${HEAL_TEST:-}" ] || main "$@"
