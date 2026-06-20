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

# --- detection helpers: stub kc as a dispatcher over fixtures ---
# A crashlooping radarr pod on a longhorn RWO PVC.
kc() {
  case "$*" in
    "get pod radarr-x -n mediastack -o jsonpath={.status.containerStatuses[*].state.waiting.reason}{.status.initContainerStatuses[*].state.waiting.reason}")
      echo "CrashLoopBackOff" ;;
    "get pod radarr-x -n mediastack -o jsonpath={.status.containerStatuses[*].restartCount} {.status.initContainerStatuses[*].restartCount}")
      echo "37 " ;;
    "get pod radarr-x -n mediastack -o jsonpath={.spec.volumes[*].persistentVolumeClaim.claimName}")
      echo "radarr-config" ;;
    "get pvc radarr-config -n mediastack -o jsonpath={.spec.storageClassName}")
      echo "longhorn" ;;
    "get pvc radarr-config -n mediastack -o jsonpath={.spec.accessModes[*]}")
      echo "ReadWriteOnce" ;;
    "get pvc radarr-config -n mediastack -o jsonpath={.spec.volumeName}")
      echo "pvc-abc123" ;;
    "get pod radarr-x -n mediastack -o jsonpath={.metadata.ownerReferences[?(@.kind==\"ReplicaSet\")].name}")
      echo "radarr-5d9" ;;
    "get rs radarr-5d9 -n mediastack -o jsonpath={.metadata.ownerReferences[?(@.kind==\"Deployment\")].name}")
      echo "radarr" ;;
    # a healthy pod (no waiting reason) on an smb PVC
    "get pod prowlarr-y -n mediastack -o jsonpath={.status.containerStatuses[*].state.waiting.reason}{.status.initContainerStatuses[*].state.waiting.reason}")
      echo "" ;;
    "get pod prowlarr-y -n mediastack -o jsonpath={.spec.volumes[*].persistentVolumeClaim.claimName}")
      echo "media-share" ;;
    "get pvc media-share -n mediastack -o jsonpath={.spec.storageClassName}")
      echo "smb" ;;
    *) echo "" ;;
  esac
}

assert_eq "$(pod_crashloop_restarts mediastack radarr-x)" "37" "crashlooping pod -> summed restarts"
assert_eq "$(pod_crashloop_restarts mediastack prowlarr-y)" "0" "healthy pod -> 0 restarts"
assert_eq "$(longhorn_rwo_volume mediastack radarr-x)" "pvc-abc123" "longhorn RWO pod -> PV name"
assert_eq "$(longhorn_rwo_volume mediastack prowlarr-y)" "" "smb pod -> no volume"
assert_eq "$(owner_deployment mediastack radarr-x)" "radarr" "pod -> owning Deployment"

# --- wait_detached: stub kc to report 'detached' immediately ---
kc() {
  case "$*" in
    "get volumes.longhorn.io pvc-abc123 -n longhorn-system -o jsonpath={.status.state}")
      echo "detached" ;;
    *) echo "" ;;
  esac
}
wait_detached pvc-abc123 10 1; assert_rc "$?" "0" "wait_detached returns 0 when state=detached"

# stub kc to never detach -> times out fast (timeout 1s, interval 1s)
kc() { echo "attached"; }
wait_detached pvc-stuck 1 1; assert_rc "$?" "1" "wait_detached returns 1 on timeout"

# --- find_and_heal_one: healthy cluster -> no heal ---
# Single namespace with one healthy pod; stub heal_workload to record calls.
HEAL_NAMESPACES="mediastack"
HEALED=""
heal_workload() { HEALED="$1/$2 vol=$3"; }
kc() {
  case "$*" in
    "get pods -n mediastack -o jsonpath={.items[*].metadata.name}") echo "prowlarr-y" ;;
    "get pod prowlarr-y -n mediastack -o jsonpath={.status.containerStatuses[*].state.waiting.reason}{.status.initContainerStatuses[*].state.waiting.reason}") echo "" ;;
    "get pod prowlarr-y -n mediastack -o jsonpath={.status.phase}") echo "Running" ;;
    *) echo "" ;;
  esac
}
out=$(find_and_heal_one)
assert_eq "$HEALED" "" "healthy cluster -> nothing healed"
case "$out" in *"no stuck workloads found"*) assert_rc 0 0 "logs no-stuck message" ;; *) assert_rc 1 0 "logs no-stuck message" ;; esac

