# VictoriaMetrics Long-Term Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-cluster single-node VictoriaMetrics as a long-term (90d), compressed, PromQL-native store that Prometheus `remote_write`s to, so Prometheus local retention can drop to 24h — restoring long-term metrics history while *reducing* the metrics stack's memory footprint.

**Architecture:** Prometheus stays the sole scraper/alerter (24h local) and `remote_write`s every sample to VictoriaMetrics single (90d, 30Gi/2-replica Longhorn). Grafana's `prometheus` datasource UID is re-pointed at VM so all existing dashboards read full history with zero edits; the real Prometheus is kept as a secondary "Prometheus (live)" datasource. A dedicated Velero `Schedule` archives the VM PVC to the existing B2 BSL daily.

**Tech Stack:** Flux CD, Helm (`victoria-metrics-single` chart v0.39.0), kube-prometheus-stack, Longhorn, Velero, Grafana.

**Spec:** `docs/superpowers/specs/victoriametrics-longterm-design.md`

**Repo conventions:** This is a GitOps repo with **no unit-test runner**. "Validation" for each task means the manifest renders (`kubectl kustomize <dir>`) and follows `.claude/rules/flux.md` + `kyverno.md`; correctness is confirmed async via Flux reconciliation after merge. Never `kubectl apply` under `clusters/`. Never push to `main` — PR only.

**Two PRs:**
- **PR #1 (this branch `feat/victoriametrics-longterm`):** Tasks 1–7 — deploy VM, remote_write, datasource swap, Velero backup. Prometheus retention stays 7d.
- **PR #2 (separate branch, after PR #1 verified):** Task 8 — drop Prometheus retention to 24h.

---

### Task 1: VictoriaMetrics HelmRepository source

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/victoria-metrics-helmrepository.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`

- [ ] **Step 1: Verify the chart version exists before pinning it**

Run:
```bash
helm repo add vm https://victoriametrics.github.io/helm-charts/ 2>/dev/null; helm repo update vm >/dev/null
helm search repo vm/victoria-metrics-single --versions | head -5
```
Expected: a row for `vm/victoria-metrics-single` with chart version `0.39.0` (or newer). If `0.39.0` is gone, pick the latest listed stable version and use it everywhere `0.39.0` appears in this plan.

- [ ] **Step 2: Create the HelmRepository**

`clusters/vollminlab-cluster/flux-system/repositories/victoria-metrics-helmrepository.yaml`:
```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: victoria-metrics-repo
  namespace: flux-system
  labels:
    app: victoria-metrics
    env: production
    category: observability
spec:
  interval: 5m
  url: https://victoriametrics.github.io/helm-charts/
  timeout: 3m
```

- [ ] **Step 3: Register it in the repositories index (keep alphabetical)**

In `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`, add the line between `velero-helmrepository.yaml` and `vmware-exporter-helmrepository.yaml`:
```yaml
  - velero-helmrepository.yaml
  - victoria-metrics-helmrepository.yaml
  - vmware-exporter-helmrepository.yaml
```

- [ ] **Step 4: Validate render**

Run: `kubectl kustomize clusters/vollminlab-cluster/flux-system/repositories/ >/dev/null && echo OK`
Expected: `OK` (no error), and the rendered output contains `victoria-metrics-repo`.

- [ ] **Step 5: Commit**

```bash
git add clusters/vollminlab-cluster/flux-system/repositories/victoria-metrics-helmrepository.yaml \
        clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml
git commit -m "feat(victoriametrics): add victoria-metrics HelmRepository source"
```

---

### Task 2: `longhorn-r2` StorageClass (2-replica, general workers)

The default `longhorn` SC is 3-replica; `longhorn-dmz` is 2-replica but pinned to DMZ nodes. VM needs a 2-replica SC that spreads across general workers.

**Files:**
- Create: `clusters/vollminlab-cluster/clusterwide/storageclass-longhorn-r2.yaml`
- Modify: `clusters/vollminlab-cluster/clusterwide/kustomization.yaml`

- [ ] **Step 1: Create the StorageClass** (mirrors `storageclass-longhorn-dmz.yaml` minus the `nodeSelector`)

`clusters/vollminlab-cluster/clusterwide/storageclass-longhorn-r2.yaml`:
```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: longhorn-r2
  labels:
    app: storageclass-longhorn-r2
    env: production
    category: storage
