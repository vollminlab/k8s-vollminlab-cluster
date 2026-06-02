# Volsync moverSecurityContext Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken mediastack Flux kustomization and give Volsync restic movers the correct UID so they can read app-owned files during backup.

**Architecture:** PR #748 accidentally introduced `spec.restic.exclude` — a field not in the Volsync CRD schema — to four `ReplicationSource` files. This causes Flux's dry-run to fail, blocking reconciliation of the entire mediastack kustomization. As a side effect, filebrowser's correct `moverSecurityContext: 1000` fix (also in PR #748) was never applied. This PR removes the invalid `exclude` fields and adds `moverSecurityContext` (with the app's UID) to all four arr app `ReplicationSource` files.

**Tech Stack:** Flux CD, Volsync 0.15.0, Kubernetes 1.34

---

### Task 1: Branch

**Files:** none

- [ ] **Create branch from current main**

```bash
git checkout main && git pull
git checkout -b fix/volsync-mover-security-context
```

---

### Task 2: Fix radarr ReplicationSource

**Files:**
- Modify: `clusters/vollminlab-cluster/mediastack/volsync/radarr-config-replicationsource.yaml`

- [ ] **Replace file contents** — remove `exclude`, add `moverSecurityContext`:

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationSource
metadata:
  name: radarr-config-restic
  namespace: mediastack
spec:
  sourcePVC: pvc-radarr-config
  trigger:
    schedule: "0 3 * * *"
  restic:
    pruneIntervalDays: 7
    repository: volsync-radarr-config-restic
    retain:
      daily: 7
      weekly: 4
      monthly: 3
    copyMethod: Clone
    cacheCapacity: 1Gi
    cacheStorageClassName: longhorn
    moverSecurityContext:
      runAsUser: 568
      runAsGroup: 568
      fsGroup: 568
```

---

### Task 3: Fix sonarr ReplicationSource

**Files:**
- Modify: `clusters/vollminlab-cluster/mediastack/volsync/sonarr-config-replicationsource.yaml`

- [ ] **Replace file contents** — remove `exclude`, add `moverSecurityContext`:

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationSource
metadata:
  name: sonarr-config-restic
  namespace: mediastack
spec:
  sourcePVC: pvc-sonarr-config
  trigger:
    schedule: "0 3 * * *"
  restic:
    pruneIntervalDays: 7
    repository: volsync-sonarr-config-restic
    retain:
      daily: 7
      weekly: 4
      monthly: 3
    copyMethod: Clone
    cacheCapacity: 1Gi
    cacheStorageClassName: longhorn
    moverSecurityContext:
      runAsUser: 568
      runAsGroup: 568
      fsGroup: 568
```

---

### Task 4: Fix prowlarr ReplicationSource

**Files:**
- Modify: `clusters/vollminlab-cluster/mediastack/volsync/prowlarr-config-replicationsource.yaml`

- [ ] **Replace file contents** — remove `exclude`, add `moverSecurityContext`:

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationSource
metadata:
  name: prowlarr-config-restic
  namespace: mediastack
spec:
  sourcePVC: pvc-prowlarr-config
  trigger:
    schedule: "0 3 * * *"
  restic:
    pruneIntervalDays: 7
    repository: volsync-prowlarr-config-restic
    retain:
      daily: 7
      weekly: 4
      monthly: 3
    copyMethod: Clone
    cacheCapacity: 1Gi
    cacheStorageClassName: longhorn
    moverSecurityContext:
      runAsUser: 568
      runAsGroup: 568
      fsGroup: 568
```

---

### Task 5: Fix readarr ReplicationSource

**Files:**
- Modify: `clusters/vollminlab-cluster/mediastack/volsync/readarr-config-replicationsource.yaml`

- [ ] **Replace file contents** — remove `exclude`, add `moverSecurityContext`:

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationSource
metadata:
  name: readarr-config-restic
  namespace: mediastack
spec:
  sourcePVC: pvc-readarr-config
  trigger:
    schedule: "0 3 * * *"
  restic:
    pruneIntervalDays: 7
    repository: volsync-readarr-config-restic
    retain:
      daily: 7
      weekly: 4
      monthly: 3
    copyMethod: Clone
    cacheCapacity: 1Gi
    cacheStorageClassName: longhorn
    moverSecurityContext:
      runAsUser: 568
      runAsGroup: 568
      fsGroup: 568
```

---

### Task 6: Commit and PR

**Files:** all four modified files

- [ ] **Stage and commit**

```bash
git add \
  clusters/vollminlab-cluster/mediastack/volsync/radarr-config-replicationsource.yaml \
  clusters/vollminlab-cluster/mediastack/volsync/sonarr-config-replicationsource.yaml \
  clusters/vollminlab-cluster/mediastack/volsync/prowlarr-config-replicationsource.yaml \
  clusters/vollminlab-cluster/mediastack/volsync/readarr-config-replicationsource.yaml
git commit -m "fix(volsync): replace invalid exclude field with moverSecurityContext for arr apps"
```

- [ ] **Push and open PR**

```bash
git push -u origin fix/volsync-mover-security-context
gh pr create \
  --title "fix(volsync): replace invalid exclude with moverSecurityContext for arr apps" \
  --body "$(cat <<'EOF'
## Summary

- Removes `spec.restic.exclude` from radarr/sonarr/prowlarr/readarr ReplicationSources — this field is not in the Volsync CRD schema and was causing the entire mediastack Flux kustomization dry-run to fail since PR #748 merged
- Adds `moverSecurityContext: runAsUser/runAsGroup/fsGroup: 568` to those four files so the restic mover runs as the app UID and can read all PVC contents
- Filebrowser's `moverSecurityContext: 1000` fix (already committed in PR #748) will be applied automatically once this unblocks the kustomization

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Verify CI passes** — the kyverno-cli check should pass since ReplicationSource is not a pod-bearing resource

---

### Task 7: Verify reconciliation after merge

- [ ] **After PR merges, force reconciliation**

```bash
flux reconcile kustomization mediastack --with-source
```

Expected output: `► annotating Kustomization mediastack in flux-system namespace` followed by `✔ Kustomization reconciliation completed`

- [ ] **Confirm kustomization is healthy**

```bash
flux get kustomization mediastack
```

Expected: `READY` column shows `True`

- [ ] **Confirm moverSecurityContext is live on all 5 apps**

```bash
for app in radarr sonarr prowlarr readarr filebrowser; do
  echo -n "$app: "
  kubectl get replicationsource ${app}-config-restic -n mediastack \
    -o jsonpath='{.spec.restic.moverSecurityContext.runAsUser}'
  echo
done
```

Expected output:
```
radarr: 568
sonarr: 568
prowlarr: 568
readarr: 568
filebrowser: 1000
```

- [ ] **Trigger a manual backup for one app to confirm it completes cleanly**

```bash
kubectl annotate replicationsource radarr-config-restic -n mediastack \
  volsync.backube/trigger-immediate-reconciliation="$(date +%s)"
```

Watch for a new job pod: `kubectl get pods -n mediastack | grep volsync-src-radarr`

Wait for it to complete (not Error). Then verify no permission errors:

```bash
kubectl logs -n mediastack $(kubectl get pods -n mediastack \
  --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{.items[-1].metadata.name}' \
  | grep volsync 2>/dev/null || \
  kubectl get pods -n mediastack --sort-by=.metadata.creationTimestamp \
  -o name | grep radarr-config-restic | tail -1 | sed 's|pod/||') \
  2>/dev/null | grep -E "error|Warning|snapshot"
```

Expected: a `snapshot XXXXXXXX saved` line with no `permission denied` errors.
