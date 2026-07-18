# VictoriaMetrics Cold Tier (Off-Longhorn Long-Term Metrics) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a **second** single-node VictoriaMetrics (`victoria-metrics-lt`, ~395d/13-month retention) whose PVC lives on a **static `local` PV backed by a pool_0 VMDK** (TrueNAS spinning disk, off Longhorn). Prometheus fans out `remote_write` to both the existing hot tier (30d, SSD) and this cold tier, so all metrics older than 30 days live *only* on cheap bulk disk. Purely additive — the hot path is untouched.

**Architecture:** `Prometheus (24h local) → remoteWrite fan-out → { victoria-metrics (hot, 30d, longhorn-r2) , victoria-metrics-lt (cold, 395d, local PV on pool_0) }`. Grafana gets a 3rd datasource (`uid: victoriametrics-lt`) for history panels; hot dashboards keep the default. Backup = TrueNAS ZFS snapshots of the pool_0 dataset (NOT Velero).

**Tech Stack:** Flux CD, Helm (`victoria-metrics-single` chart v0.39.0 — same chart & HelmRepository as the hot tier), kube-prometheus-stack, Grafana, VMware/ESXi, TrueNAS (pool_0).

**Spec:** `docs/superpowers/specs/victoriametrics-cold-tier-design.md`

**Repo conventions:** GitOps repo, **no unit-test runner**. "Validation" = manifest renders (`kubectl kustomize <dir>`) + passes `.claude/rules/flux.md` / `kyverno.md` / `networkpolicy.md`; correctness confirmed async via Flux reconciliation after merge. Never `kubectl apply` under `clusters/`. Never push to `main` — PR only. One branch: `feat/victoria-metrics-cold-tier`.

**Wiring note (simpler than the hot tier was):** reuses the existing `victoria-metrics-repo` HelmRepository (no new source) and the `monitoring` Flux Kustomization already globs app dirs — so **neither `flux-system` index file changes**. Only `monitoring/kustomization.yaml` gets one new line.

---

## Phase 0 — Manual VMware/TrueNAS prerequisite (out-of-band, NOT Flux)

> Done by hand in TrueNAS + vCenter + on the node, **before** any Flux manifests. **No new VM** — you add a VMDK to the existing worker VM.

**Transport: NFS (pool_0 dataset → NFS datastore).** Chosen over iSCSI because the decided backup is ZFS snapshots — with NFS the VMDK is a plain file *inside* the snapshot (trivially browsable/restorable), vs. iSCSI+VMFS burying it under an opaque block device. Also simpler (no target/extent/IQN), native thin, easy grow. iSCSI's only edge is sync-write latency, which is irrelevant to a non-latency-critical cold tier. Full rationale in the spec's decisions table.