provisioner: driver.longhorn.io
reclaimPolicy: Delete
allowVolumeExpansion: true
volumeBindingMode: WaitForFirstConsumer
parameters:
  numberOfReplicas: "2"
  dataLocality: "best-effort"
  staleReplicaTimeout: "30"
  fromBackup: ""
  fsType: "ext4"
```

- [ ] **Step 2: Register it in the clusterwide index**

In `clusters/vollminlab-cluster/clusterwide/kustomization.yaml`, add after `storageclass-longhorn-dmz.yaml`:
```yaml
  - storageclass-longhorn-dmz.yaml
  - storageclass-longhorn-r2.yaml
```

- [ ] **Step 3: Validate render**

Run: `kubectl kustomize clusters/vollminlab-cluster/clusterwide/ >/dev/null && echo OK`
Expected: `OK`, rendered output contains `longhorn-r2` with `numberOfReplicas: "2"`.

- [ ] **Step 4: Commit**

```bash
git add clusters/vollminlab-cluster/clusterwide/storageclass-longhorn-r2.yaml \
        clusters/vollminlab-cluster/clusterwide/kustomization.yaml
git commit -m "feat(storage): add longhorn-r2 2-replica StorageClass for general workers"
```

---

### Task 3: VictoriaMetrics app (HelmRelease + values)

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/victoria-metrics/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/victoria-metrics/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/victoria-metrics/app/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/monitoring/kustomization.yaml`

No SealedSecret/ExternalSecret is needed — backups go through Velero (Task 5), reusing existing credentials.

- [ ] **Step 1: Create the values ConfigMap**

`clusters/vollminlab-cluster/monitoring/victoria-metrics/app/configmap.yaml`:
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: victoria-metrics-values
  namespace: monitoring
  labels:
    app: victoria-metrics
    env: production
    category: observability
data:
  values.yaml: |
    # Stable service name -> victoria-metrics-single-server.monitoring.svc:8428
    fullnameOverride: victoria-metrics-single

    server:
      retentionPeriod: 90d

      # StatefulSet (not Deployment) so the RWO Longhorn volume is handled via a
      # volumeClaimTemplate and there is no rolling-update detach trap.
      statefulSet:
        enabled: true

      persistentVolume:
        enabled: true
        storageClassName: longhorn-r2
        accessModes:
          - ReadWriteOnce
        size: 30Gi

      podLabels:
        app: victoria-metrics
        env: production
        category: observability

      resources:
        requests:
          cpu: 100m
          memory: 256Mi
        limits:
          cpu: "1"
          memory: 1Gi
```

- [ ] **Step 2: Create the HelmRelease**

`clusters/vollminlab-cluster/monitoring/victoria-metrics/app/helmrelease.yaml`:
```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: victoria-metrics
  namespace: monitoring
  labels:
    app: victoria-metrics
    env: production
    category: observability
spec:
  interval: 5m
  releaseName: victoria-metrics
  chart:
    spec:
      chart: victoria-metrics-single
      version: 0.39.0
      sourceRef:
        kind: HelmRepository
        name: victoria-metrics-repo
        namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: victoria-metrics-values
      valuesKey: values.yaml
```

- [ ] **Step 3: Create the app kustomization**

`clusters/vollminlab-cluster/monitoring/victoria-metrics/app/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - configmap.yaml
  - helmrelease.yaml
```

- [ ] **Step 4: Wire the app into the monitoring namespace**

In `clusters/vollminlab-cluster/monitoring/kustomization.yaml`, add `- victoria-metrics/app` to `resources` (after `promtail/app`):
```yaml
  - promtail/app
  - victoria-metrics/app
