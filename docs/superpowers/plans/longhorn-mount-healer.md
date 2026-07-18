# Longhorn Mount Healer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `longhorn-mount-healer` CronJob that auto-clears storage-induced crashloops by codifying the proven `scale 0 → wait until the Longhorn volume detaches → scale back` runbook.

**Architecture:** A raw-manifest app in `kube-system` (modeled on the existing `etcd-defrag` CronJob): a ServiceAccount + ClusterRole/Binding, a POSIX-sh script (`heal.sh`) delivered via a kustomize `configMapGenerator`, and a CronJob running `alpine/kubectl` every 10 minutes. The script is dependency-free (pure `kubectl -o jsonpath`, no `jq`/`apk`) so each helper is unit-testable with a stubbed `kubectl`. Safety is enforced by a namespace allowlist, a one-heal-per-run cap, a per-Deployment cooldown, and a crash-safe orphan-restore that runs first every invocation.

**Tech Stack:** Kubernetes CronJob, `docker.io/alpine/kubectl:1.33.4`, POSIX `sh`, kustomize `configMapGenerator`, Flux CD, Longhorn `volumes.longhorn.io` CRs.

**Scope:** This plan is **PR 1 — Layer 1 (the cure) only**, per the design spec (`docs/superpowers/specs/storage-crashloop-resiliency-design.md`). Layer 2 (`default-data-locality`) and Layer 3 (symptom `PrometheusRule`) are a separate follow-up plan.

---

## File Structure

All paths relative to repo root.

| File | Responsibility |
|------|----------------|
| `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh` | The healer logic (POSIX sh). Single source of truth — mounted into the CronJob *and* sourced by the test. |
| `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh` | Unit tests. Stubs `kubectl`, sources `heal.sh`, asserts helper behavior. Not deployed (not referenced by kustomization). |
| `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/rbac.yaml` | ServiceAccount (kube-system) + ClusterRole + ClusterRoleBinding. |
| `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/cronjob.yaml` | CronJob (`*/10`), mounts the generated script ConfigMap, sets tunables via env. |
| `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/kustomization.yaml` | Aggregates `rbac.yaml` + `cronjob.yaml`, generates the script ConfigMap from `heal.sh`. |
| `clusters/vollminlab-cluster/kube-system/kustomization.yaml` (MODIFY) | Add `./longhorn-mount-healer/app` to the `resources` list so Flux reconciles it. |

**Design constants (used throughout):**
- App/category labels: `app: longhorn-mount-healer`, `env: production`, `category: storage`
- Allowlist namespaces (env `HEAL_NAMESPACES`): `mediastack monitoring harbor`
- Annotations on the Deployment:
  - `mount-healer.vollminlab.com/original-replicas` — replica count captured before scaling to 0
  - `mount-healer.vollminlab.com/last-healed` — Unix epoch of the last heal (drives cooldown)

---

## Task 1: Scaffold the app directory and Flux wiring

**Files:**
- Create: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/kustomization.yaml`
- Create: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh` (placeholder, fleshed out in Tasks 3–6)
- Modify: `clusters/vollminlab-cluster/kube-system/kustomization.yaml`

- [ ] **Step 1: Create a minimal `heal.sh` placeholder so `configMapGenerator` has a file**

```sh
#!/bin/sh
# longhorn-mount-healer — placeholder, implemented in later tasks
echo "healer not yet implemented"
```

- [ ] **Step 2: Create the app `kustomization.yaml` with the script generator**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: longhorn-mount-healer
resources:
  - rbac.yaml
  - cronjob.yaml
configMapGenerator:
  - name: longhorn-mount-healer-script
    files:
      - heal.sh
generatorOptions:
  disableNameSuffixHash: true
  labels:
    app: longhorn-mount-healer
    env: production
    category: storage
```

Note: `rbac.yaml` and `cronjob.yaml` don't exist yet — `kustomize build` will fail until Tasks 2 and 7. That's expected; this step only lays down the file.

- [ ] **Step 3: Wire the app into the kube-system aggregation**

Modify `clusters/vollminlab-cluster/kube-system/kustomization.yaml` — add the new app to `resources` in alphabetical position (after `kubeadm-cert-renew`, before `metrics-server`):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: kube-system
resources:
  - namespace.yaml
  - ./descheduler/app
  - ./etcd-defrag/app
  - ./kubeadm-cert-monitor/app
  - ./kubeadm-cert-renew/app
  - ./longhorn-mount-healer/app
  - ./metrics-server/app
  - ./smb-csi-driver/app
```