# --- find_and_heal_one: one stuck radarr -> heal exactly once ---
HEALED=""
HEAL_COUNT=0
heal_workload() { HEAL_COUNT=$((HEAL_COUNT+1)); HEALED="$1/$2 vol=$3"; }
kc() {
  case "$*" in
    "get pods -n mediastack -o jsonpath={.items[*].metadata.name}") echo "radarr-x sonarr-z" ;;
    # radarr: crashlooping over threshold, longhorn RWO
    "get pod radarr-x -n mediastack -o jsonpath={.status.containerStatuses[*].state.waiting.reason}{.status.initContainerStatuses[*].state.waiting.reason}") echo "CrashLoopBackOff" ;;
    "get pod radarr-x -n mediastack -o jsonpath={.status.containerStatuses[*].restartCount} {.status.initContainerStatuses[*].restartCount}") echo "37 " ;;
    "get pod radarr-x -n mediastack -o jsonpath={.spec.volumes[*].persistentVolumeClaim.claimName}") echo "radarr-config" ;;
    "get pvc radarr-config -n mediastack -o jsonpath={.spec.storageClassName}") echo "longhorn" ;;
    "get pvc radarr-config -n mediastack -o jsonpath={.spec.accessModes[*]}") echo "ReadWriteOnce" ;;
    "get pvc radarr-config -n mediastack -o jsonpath={.spec.volumeName}") echo "pvc-abc123" ;;
    "get pod radarr-x -n mediastack -o jsonpath={.metadata.ownerReferences[?(@.kind==\"ReplicaSet\")].name}") echo "radarr-5d9" ;;
    "get rs radarr-5d9 -n mediastack -o jsonpath={.metadata.ownerReferences[?(@.kind==\"Deployment\")].name}") echo "radarr" ;;
    "get deploy radarr -n mediastack -o jsonpath={.metadata.annotations.mount-healer\\.vollminlab\\.com/last-healed}") echo "" ;;
    # sonarr would also be stuck, but we must stop after the first heal
    *) echo "" ;;
  esac
}
find_and_heal_one >/dev/null
assert_eq "$HEAL_COUNT" "1" "exactly one workload healed per run"
assert_eq "$HEALED" "mediastack/radarr vol=pvc-abc123" "healed the stuck radarr"

# --- cooldown suppresses re-heal ---
HEAL_COUNT=0
kc_inner() { :; }
# same as above but last-healed is 'now' -> in cooldown
NOWEPOCH=$(date -u +%s)
kc() {
  case "$*" in
    "get pods -n mediastack -o jsonpath={.items[*].metadata.name}") echo "radarr-x" ;;
    "get pod radarr-x -n mediastack -o jsonpath={.status.containerStatuses[*].state.waiting.reason}{.status.initContainerStatuses[*].state.waiting.reason}") echo "CrashLoopBackOff" ;;
    "get pod radarr-x -n mediastack -o jsonpath={.status.containerStatuses[*].restartCount} {.status.initContainerStatuses[*].restartCount}") echo "37 " ;;
    "get pod radarr-x -n mediastack -o jsonpath={.spec.volumes[*].persistentVolumeClaim.claimName}") echo "radarr-config" ;;
    "get pvc radarr-config -n mediastack -o jsonpath={.spec.storageClassName}") echo "longhorn" ;;
    "get pvc radarr-config -n mediastack -o jsonpath={.spec.accessModes[*]}") echo "ReadWriteOnce" ;;
    "get pvc radarr-config -n mediastack -o jsonpath={.spec.volumeName}") echo "pvc-abc123" ;;
    "get pod radarr-x -n mediastack -o jsonpath={.metadata.ownerReferences[?(@.kind==\"ReplicaSet\")].name}") echo "radarr-5d9" ;;
    "get rs radarr-5d9 -n mediastack -o jsonpath={.metadata.ownerReferences[?(@.kind==\"Deployment\")].name}") echo "radarr" ;;
    "get deploy radarr -n mediastack -o jsonpath={.metadata.annotations.mount-healer\\.vollminlab\\.com/last-healed}") echo "$NOWEPOCH" ;;
    *) echo "" ;;
  esac
}
find_and_heal_one >/dev/null
assert_eq "$HEAL_COUNT" "0" "cooldown suppresses re-heal"

# --- restore_orphans ---

# Test A: parked-at-0 orphan is restored
HEAL_NAMESPACES="mediastack"
DRY_RUN="false"
RESTORED=""
CLEARED=""
kc() {
  case "$*" in
    "get deploy -n mediastack -o jsonpath={.items[*].metadata.name}")
      echo "radarr" ;;
    "get deploy radarr -n mediastack -o jsonpath={.metadata.annotations.mount-healer\\.vollminlab\\.com/original-replicas}")
      echo "2" ;;
    "get deploy radarr -n mediastack -o jsonpath={.spec.replicas}")
      echo "0" ;;
    "scale deploy radarr -n mediastack --replicas=2")
      RESTORED="radarr->2" ;;
    "annotate deploy radarr -n mediastack mount-healer.vollminlab.com/original-replicas-")
      CLEARED="yes" ;;
    *) : ;;
  esac
}
restore_orphans >/dev/null
assert_eq "$RESTORED" "radarr->2" "restore_orphans rescales parked deployment"
assert_eq "$CLEARED" "yes" "restore_orphans clears original-replicas annotation"

# Test B: deployment already running is not restored
RESTORED2=""
kc() {
  case "$*" in
    "get deploy -n mediastack -o jsonpath={.items[*].metadata.name}")
      echo "radarr" ;;
    "get deploy radarr -n mediastack -o jsonpath={.metadata.annotations.mount-healer\\.vollminlab\\.com/original-replicas}")
      echo "2" ;;
    "get deploy radarr -n mediastack -o jsonpath={.spec.replicas}")
      echo "2" ;;
    "scale deploy radarr -n mediastack --replicas=2")
      RESTORED2="radarr->2" ;;
    *) : ;;
  esac
}
restore_orphans >/dev/null
assert_eq "$RESTORED2" "" "restore_orphans skips an already-running deployment"

printf '\n%s failures\n' "$FAILS"
[ "$FAILS" = "0" ]