```

- [ ] **Step 5: Validate render**

Run: `kubectl kustomize clusters/vollminlab-cluster/monitoring/ >/dev/null && echo OK`
Expected: `OK`. Then confirm the values ConfigMap and HelmRelease are present:
`kubectl kustomize clusters/vollminlab-cluster/monitoring/ | grep -E 'victoria-metrics-values|kind: HelmRelease' | head`

- [ ] **Step 6: Commit**

```bash
git add clusters/vollminlab-cluster/monitoring/victoria-metrics/ \
        clusters/vollminlab-cluster/monitoring/kustomization.yaml
git commit -m "feat(victoriametrics): deploy single-node VictoriaMetrics (90d, 30Gi/2-replica)"
```

---

### Task 4: Prometheus remote_write + Grafana datasource swap

Edit the kube-prometheus-stack values ConfigMap: add remote_write to VM, and replace the auto-provisioned Prometheus datasource with VM-as-`prometheus` (default) + Prometheus-as-`prometheus-live` (secondary). Loki stays. **Prometheus retention stays `7d` in this PR.**

**Files:**
- Modify: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`

- [ ] **Step 1: Add `remoteWrite` under `prometheus.prometheusSpec`**

In `configmap.yaml`, immediately after the `retention`/`retentionSize` lines inside `prometheusSpec`, add:
```yaml
        remoteWrite:
          - url: http://victoria-metrics-single-server.monitoring.svc.cluster.local:8428/api/v1/write
```
(Indentation: `remoteWrite` aligns with `retention:` — 8 spaces under `prometheusSpec:`.)

- [ ] **Step 2: Replace the Grafana `additionalDataSources` block and disable the default datasource**

Find the existing `grafana:` → `additionalDataSources:` block (currently just Loki) and replace it with the block below, and add the `sidecar.datasources.defaultDatasourceEnabled: false` setting under `grafana:`:
```yaml
      sidecar:
        datasources:
          defaultDatasourceEnabled: false

      additionalDataSources:
        - name: VictoriaMetrics
          type: prometheus
          uid: prometheus
          access: proxy
          url: http://victoria-metrics-single-server.monitoring.svc.cluster.local:8428
          isDefault: true
          jsonData:
            timeInterval: 30s
        - name: Prometheus (live)
          type: prometheus
          uid: prometheus-live
          access: proxy
          url: http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090
          isDefault: false
          jsonData:
            timeInterval: 30s
        - name: Loki
          type: loki
          url: http://loki.monitoring.svc.cluster.local:3100
          access: proxy
```
Notes:
- `uid: prometheus` is the UID every existing dashboard pins, so VM transparently serves them.
- `timeInterval: 30s` matches `scrapeInterval` so rate() windows render correctly.
- If `grafana:` already has a `sidecar:` key, merge `datasources.defaultDatasourceEnabled: false` into it rather than adding a second `sidecar:`.

- [ ] **Step 3: Validate render**

Run: `kubectl kustomize clusters/vollminlab-cluster/monitoring/ >/dev/null && echo OK`
Expected: `OK`. Confirm both datasource UIDs and the remote_write URL appear:
`kubectl kustomize clusters/vollminlab-cluster/monitoring/ | grep -E 'prometheus-live|api/v1/write|defaultDatasourceEnabled'`

- [ ] **Step 4: Commit**

```bash
git add clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml
git commit -m "feat(victoriametrics): remote_write Prometheus to VM and point Grafana at VM"
```

---

### Task 5: Velero backup Schedule for the VM PVC → B2

Add a dedicated `Schedule` scoped to only the VM volume, overriding the blanket `monitoring` exclusion on the existing schedules.

**Files:**
- Modify: `clusters/vollminlab-cluster/velero/velero/app/configmap.yaml`