- [ ] **Step 4: Commit**

```bash
git add clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/kustomization.yaml \
        clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh \
        clusters/vollminlab-cluster/kube-system/kustomization.yaml
git commit -m "feat(kube-system): scaffold longhorn-mount-healer app + Flux wiring"
```

---

## Task 2: RBAC (ServiceAccount + ClusterRole + ClusterRoleBinding)

The healer reads pods/PVCs and patches Deployment scale across the allowlist namespaces, reads Longhorn `Volume` CRs in `longhorn-system`, and creates Events — so it needs a **ClusterRole** (cross-namespace), unlike `etcd-defrag`'s namespaced Role.

**Files:**
- Create: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/rbac.yaml`

- [ ] **Step 1: Write the RBAC manifest**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: longhorn-mount-healer
  namespace: kube-system
  labels:
    app: longhorn-mount-healer
    env: production
    category: storage
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: longhorn-mount-healer
  labels:
    app: longhorn-mount-healer
    env: production
    category: storage
rules:
  - apiGroups: [""]
    resources: ["pods", "persistentvolumeclaims"]
    verbs: ["get", "list"]
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["get", "list", "create"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["get", "list", "patch"]
  - apiGroups: ["apps"]
    resources: ["deployments/scale"]
    verbs: ["get", "update", "patch"]
  - apiGroups: ["longhorn.io"]
    resources: ["volumes"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: longhorn-mount-healer
  labels:
    app: longhorn-mount-healer
    env: production
    category: storage
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: longhorn-mount-healer
subjects:
  - kind: ServiceAccount
    name: longhorn-mount-healer
    namespace: kube-system
```

- [ ] **Step 2: Commit**

```bash
git add clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/rbac.yaml
git commit -m "feat(kube-system): longhorn-mount-healer RBAC (ClusterRole + binding)"
```

---

## Task 3: Script foundation + pure helpers (`sum_ints`, `in_cooldown`) with tests

Establish the test harness and the `kc` wrapper, then the two pure helpers that need no Kubernetes.

**Files:**
- Modify: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh`
- Create: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`

- [ ] **Step 1: Write the failing test harness + first tests**

Create `heal_test.sh`:

```sh
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

printf '\n%s failures\n' "$FAILS"
[ "$FAILS" = "0" ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `sh clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`
Expected: FAIL — `heal.sh` is still the placeholder, so `sum_ints`/`in_cooldown` are undefined (`sum_ints: not found`).

- [ ] **Step 3: Replace `heal.sh` with the foundation + the two helpers**

Overwrite `heal.sh`:

```sh
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `sh clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`
Expected: PASS — `0 failures`.

- [ ] **Step 5: Commit**

```bash
git add clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh \
        clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh
git commit -m "feat(healer): script foundation + sum_ints/in_cooldown helpers with tests"
```

---

## Task 4: Detection helpers (`pod_crashloop_restarts`, `longhorn_rwo_volume`, `owner_deployment`) with tests

These call `kubectl`, so tests stub `kc` with a dispatcher that echoes canned jsonpath output keyed on the arguments.

**Files:**
- Modify: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh`
- Modify: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`

- [ ] **Step 1: Add failing tests for the detection helpers**

Append to `heal_test.sh` before the final `printf '\n%s failures\n'` line:

```sh
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
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `sh clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`
Expected: FAIL on the five new assertions (`pod_crashloop_restarts: not found`, etc.); the Task 3 assertions still pass.

- [ ] **Step 3: Add the detection helpers to `heal.sh`**

Insert these functions immediately above the `main()` definition:

```sh
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `sh clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`
Expected: PASS — `0 failures`.

- [ ] **Step 5: Commit**

```bash
git add clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh \
        clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh
git commit -m "feat(healer): detection helpers (crashloop, longhorn-rwo, owner) with tests"
```

---

## Task 5: Action helpers (`wait_detached`, `emit_event`, `heal_workload`)

These perform side effects (scale, annotate, poll). `wait_detached` is testable; `heal_workload` is verified end-to-end in Task 6's orchestration test and the controlled live test (Verification).

**Files:**
- Modify: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh`
- Modify: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`

