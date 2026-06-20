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

main() {
  log "longhorn-mount-healer placeholder main"
}

[ -n "${HEAL_TEST:-}" ] || main "$@"