- [ ] **Step 1: Add the `victoria-metrics-b2` schedule**

In the `schedules:` map (after the `monthly-b2:` block), add:
```yaml
      victoria-metrics-b2:
        disabled: false
        schedule: "0 5 * * *"
        useOwnerReferencesInBackup: false
        template:
          ttl: 2160h
          storageLocation: b2
          defaultVolumesToFsBackup: true
          includedNamespaces:
            - monitoring
          labelSelector:
            matchLabels:
              app: victoria-metrics
```
This captures only pods/PVCs labelled `app: victoria-metrics` (the VM pod via `server.podLabels`), so Prometheus/Loki volumes stay excluded. `ttl: 2160h` ≈ 90 days, matching `daily-b2`.

- [ ] **Step 2: Validate render**

Run: `kubectl kustomize clusters/vollminlab-cluster/velero/velero/app/ >/dev/null && echo OK`
Expected: `OK`. Confirm the schedule is present:
`kubectl kustomize clusters/vollminlab-cluster/velero/velero/app/ | grep -A2 victoria-metrics-b2`

- [ ] **Step 3: Commit**

```bash
git add clusters/vollminlab-cluster/velero/velero/app/configmap.yaml
git commit -m "feat(victoriametrics): add Velero daily B2 backup schedule for VM PVC"
```

---

### Task 6: Push branch and open PR #1

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin feat/victoriametrics-longterm
gh pr create --title "feat(victoriametrics): long-term metrics store + remote_write + datasource swap" \
  --body "Deploys single-node VictoriaMetrics (90d, 30Gi/2-replica longhorn-r2), points Prometheus remote_write at it, re-points Grafana's prometheus datasource UID at VM (Prometheus kept as 'Prometheus (live)'), and adds a Velero B2 backup schedule scoped to the VM PVC. Prometheus retention stays 7d — the 24h cutover is a follow-up PR after VM ingestion is verified. Spec: docs/superpowers/specs/victoriametrics-longterm-design.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 2: Wait for CI**

Run: `gh pr checks --watch`
Expected: kyverno-cli test, Validate Terraform Modules (n/a), and Secret Scanning all pass. Fix any kyverno label/resource-limit failures before requesting merge. **Do not merge** — merging requires explicit user instruction.

---

### Task 7: Post-merge verification (after PR #1 merges)

Run after Flux reconciles (~10m), or `flux reconcile kustomization monitoring --with-source`.

- [ ] **Step 1: VM is healthy and the service name matches the remote_write URL**

```bash
flux get helmrelease victoria-metrics -n monitoring         # Ready=True
kubectl get pods -n monitoring -l app=victoria-metrics      # Running
kubectl get svc -n monitoring | grep victoria-metrics
```
Expected: a service named `victoria-metrics-single-server` on port 8428. **If the service name differs**, update the `remoteWrite` URL and the VictoriaMetrics datasource URL in `kube-prometheus-stack/app/configmap.yaml` to match, in a quick fix PR.

- [ ] **Step 2: VM is ingesting remote_write samples**

```bash
VM=$(kubectl get pod -n monitoring -l app=victoria-metrics -o name | head -1)
kubectl exec -n monitoring ${VM#pod/} -- wget -qO- \
  'http://localhost:8428/api/v1/query?query=vm_rows_inserted_total' | head -c 300
```
Expected: a non-empty JSON result with a growing value (re-run; it should increase).

- [ ] **Step 3: Prometheus remote_write queue is healthy** (Grafana Explore → "Prometheus (live)", or):

```
PromQL: rate(prometheus_remote_storage_samples_failed_total[5m])   # expect 0
PromQL: prometheus_remote_storage_samples_pending                  # expect small/stable
```

- [ ] **Step 4: Dashboards read VM (full history) with no edits**

In Grafana, open an existing dashboard (e.g. MinIO). Confirm: datasource list shows **VictoriaMetrics** (default), **Prometheus (live)**, **Loki**; and panels render for a range **older than 24h** once VM has accumulated that much — proving they hit VM, not Prometheus.