- [ ] **Step 1: Add a failing test for `wait_detached`**

Append to `heal_test.sh` before the final summary line:

```sh
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
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `sh clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`
Expected: FAIL — `wait_detached: not found`.

- [ ] **Step 3: Add the action helpers to `heal.sh`**

Insert above `main()`:

```sh
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `sh clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`
Expected: PASS — `0 failures`.

- [ ] **Step 5: Commit**

```bash
git add clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh \
        clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh
git commit -m "feat(healer): action helpers wait_detached/emit_event/heal_workload"
```

---

## Task 6: Orchestration (`restore_orphans`, `find_and_heal_one`, `main`) with an end-to-end stub test

Ties detection + actions together with the safety invariants: orphan-restore runs first, then at most one workload is healed per run, gated by the cooldown.

**Files:**
- Modify: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh`
- Modify: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`

- [ ] **Step 1: Add failing orchestration tests**

Append to `heal_test.sh` before the final summary line:

```sh
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
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `sh clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`
Expected: FAIL — `find_and_heal_one: not found`.

- [ ] **Step 3: Add orchestration to `heal.sh` and replace `main()`**

Replace the placeholder `main()` with the orchestration (and add `restore_orphans` + `find_and_heal_one` above it):

```sh
# restore_orphans: repair any Deployment a prior interrupted heal left at 0.
# Runs FIRST every invocation so a dead healer pod can't park a workload down.
restore_orphans() {
  for ns in $HEAL_NAMESPACES; do
    deps=$(kc get deploy -n "$ns" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)
    for dep in $deps; do
      [ -n "$dep" ] || continue
      orig=$(kc get deploy "$dep" -n "$ns" -o jsonpath="{.metadata.annotations.$JP_ORIG}" 2>/dev/null)
      [ -n "$orig" ] || continue
      cur=$(kc get deploy "$dep" -n "$ns" -o jsonpath='{.spec.replicas}')
      if [ "$cur" = "0" ] && [ "$orig" != "0" ]; then
        log "orphan-restore $ns/$dep -> $orig"
        [ "$DRY_RUN" = "true" ] && continue
        kc scale deploy "$dep" -n "$ns" --replicas="$orig"
        kc annotate deploy "$dep" -n "$ns" "${ANN_ORIG}-" >/dev/null 2>&1 || true
        kc annotate deploy "$dep" -n "$ns" "$ANN_LAST=$(date -u +%s)" --overwrite
        emit_event "$ns" "$dep" RestoredAfterInterrupt Warning "Restored replicas to $orig after an interrupted heal"
      fi
    done
  done
}

# pod_mount_wedged: exit 0 if pod is Pending with a FailedMount 'busy' event
# (the post-scale reattach race). $1=ns $2=pod
pod_mount_wedged() {
  ns="$1"; pod="$2"
  phase=$(kc get pod "$pod" -n "$ns" -o jsonpath='{.status.phase}')
  [ "$phase" = "Pending" ] || return 1
  msgs=$(kc get events -n "$ns" --field-selector "involvedObject.name=$pod,reason=FailedMount" -o jsonpath='{.items[*].message}' 2>/dev/null)
  case "$msgs" in
    *"already mounted or mount point busy"*) return 0 ;;
    *"volume is already exclusively attached"*) return 0 ;;
    *) return 1 ;;
  esac
}

# find_and_heal_one: heal at most ONE stuck workload, gated by cooldown.
find_and_heal_one() {
  now=$(date -u +%s)
  for ns in $HEAL_NAMESPACES; do
    pods=$(kc get pods -n "$ns" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)
    for pod in $pods; do
      [ -n "$pod" ] || continue
      restarts=$(pod_crashloop_restarts "$ns" "$pod")
      if [ "$restarts" -gt "$RESTART_THRESHOLD" ]; then
        :
      elif pod_mount_wedged "$ns" "$pod"; then
        :
      else
        continue
      fi
      vol=$(longhorn_rwo_volume "$ns" "$pod")
      [ -n "$vol" ] || continue
      dep=$(owner_deployment "$ns" "$pod")
      [ -n "$dep" ] || { log "skip $ns/$pod: no Deployment owner"; continue; }
      last=$(kc get deploy "$dep" -n "$ns" -o jsonpath="{.metadata.annotations.$JP_LAST}" 2>/dev/null)
      if in_cooldown "$last" "$now" "$COOLDOWN_SECONDS"; then
        log "skip $ns/$dep: in cooldown (last-healed=$last)"; continue
      fi
      heal_workload "$ns" "$dep" "$vol"
      return 0
    done
  done
  log "no stuck workloads found"
}

