# Reloader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Stakater Reloader as a cluster-wide controller so that annotated Deployments/StatefulSets/DaemonSets automatically roll when their ConfigMaps or SealedSecrets change.

**Architecture:** Reloader runs as a single Deployment in a dedicated `reloader` namespace, watching all namespaces for ConfigMap/Secret changes. Resources opt in via the `reloader.stakater.com/auto: "true"` annotation. No ingress, PVCs, or secrets required.

**Tech Stack:** Stakater Reloader chart v2.2.11 (app v1.4.16) via `https://stakater.github.io/stakater-charts`, Flux CD GitOps, SealedSecrets.

---

## File Map

**Create:**
- `clusters/vollminlab-cluster/reloader/namespace.yaml`
- `clusters/vollminlab-cluster/reloader/kustomization.yaml`
- `clusters/vollminlab-cluster/reloader/reloader/app/helmrelease.yaml`
- `clusters/vollminlab-cluster/reloader/reloader/app/configmap.yaml`
- `clusters/vollminlab-cluster/reloader/reloader/app/kustomization.yaml`
- `clusters/vollminlab-cluster/flux-system/flux-kustomizations/reloader-kustomization.yaml`
- `clusters/vollminlab-cluster/flux-system/repositories/reloader-helmrepository.yaml`

**Modify:**
- `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml` — add `reloader-kustomization.yaml` entry (alphabetical order)
- `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml` — add `reloader-helmrepository.yaml` entry (alphabetical order)
- `docs/roadmap.md` — mark Volsync, Goldilocks, Trivy, and Reloader as done

---

### Task 1: Create new branch

- [ ] **Step 1: Checkout main and pull latest**

```bash
git checkout main && git pull
```

- [ ] **Step 2: Create feature branch**

```bash
git checkout -b feat/reloader
```

---

### Task 2: Create namespace and namespace-level kustomization

**Files:**
- Create: `clusters/vollminlab-cluster/reloader/namespace.yaml`
- Create: `clusters/vollminlab-cluster/reloader/kustomization.yaml`

- [ ] **Step 1: Create namespace.yaml**

```yaml
# clusters/vollminlab-cluster/reloader/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: reloader
  labels:
    app: reloader
    env: production
    category: core
```

- [ ] **Step 2: Create namespace-level kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/reloader/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: reloader
  labels:
    app: reloader
    env: production
    category: core
resources:
  - namespace.yaml
  - reloader/app
```

- [ ] **Step 3: Verify YAML syntax**

```bash
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/reloader/namespace.yaml
```

Expected: `namespace/reloader created (dry run)`

---

### Task 3: Create app manifests (HelmRelease + ConfigMap)

**Files:**
- Create: `clusters/vollminlab-cluster/reloader/reloader/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/reloader/reloader/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/reloader/reloader/app/kustomization.yaml`

- [ ] **Step 1: Create helmrelease.yaml**

```yaml
# clusters/vollminlab-cluster/reloader/reloader/app/helmrelease.yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: reloader
  namespace: reloader
  labels:
    app: reloader
    env: production
    category: core
spec:
  interval: 10m
  chart:
    spec:
      chart: reloader
      version: 2.2.11
      sourceRef:
        kind: HelmRepository
        name: reloader-repo
        namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: reloader-values
      valuesKey: values.yaml
```

- [ ] **Step 2: Create configmap.yaml**

```yaml
# clusters/vollminlab-cluster/reloader/reloader/app/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: reloader-values
  namespace: reloader
  labels:
    app: reloader
    env: production
    category: core
data:
  values.yaml: |
    reloader:
      watchGlobally: true
      deployment:
        resources:
          requests:
            cpu: 10m
            memory: 32Mi
          limits:
            cpu: 100m
            memory: 128Mi
```

- [ ] **Step 3: Create app kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/reloader/reloader/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: reloader-deployment
  namespace: flux-system
  labels:
    app: reloader
    env: production
    category: core
resources:
  - helmrelease.yaml
  - configmap.yaml
```

- [ ] **Step 4: Verify YAML syntax**

```bash
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/reloader/reloader/app/helmrelease.yaml
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/reloader/reloader/app/configmap.yaml
```

Expected: both return `(dry run)` with no errors.

---

### Task 4: Create HelmRepository and Flux Kustomization CR

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/reloader-helmrepository.yaml`
- Create: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/reloader-kustomization.yaml`

- [ ] **Step 1: Create reloader-helmrepository.yaml**

```yaml
# clusters/vollminlab-cluster/flux-system/repositories/reloader-helmrepository.yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: reloader-repo
  namespace: flux-system
  labels:
    app: reloader
    env: production
    category: core
spec:
  interval: 5m
  url: https://stakater.github.io/stakater-charts
  timeout: 3m
```

- [ ] **Step 2: Create reloader-kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/flux-system/flux-kustomizations/reloader-kustomization.yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: reloader
  namespace: flux-system
  labels:
    app: reloader
    env: production
    category: core
spec:
  interval: 10m
  path: ./clusters/vollminlab-cluster/reloader
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
  targetNamespace: reloader
  timeout: 5m
  dependsOn:
    - name: sealed-secrets
```

- [ ] **Step 3: Verify YAML syntax**

```bash
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/flux-system/repositories/reloader-helmrepository.yaml
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/flux-system/flux-kustomizations/reloader-kustomization.yaml
```

Expected: both return `(dry run)` with no errors.

---

### Task 5: Wire into Flux indexes

**Files:**
- Modify: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`