- [ ] **Step 0.1: Decide the node.** Default **k8sworker01** (only non-DMZ, non-CP worker with no stability history; w03/w04's only strike was *Longhorn* EIO, irrelevant to a local PV). Final constraint = which ESXi host can reach pool_0 + host the VMDK. Whatever you pick, use it consistently in the PV `nodeAffinity` (Task 1) and record it here: `NODE = ______`.

- [ ] **Step 0.2: TrueNAS — create a dataset on pool_0.** Create a **dataset** (e.g. `pool_0/vm-lt`) from free space — does not touch the media datasets. Record the dataset path.

- [ ] **Step 0.3: TrueNAS — share the dataset via NFS.** Add an NFS share on the dataset, authorized to the ESXi host that runs `NODE` (maproot as appropriate for the ESXi host). Record the NFS export path + TrueNAS IP.

- [ ] **Step 0.4: TrueNAS — snapshot task (this is the cold tier's ONLY backup).** Add a periodic snapshot task on the dataset: **weekly, retain 4–8**. The VMDK file sits inside the snapshot; restore = clone the dataset or copy the `.vmdk` back.

- [ ] **Step 0.5: ESXi — mount the NFS datastore.** On the host running `NODE`: Storage → New Datastore → **NFS** → point at the TrueNAS IP + export path from 0.3 → name it (e.g. `ds-vm-lt`).

- [ ] **Step 0.6: ESXi — add a VMDK to the existing worker VM.** Edit the `NODE` VM → Add Hard Disk → **750 GB** on `ds-vm-lt` → attach to a new SCSI controller position (note the controller/unit). Thin provisioning is fine — the VMDK grows into the dataset's shared pool free space. No reboot needed; the guest hot-adds it.

- [ ] **Step 0.7: On the node — format + mount ext4.** SSH to `NODE`:
  ```bash
  lsblk                                   # identify the new empty disk, e.g. /dev/sdX
  sudo mkfs.ext4 -L vm-lt /dev/sdX
  sudo mkdir -p /mnt/vm-lt
  # add to /etc/fstab by UUID so it survives reboot:
  UUID=$(sudo blkid -s UUID -o value /dev/sdX)
  echo "UUID=$UUID /mnt/vm-lt ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
  sudo mount /mnt/vm-lt && df -h /mnt/vm-lt
  ```
  Record the final **mount path** (`/mnt/vm-lt`) — it feeds the PV in Task 1.

> **Multipath note:** because the transport is NFS at the ESXi layer (not guest-level iSCSI), there is **no** `multipathd`/Longhorn entanglement to worry about — the guest sees a plain VMDK block device, never an iSCSI LUN. This is a second reason NFS is the cleaner choice here.

---

## Phase 1 — Flux manifests (one PR)

### Task 1: Static local StorageClass + PersistentVolume

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/victoria-metrics-lt/app/storageclass.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/victoria-metrics-lt/app/pv.yaml`

- [ ] **Step 1: No-provisioner local StorageClass**
  ```yaml
  apiVersion: storage.k8s.io/v1
  kind: StorageClass
  metadata:
    name: local-vm-lt
    labels: { app: victoria-metrics-lt, env: production, category: observability }
  provisioner: kubernetes.io/no-provisioner
  volumeBindingMode: WaitForFirstConsumer
  ```

- [ ] **Step 2: Static `local` PV bound to the VMDK mount** — set `local.path` and `nodeAffinity` values to the `NODE` + mount path from Phase 0.
  ```yaml
  apiVersion: v1
  kind: PersistentVolume
  metadata:
    name: victoria-metrics-lt-data
    labels: { app: victoria-metrics-lt, env: production, category: observability }
  spec:
    capacity: { storage: 750Gi }
    volumeMode: Filesystem
    accessModes: [ReadWriteOnce]
    persistentVolumeReclaimPolicy: Retain
    storageClassName: local-vm-lt
    local:
      path: /mnt/vm-lt
    nodeAffinity:
      required:
        nodeSelectorTerms:
          - matchExpressions:
              - key: kubernetes.io/hostname
                operator: In
                values: [k8sworker01]     # <-- Phase 0 NODE
  ```
  > **Kyverno:** PV `local` type ≠ pod `hostPath`; the "no hostPath" policy targets Pod specs, so this passes. Confirmed by CI `kyverno-cli test`.

- [ ] **Step 3: Validate render** — `kubectl kustomize` after Task 4 wires the dir (these two files render alone via `kubectl apply --dry-run=client -f`).

### Task 2: `victoria-metrics-lt` HelmRelease + values

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/victoria-metrics-lt/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/victoria-metrics-lt/app/configmap.yaml`

- [ ] **Step 1: HelmRelease** (mirror the hot tier's `victoria-metrics/app/helmrelease.yaml`, changing only names/labels; **same chart, same `victoria-metrics-repo` source**)
  ```yaml
  apiVersion: helm.toolkit.fluxcd.io/v2
  kind: HelmRelease
  metadata:
    name: victoria-metrics-lt
    namespace: monitoring
    labels: { app: victoria-metrics-lt, env: production, category: observability }
  spec:
    interval: 5m
    releaseName: victoria-metrics-lt
    chart:
      spec:
        chart: victoria-metrics-single
        version: 0.39.0
        sourceRef: { kind: HelmRepository, name: victoria-metrics-repo, namespace: flux-system }
    valuesFrom:
      - kind: ConfigMap
        name: victoria-metrics-lt-values
        valuesKey: values.yaml
  ```

- [ ] **Step 2: Values ConfigMap** — note `retentionPeriod: 395d`, the distinct `fullnameOverride`, and `storageClassName: local-vm-lt`.
  ```yaml
  apiVersion: v1
  kind: ConfigMap
  metadata:
    name: victoria-metrics-lt-values
    namespace: monitoring
    labels: { app: victoria-metrics-lt, env: production, category: observability }
  data:
    values.yaml: |
      fullnameOverride: victoria-metrics-lt        # -> victoria-metrics-lt-server.monitoring.svc:8428
      server:
        retentionPeriod: 395d
        mode: statefulSet
        persistentVolume:
          enabled: true
          storageClassName: local-vm-lt
          accessModes: [ReadWriteOnce]
          size: 750Gi
        podLabels: { app: victoria-metrics-lt, env: production, category: observability }
        resources:
          requests: { cpu: 100m, memory: 512Mi }
          limits:   { cpu: "1",  memory: 2Gi }     # year-range queries are RAM-heavier; tune after observing
        serviceMonitor:
          enabled: true
          extraLabels: { app: victoria-metrics-lt, env: production, category: observability }
  ```
  > **⚠ Label discipline (load-bearing):** the app label MUST be `victoria-metrics-lt`, NOT `victoria-metrics`. The existing `victoria-metrics-b2` Velero schedule selects `app: victoria-metrics`; a matching label would sweep this 750Gi volume into a daily B2 backup — the opposite of the design intent. Grep the whole dir for `app: victoria-metrics$` before committing.

### Task 3: App kustomization + wire into the monitoring namespace

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/victoria-metrics-lt/app/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/monitoring/kustomization.yaml`

- [ ] **Step 1: App kustomization** — list all four resources:
  ```yaml
  apiVersion: kustomize.config.k8s.io/v1beta1
  kind: Kustomization
  resources:
    - storageclass.yaml
    - pv.yaml
    - configmap.yaml
    - helmrelease.yaml
  ```

- [ ] **Step 2: Add to the namespace kustomization** — insert `- victoria-metrics-lt/app` into `monitoring/kustomization.yaml` `resources:` (keep alphabetical, right after `victoria-metrics/app`).

- [ ] **Step 3: Validate render** — `kubectl kustomize clusters/vollminlab-cluster/monitoring/victoria-metrics-lt/app` renders cleanly; then `kubectl kustomize clusters/vollminlab-cluster/monitoring | grep victoria-metrics-lt` confirms it's aggregated.

### Task 4: Prometheus remoteWrite fan-out + Grafana datasource

**Files:**
- Modify: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`

- [ ] **Step 1: Add the second `remoteWrite` endpoint** (append to the existing list — do NOT touch the first entry):
  ```yaml
        remoteWrite:
          - url: http://victoria-metrics-single-server.monitoring.svc.cluster.local:8428/api/v1/write
          - url: http://victoria-metrics-lt-server.monitoring.svc.cluster.local:8428/api/v1/write
            name: vm-lt
            queueConfig:
              capacity: 10000
              maxShards: 10
              maxSamplesPerSend: 2000
  ```
  Per-endpoint queue isolation → a slow pool_0 write cannot stall the hot write or scraping.

- [ ] **Step 2: Add the 3rd Grafana datasource** — locate the `additionalDataSources` block (where `prometheus-live` is defined in this same configmap) and add:
  ```yaml
        - name: VictoriaMetrics (long-term)
          type: prometheus
          uid: victoriametrics-lt
          access: proxy
          url: http://victoria-metrics-lt-server.monitoring.svc.cluster.local:8428
          isDefault: false
  ```
  Leave the default (`uid: prometheus` → hot VM) and `prometheus-live` unchanged.

- [ ] **Step 3: Validate render** — `kubectl kustomize clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app` renders; both remoteWrite URLs present, three datasources total.

### Task 5: NetworkPolicy check (verify, likely no change)

- [ ] **Step 1:** The cold VM is in the **same `monitoring` namespace** and listens on the **same container port 8428** as the working hot tier. Prometheus→VM and Grafana→VM already work, so existing monitoring NetworkPolicies should cover it. **Verify, don't assume** — render the monitoring `networkpolicies/` dir and confirm no podSelector pins to `app: victoria-metrics` in a way that would exclude `victoria-metrics-lt`. If a policy is app-scoped rather than namespace/port-scoped, add a matching allow. See `.claude/rules/networkpolicy.md`.

### Task 6: Push branch, open PR

- [ ] **Step 1:** `/new-branch feat/victoria-metrics-cold-tier`, stage the new/modified files **explicitly by name** (never `git add -A`), commit, push, open PR. No test-plan section (async verification). One concern per PR.
- [ ] **Step 2:** Wait for CI green (kyverno-cli test, gitleaks, yaml lint). Do NOT merge without explicit user approval.

---

## Phase 2 — Post-merge verification (after PR merges + Flux reconciles)

- [ ] **V1:** `flux get hr victoria-metrics-lt -n monitoring` → Ready=True; pod `victoria-metrics-lt-server-0` Running on the Phase 0 `NODE`.
- [ ] **V2:** `kubectl get pv victoria-metrics-lt-data` → Bound; `kubectl get pvc -n monitoring | grep lt` → templated PVC Bound to it (WaitForFirstConsumer resolved on schedule).
- [ ] **V3:** Cold ingest rising — from the grafana pod: `curl -s victoria-metrics-lt-server:8428/api/v1/query?query=vm_rows_inserted_total` climbing; `.../query?query=count(up)` returns data.
- [ ] **V4:** Prometheus fan-out healthy — `prometheus_remote_storage_samples_failed_total{url=~".*lt.*"}` flat at 0; `prometheus_remote_storage_shards{url=~".*lt.*"}` > 0. Hot endpoint unchanged.
- [ ] **V5:** Grafana — the `victoriametrics-lt` datasource passes "Save & test"; a panel pointed at it renders a >30d range.
- [ ] **V6:** Backup exclusion confirmed — `kubectl get schedules.velero.io -n velero` shows no new LT schedule and the LT PVC is NOT in any backup (label mismatch + `monitoring` excluded). TrueNAS snapshot task from Phase 0.4 has taken at least one snapshot.
- [ ] **V7:** Disk growth sane — after ~48h, `vm_data_size_bytes` on the LT instance tracking ~1.6 GB/day against the 750Gi PV (≈13 months of headroom).

## Rollback

Purely additive → revert = remove the LT remoteWrite entry + LT datasource, delete the `victoria-metrics-lt` HelmRelease/PVC (PV is `Retain`, so data survives). Hot tier returns to exact current behavior, zero data loss. The pool_0 zvol/dataset + VMDK can be reclaimed manually afterward.

## Out of scope (v2)

OSS `vmagent` stream-aggregation downsampling on the LT stream · `vmauth` single endpoint · VictoriaLogs for Loki.