- [ ] **Step 5: Alerting unaffected** (VM is not in the alert path)

```bash
kubectl get prometheusrules -n monitoring | head
# In Alertmanager UI, confirm alerts still flow; no PrometheusRule errors in Prometheus UI → Status → Rules.
```

- [ ] **Step 6: First Velero VM backup succeeds** (after the 5am run, or trigger once):

```bash
kubectl get schedules.velero.io -n velero | grep victoria-metrics-b2
kubectl get backups.velero.io -n velero -l velero.io/schedule-name=victoria-metrics-b2 \
  --sort-by=.metadata.creationTimestamp \
  -o custom-columns='NAME:.metadata.name,PHASE:.status.phase,ERRORS:.status.errors'
```
Expected: latest backup `Completed`, 0 errors. Confirm it captured a volume (not empty) via `velero backup describe <name> --details`.

---

### Task 8: Prometheus retention cutover to 24h (PR #2 — separate branch, after Task 7 passes)

Only start this once Task 7 confirms VM is ingesting and dashboards read from it.

**Files:**
- Modify: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`

- [ ] **Step 1: New branch off fresh main**

```bash
git -C /home/vollmin/repos/vollminlab/k8s-vollminlab-cluster checkout main
git -C /home/vollmin/repos/vollminlab/k8s-vollminlab-cluster pull
git -C /home/vollmin/repos/vollminlab/k8s-vollminlab-cluster checkout -b feat/prometheus-24h-retention
```

- [ ] **Step 2: Drop retention to 24h**

In `prometheus.prometheusSpec`, change:
```yaml
        retention: 7d
        retentionSize: 3GB
```
to:
```yaml
        retention: 24h
```
(Remove `retentionSize` — at 24h the size cap is unnecessary. Leave the 20Gi PVC as-is; Longhorn can't shrink it and it simply won't fill.)

- [ ] **Step 3: Optionally lower the Prometheus memory request/limit**

Now that local TSDB holds only 24h, re-measure live usage first:
```bash
kubectl top pod -n monitoring -l app.kubernetes.io/name=prometheus
```
If steady-state RSS is well under the current `1500Mi` request, lower `prometheus.prometheusSpec.resources.requests.memory` (e.g. to `512Mi`) and `limits.memory` (e.g. `1Gi`) in the same PR. If unsure, leave resources unchanged and tune in a later PR.

- [ ] **Step 4: Validate, commit, PR**

```bash
kubectl kustomize clusters/vollminlab-cluster/monitoring/ >/dev/null && echo OK
git add clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml
git commit -m "feat(prometheus): drop local retention to 24h now that VM holds history"
git push -u origin feat/prometheus-24h-retention
gh pr create --title "feat(prometheus): cut local retention to 24h (VM holds long-term)" \
  --body "Follow-up to the VictoriaMetrics PR. VM ingestion + dashboard reads verified, so Prometheus local retention drops to 24h (enough for alerting/rule eval), reclaiming worker memory. Long-term history now lives in VM.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 5: Post-merge — confirm the memory win**

```bash
kubectl top pod -n monitoring -l app.kubernetes.io/name=prometheus
```
Expected: Prometheus RSS drops materially versus the pre-cutover baseline; dashboards still render full history (now served entirely by VM beyond 24h).

---

## Self-review notes

- **Spec coverage:** chart shape (Task 3), remote_write topology (Task 4), datasource swap (Task 4), PVC/replicas (Tasks 2–3), Velero backup (Task 5), two-stage cutover (Tasks 6–8) — all covered.
- **No new secrets:** the Velero backup decision removed the B2-bucket/1Password/ExternalSecret prerequisite entirely.
- **Build-time check retained:** the exact VM service name is verified post-deploy in Task 7 Step 1 with a concrete fix path, because it can't be confirmed before the chart renders in-cluster.