- [ ] **Step 1: Add reloader-kustomization.yaml to flux-kustomizations index**

Open `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml` and add `- reloader-kustomization.yaml` in alphabetical order alongside the other entries. It belongs between `renovate-kustomization.yaml` and `sealed-secrets-kustomization.yaml`.

- [ ] **Step 2: Add reloader-helmrepository.yaml to repositories index**

Open `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml` and add `- reloader-helmrepository.yaml` in alphabetical order. It belongs between `radarr-ocirepository.yaml` / `readarr-ocirepository.yaml` and `renovate-ocirepository.yaml`.

- [ ] **Step 3: Verify both index files are valid**

```bash
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml
```

Expected: both return `(dry run)` with no errors.

---

### Task 6: Update roadmap

**Files:**
- Modify: `docs/roadmap.md`

- [ ] **Step 1: Mark Volsync (1.6) as done**

Find the line `**Status:** \`planned\`` under `### 1.6 Volsync` and replace with:

```
**Status:** `done` — PRs #728–#732
```

Also replace the bullet list body with a brief summary of what was deployed:

```
Restic-based PVC replication to Backblaze B2 for 13 PVCs (CNPG clusters, Harbor registry, Longhorn app volumes). `ReplicationSource` CRs with 15-min sync interval. Scoped B2 application key. CSI VolumeSnapshot CRDs deployed separately. Metrics endpoint secured after TargetDown alert (PR #732).
```

- [ ] **Step 2: Mark Goldilocks (2.3) as done**

Find the line `**Status:** \`planned\`` under `### 2.3 Goldilocks` and replace with:

```
**Status:** `done` — PRs #721, #734
```

Replace body with:

```
Goldilocks VPA recommender deployed in `goldilocks` namespace (PR #721). VPA recommendations enabled for all app namespaces (PR #734). Resource limits right-sized across the cluster based on Goldilocks data (PR #735).
```

- [ ] **Step 3: Mark Reloader (3.7) as done**

Find `**Status:** \`planned\` (priority: next — resolves active friction)` under `### 3.7 Reloader` and replace with:

```
**Status:** `done` — this PR
```

Replace body with:

```
Stakater Reloader deployed in `reloader` namespace, watching all namespaces. Resources opt in via `reloader.stakater.com/auto: "true"` annotation on Deployments, StatefulSets, and DaemonSets. ConfigMap or SealedSecret changes trigger automatic rolling restarts without manual `kubectl rollout restart`.
```

- [ ] **Step 4: Mark Trivy (3.9) as done**

Find `**Status:** \`planned\`` under `### 3.9 Trivy Operator` and replace with:

```
**Status:** `done` — PR #721
```

Replace body with:

```
Trivy Operator deployed in `trivy-system` namespace alongside Goldilocks. Scans all running workloads continuously; generates `VulnerabilityReport` and `ConfigAuditReport` CRs. DMZ and control-plane node tolerations added (PR #722). MinIO concurrent scan jobs throttled to prevent timestamp ordering issues (PR #724).
```

- [ ] **Step 5: Add entries to the Completed table**

In the `## Completed` section at the bottom, add these rows (keep table sorted chronologically):

```markdown
| Volsync — PVC replication to B2 | PRs #728–#732 — restic ReplicationSources for 13 PVCs, 15-min sync, scoped B2 key |
| Goldilocks VPA recommender | PRs #721, #734, #735 — VPA recommendations enabled cluster-wide, limits right-sized |
| Trivy Operator | PRs #721–#722, #724 — runtime vulnerability + config audit scanning, all nodes tolerated |
| Stakater Reloader | This PR — auto rolling restarts on ConfigMap/Secret changes, opt-in via annotation |
```

---

### Task 7: Commit and open PR

- [ ] **Step 1: Stage all new and modified files explicitly**

```bash
git add \
  clusters/vollminlab-cluster/reloader/namespace.yaml \
  clusters/vollminlab-cluster/reloader/kustomization.yaml \
  clusters/vollminlab-cluster/reloader/reloader/app/helmrelease.yaml \
  clusters/vollminlab-cluster/reloader/reloader/app/configmap.yaml \
  clusters/vollminlab-cluster/reloader/reloader/app/kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/flux-kustomizations/reloader-kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/repositories/reloader-helmrepository.yaml \
  clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml \
  docs/roadmap.md
```

- [ ] **Step 2: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(reloader): deploy Stakater Reloader and update roadmap

Deploys Stakater Reloader v1.4.16 (chart 2.2.11) in a dedicated
`reloader` namespace. Watches all namespaces; resources opt in via
the `reloader.stakater.com/auto: "true"` annotation.

Also marks Volsync, Goldilocks, Trivy, and Reloader as done in
the roadmap.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/reloader
gh pr create \
  --title "feat(reloader): deploy Stakater Reloader" \
  --body "$(cat <<'EOF'
## Summary

- Deploys Stakater Reloader v1.4.16 (chart 2.2.11) in dedicated `reloader` namespace
- Watches all namespaces; opt-in via `reloader.stakater.com/auto: "true"` annotation
- Updates roadmap: Volsync, Goldilocks, Trivy, and Reloader all marked done

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Verify Flux reconciles after merge**

After the PR merges, check reconciliation:

```bash
flux get kustomization reloader -n flux-system
flux get helmrelease reloader -n reloader
kubectl get pods -n reloader
```

Expected: kustomization `Ready=True`, HelmRelease `Ready=True`, one `reloader-*` pod Running.
