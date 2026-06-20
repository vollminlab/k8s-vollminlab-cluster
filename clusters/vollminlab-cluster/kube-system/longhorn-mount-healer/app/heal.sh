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

main() {
  log "longhorn-mount-healer placeholder main"
}

[ -n "${HEAL_TEST:-}" ] || main "$@"
