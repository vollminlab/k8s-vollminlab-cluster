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

printf '\n%s failures\n' "$FAILS"
[ "$FAILS" = "0" ]