main() {
  log "longhorn-mount-healer start (ns='$HEAL_NAMESPACES' threshold=$RESTART_THRESHOLD dry_run=$DRY_RUN)"
  restore_orphans
  find_and_heal_one
  log "longhorn-mount-healer done"
}

[ -n "${HEAL_TEST:-}" ] || main "$@"
```

Note: delete the old placeholder `main()` and the trailing `[ -n "${HEAL_TEST:-}" ] || main "$@"` line from Task 3 so there is exactly one of each.

- [ ] **Step 4: Run tests to verify they pass**

Run: `sh clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`
Expected: PASS — `0 failures`.

- [ ] **Step 5: Commit**

```bash
git add clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal.sh \
        clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh
git commit -m "feat(healer): orchestration (orphan-restore, one-per-run, cooldown) with e2e stub tests"
```

---

## Task 7: CronJob manifest

**Files:**
- Create: `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/cronjob.yaml`

- [ ] **Step 1: Write the CronJob**

Modeled on `etcd-defrag/app/cronjob.yaml` (same image, resource shape, `concurrencyPolicy: Forbid`). Mounts the generated script ConfigMap read-only and runs it.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: longhorn-mount-healer
  namespace: kube-system
  labels:
    app: longhorn-mount-healer
    env: production
    category: storage
spec:
  schedule: "*/10 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      ttlSecondsAfterFinished: 3600
      backoffLimit: 0
      template:
        metadata:
          labels:
            app: longhorn-mount-healer
            env: production
            category: storage
        spec:
          serviceAccountName: longhorn-mount-healer
          restartPolicy: Never
          containers:
            - name: longhorn-mount-healer
              image: docker.io/alpine/kubectl:1.33.4
              command: ["/bin/sh", "/scripts/heal.sh"]
              env:
                - name: HEAL_NAMESPACES
                  value: "mediastack monitoring harbor"
                - name: RESTART_THRESHOLD
                  value: "5"
                - name: COOLDOWN_SECONDS
                  value: "21600"
                - name: DETACH_TIMEOUT_SECONDS
                  value: "180"
                - name: POLL_INTERVAL_SECONDS
                  value: "5"
                - name: DRY_RUN
                  value: "false"
              volumeMounts:
                - name: script
                  mountPath: /scripts
                  readOnly: true
              resources:
                requests:
                  cpu: 10m
                  memory: 32Mi
                limits:
                  cpu: 100m
                  memory: 64Mi
          volumes:
            - name: script
              configMap:
                name: longhorn-mount-healer-script
```

- [ ] **Step 2: Commit**

```bash
git add clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/cronjob.yaml
git commit -m "feat(kube-system): longhorn-mount-healer CronJob (*/10)"
```

---

## Task 8: Validate, run the full test suite, and open the PR

**Files:** none (validation + PR).

- [ ] **Step 1: Run the unit test suite one final time**

Run: `sh clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/heal_test.sh`
Expected: PASS — `0 failures`.

- [ ] **Step 2: Validate the manifests build**

Run: `kubectl kustomize clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app`
Expected: clean YAML output containing the ServiceAccount, ClusterRole, ClusterRoleBinding, CronJob, and a `ConfigMap` named exactly `longhorn-mount-healer-script` (no hash suffix) carrying the `app/env/category` labels and the `heal.sh` key.

Then build the whole namespace to confirm the aggregation edit is valid:
Run: `kubectl kustomize clusters/vollminlab-cluster/kube-system`
Expected: builds without error and includes the healer resources.

- [ ] **Step 3: Sanity-check Kyverno-relevant fields**

Confirm by inspection of the build output:
- CronJob pod template has `app`, `env`, `category` labels and CPU+memory requests+limits (resource-limits + required-labels policies).
- Image is pinned `docker.io/alpine/kubectl:1.33.4` (no `:latest`).
- No `privileged`, no `hostPath`, not in `default` namespace.
- Generated ConfigMap carries the three labels (required-labels on ConfigMaps).

- [ ] **Step 4: Push the branch and open the PR**

```bash
git push -u origin feat/storage-crashloop-resiliency
gh pr create --title "feat(kube-system): longhorn-mount-healer — auto-clear storage-induced crashloops" \
  --body "Implements Layer 1 of docs/superpowers/specs/storage-crashloop-resiliency-design.md.

CronJob in kube-system that codifies the proven scale-0 -> wait-detached -> scale-back
runbook for pods stuck in CrashLoopBackOff (or wedged Pending) on a Longhorn RWO volume.
Safety: namespace allowlist (mediastack/monitoring/harbor), one heal per run, per-Deployment
6h cooldown, and a crash-safe orphan-restore that runs first every invocation.

Layers 2 (data-locality nudge) and 3 (symptom alert) follow in a separate PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Do not merge — wait for explicit approval and green CI (Kyverno test + gitleaks).

---

## Post-merge controlled verification (operational, not part of the PR)

After Flux reconciles the merged PR:

1. `kubectl get cronjob longhorn-mount-healer -n kube-system` → schedule `*/10`, recently scheduled.
2. Inspect a steady-state job log: `kubectl logs -n kube-system job/<latest-healer-job>` → ends with `no stuck workloads found` and `done`; nothing scaled.
3. RBAC spot-check:
   `kubectl auth can-i patch deployments/scale -n mediastack --as=system:serviceaccount:kube-system:longhorn-mount-healer` → `yes`;
   `kubectl auth can-i get volumes.longhorn.io -n longhorn-system --as=system:serviceaccount:kube-system:longhorn-mount-healer` → `yes`.
4. **DRY_RUN rehearsal (optional):** temporarily set `DRY_RUN=true` (env edit on the CronJob, not committed) and induce a crashloop on a throwaway allowlisted Deployment past 5 restarts; confirm the next job logs `DRY_RUN: would heal ...` and does **not** scale it. Revert.
5. **Live heal:** with `DRY_RUN=false`, replay against a genuinely stuck workload (or the next real incident); confirm the job scales it to 0, waits for `detached`, scales it back, and emits a `HealedStaleMount` Event (`kubectl get events -n <ns> --field-selector reason=HealedStaleMount`).
6. **Crash-safe restore:** while a heal is mid-flight (workload at 0 with the `original-replicas` annotation), delete the running job pod; confirm the next `*/10` run logs `orphan-restore` and brings the workload back.

---

## Self-Review

**Spec coverage** (against `storage-crashloop-resiliency-design.md`, Layer 1 only — Layers 2/3 are out of scope for this plan):
- Detection: CrashLoopBackOff>threshold → `pod_crashloop_restarts` (Task 4); wedged Pending/FailedMount → `pod_mount_wedged` (Task 6); Longhorn RWO backing → `longhorn_rwo_volume` (Task 4). ✓
- Action runbook (record replicas → scale 0 → poll until detached → scale back → Event): `heal_workload` (Task 5). ✓
- Namespace allowlist: `HEAL_NAMESPACES` env + loop (Tasks 6/7). ✓
- One-per-run: `find_and_heal_one` returns after first heal (Task 6, tested). ✓
- Per-workload cooldown: `in_cooldown` + `ANN_LAST` (Tasks 3/6, tested). ✓
- Crash-safe restore: `restore_orphans` runs first in `main` (Task 6). ✓
- Detach-timeout fallback (restore anyway + Warning event): `heal_workload` else-branch (Task 5). ✓
- Deployments-only: owner resolution stops at Deployment; StatefulSets never matched (Task 4). ✓
- RBAC least-privilege: ClusterRole scoped to the exact verbs/resources (Task 2). ✓
- Flux wiring (single index — no HelmRepository): kube-system aggregation edit (Task 1). ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. ✓

**Type/name consistency:** `kc`, `log`, `sum_ints`, `in_cooldown`, `pod_crashloop_restarts`, `longhorn_rwo_volume`, `owner_deployment`, `wait_detached`, `emit_event`, `heal_workload`, `restore_orphans`, `pod_mount_wedged`, `find_and_heal_one`, `main` — names used in tests match definitions. ConfigMap name `longhorn-mount-healer-script` matches between `kustomization.yaml` (generator) and `cronjob.yaml` (volume). Annotation keys `ANN_ORIG`/`ANN_LAST` and their jsonpath-escaped twins `JP_ORIG`/`JP_LAST` are consistent. ✓
