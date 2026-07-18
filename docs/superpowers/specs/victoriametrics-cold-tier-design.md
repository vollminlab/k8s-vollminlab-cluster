# VictoriaMetrics Cold Tier (Long-Term, Off-Longhorn Metrics) — Design

**Created:** 2026-07-07 (architecture decided 2026-06-21, open questions resolved 2026-07-07)
**Status:** Finalized — ready for implementation plan.
**Depends on:** The base VictoriaMetrics rollout (the *hot* tier) is complete and live —
see `victoriametrics-longterm-design.md` (PRs #831/#834/#835/#836/#837). This spec is purely
**additive** to that stack; the hot path is untouched throughout.

## Problem / Goal

The hot VictoriaMetrics tier keeps **30 days** of metrics on `longhorn-r2` (replicated SSD).
The goal is **a full year of queryable metrics where anything older than ~30 days lives on
storage OUTSIDE Longhorn** — on TrueNAS **pool_0** (the bulk spinning-disk pool that already
backs the media library and SMB shares, ~22 TiB free). Recent data stays fast on SSD; cold
history moves to cheap bulk disk. No new hardware, no shared-datastore SSD pressure.

## Why not VictoriaMetrics cluster mode

VM **cluster mode does NOT tier by age.** `vminsert` shards series across `vmstorage` nodes by
consistent hash; each node holds the full `-retentionPeriod` for *its shard* of series. Putting a
`vmstorage` on pool_0 would answer *half of all series* (hot **and** cold) from spinning disk.
Age-based tiering / downsampling (`-retentionFilter`) is **VictoriaMetrics Enterprise-only** (same
licensing trap that ruled out `vmbackupmanager`). The OSS-correct realization of "old data on
spinning disk" is a **second single-node instance**, not cluster mode. At ~1.6 GB/day this cluster
is trivially single-node scale anyway.

## Architecture — two-instance fan-out

```
                          ┌─ remoteWrite #1 ─▶  victoria-metrics (HOT)
                          │                     30d · longhorn-r2 SSD · fast · Grafana default
 Prometheus (24h local) ──┤
                          │                     victoria-metrics-lt (COLD)
                          └─ remoteWrite #2 ─▶  ~395d (13mo) · local PV on pool_0 VMDK · spinning
                                                disk · Grafana 3rd datasource for history panels
```

- **Hot tier (existing, unchanged):** `victoria-metrics` single, 30d, `longhorn-r2`, stays the
  Grafana default datasource (`uid: prometheus`). Everything ≤30d served from here.
- **Cold tier (new):** a **second** `victoria-metrics-single` release, `victoria-metrics-lt`,
  retention **~395d**, PVC on a **static local PV backed by a pool_0 VMDK**. Because it holds the
  full 13 months, all data older than 30d exists **only here → off Longhorn** ✓.
- **Ingest:** Prometheus `remoteWrite` fans out to *both* VM services. Each `remoteWrite` endpoint
  has its own in-memory queue, so a slow pool_0 write cannot stall the hot write.
- **Query:** a 3rd Grafana datasource `VictoriaMetrics (long-term)` (`uid: victoriametrics-lt`);
  pick it explicitly for historical / year-range panels. Hot dashboards keep using the default.

## Decisions (all resolved — do not re-litigate)

| Topic | Decision | Rationale |
|-------|----------|-----------|
| Instance model | Second single-node release, **not** cluster mode | Cluster mode can't tier by age; Enterprise-only downsampling |
| Storage access path | **VMDK on a pool_0-backed VMware datastore → static `local` PV** (ext4) | Guest sees a plain block VMDK + ext4 (true block semantics for the tsdb); the datastore transport is invisible to the guest |
| **Datastore transport: NFS** (dataset → NFS datastore), **not iSCSI** | Create a pool_0 **dataset**, share via NFS, mount as an NFS datastore; the 750Gi VMDK is a file on it | The decided backup = **ZFS snapshots**, and NFS makes the VMDK a plain file *inside* the snapshot → trivially browsable/restorable; iSCSI+VMFS would bury it under an opaque block device. Also: simpler provisioning (no target/extent/IQN), native thin + shared pool free space, easy grow. iSCSI's only edge (sync-write latency) is irrelevant to a non-latency-critical cold tier — the etcd-parity argument imports the wrong precedent (etcd is fsync-hot-path; this is the opposite) |
| **SMB/CIFS rejected** | Do **not** point the tsdb at an SMB share | CIFS mmap/locking/fsync semantics fight a database — worst possible tsdb backing. NOTE: this does **not** apply to the NFS *datastore* above — the guest never speaks NFS, it sees a block VMDK; NFS is only the ESXi↔TrueNAS transport |
| Node placement | **k8sworker01** is the documented default; finalize at the VMware step | Pin follows the local PV's `nodeAffinity`. w02 has NotReady-flap history; w03/w04's only strike was *Longhorn* EIO stale-mounts, which are irrelevant to a **local** PV. Real constraint = which ESXi host has pool_0 datastore access + VMDK room |
| Backup | **TrueNAS ZFS snapshots of the pool_0 dataset**; **excluded from Velero** | 450Gi of "nice-to-have" history; snapshotting where it already lives is free and avoids B2 bloat |
| Label discipline | Cold instance uses **`app: victoria-metrics-lt`** (NOT `victoria-metrics`) | The existing `victoria-metrics-b2` Velero schedule selects `app: victoria-metrics`; a matching label would sweep the 450Gi volume into a daily B2 backup — exactly what we're avoiding |
| Downsampling | **Deferred to a v2** (OSS `vmagent` stream-aggregation on the LT stream only) | Full-resolution 13mo fits pool_0 comfortably; add later if year-range queries feel slow |

## Sizing

- Measured hot-tier growth: **~1.63 GB/day on-disk** (505k active series, ~16.7k samples/sec).
- 395 days × 1.63 GB/day ≈ **~645 GB raw**. Earlier design note said ~343 GB at 0.87 GB/day —
  that figure predated the 1.63 GB/day capacity baseline; **use the higher number.**
- **Provision a 750 Gi VMDK / PV** (645 GB + headroom). pool_0 has ~22 TiB free, so this is safe.
  Revisit if a later v2 stream-aggregation downsample lands (would cut this severalfold).

> Note: this supersedes the "~450Gi" figure in the original brainstorm — that assumed the older,
> lower bytes/day estimate. Size to the measured 1.63 GB/day.

## Manual prerequisite (out-of-band — NOT Flux-managed)

Do this **before** the Flux manifests land. **No new VM** — a VMDK is added to the *existing*
worker VM. Transport is **NFS** (see the decisions table):

1. **TrueNAS:** on **pool_0**, create a **dataset** (e.g. `pool_0/vm-lt`) to back the VMware
   datastore — carved from free space, media untouched. Enable a **snapshot task** on this dataset
   (recommended: weekly, retain 4–8) — this is the cold tier's only backup. Share it via **NFS**,
   authorized to the ESXi host that runs the chosen worker.
2. **vCenter:** mount the dataset as an **NFS datastore** on that ESXi host, then create a
   **750 Gi VMDK** on it attached to the existing worker VM (default **k8sworker01**). Thin
   provisioning is fine — the VMDK grows into the dataset's shared pool free space.
3. **On the worker node:** the guest hot-adds the VMDK as a plain block device; partition/format it
   **ext4** and mount at a stable path, e.g. `/mnt/vm-lt` (add to `/etc/fstab` by `UUID=` so it
   survives reboot).
4. Record the final **node name** and **mount path** — they feed the `local` PV manifest below.

## Kubernetes / Flux changes

All new files live under `clusters/vollminlab-cluster/monitoring/victoria-metrics-lt/app/`. The
`monitoring` Flux Kustomization already reconciles `./clusters/vollminlab-cluster/monitoring`, and
`monitoring/kustomization.yaml` lists the app dirs — so **neither** `flux-system` index file needs
changing, and **no new HelmRepository** is needed (reuse `victoria-metrics-repo`).

### Files to create

```
clusters/vollminlab-cluster/monitoring/victoria-metrics-lt/app/
  storageclass.yaml     # no-provisioner local SC (WaitForFirstConsumer)   [only if not reused]
  pv.yaml               # static `local` PV → the pool_0 VMDK mount, nodeAffinity to the worker
  helmrelease.yaml      # HelmRelease, chart victoria-metrics-single 0.39.0, name victoria-metrics-lt
  configmap.yaml        # values: fullnameOverride victoria-metrics-lt, 395d, binds the static PV
  kustomization.yaml    # lists the above
```

### One edit to an existing file

- `clusters/vollminlab-cluster/monitoring/kustomization.yaml` — add `- victoria-metrics-lt/app`
  to `resources:` (keep alphabetical).

### StorageClass — static local, no provisioner

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: local-vm-lt
  labels: { app: victoria-metrics-lt, env: production, category: observability }
provisioner: kubernetes.io/no-provisioner
volumeBindingMode: WaitForFirstConsumer   # bind only when the pod schedules on the pinned node
```

### PersistentVolume — `local` type bound to the VMDK mount

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
  persistentVolumeReclaimPolicy: Retain      # never auto-wipe a year of history
  storageClassName: local-vm-lt
  local:
    path: /mnt/vm-lt                          # <-- the ext4 mount from prereq step 3
  nodeAffinity:                               # <-- pins the pod; set to the FINAL chosen node
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values: [k8sworker01]           # default; change if VMware step picks another host
```

> **Kyverno note:** the PV `local` volume type is **not** the same as a pod-level `hostPath`
> volume — the "no hostPath" enforce policy targets Pod specs, not PersistentVolumes. The pod only
> mounts a PVC, so this passes policy. Confirm with `kyverno-cli test` in CI as usual.

### HelmRelease

Identical shape to the hot tier's `helmrelease.yaml`, changing only `metadata.name`,
`releaseName`, labels, and the values ConfigMap name to `victoria-metrics-lt` /
`victoria-metrics-lt-values`. Same `chart: victoria-metrics-single`, `version: 0.39.0`,
`sourceRef: victoria-metrics-repo`.

### Values ConfigMap (key differences from the hot tier)

```yaml
data:
  values.yaml: |
    fullnameOverride: victoria-metrics-lt   # -> service victoria-metrics-lt-server.monitoring.svc:8428
    server:
      retentionPeriod: 395d                 # 13 months
      mode: statefulSet                     # RWO local PV via volumeClaimTemplate, no detach trap
      persistentVolume:
        enabled: true
        storageClassName: local-vm-lt       # binds the static local PV above
        accessModes: [ReadWriteOnce]
        size: 750Gi
      podLabels:   { app: victoria-metrics-lt, env: production, category: observability }
      resources:
        requests: { cpu: 100m, memory: 512Mi }
        limits:   { cpu: "1",  memory: 2Gi }   # year-range queries are RAM-heavier than the hot tier; tune after observing
      serviceMonitor:
        enabled: true
        extraLabels: { app: victoria-metrics-lt, env: production, category: observability }
```

> The StatefulSet's `volumeClaimTemplate` will emit a PVC (`storageClassName: local-vm-lt`,
> 750Gi, RWO) that binds the pre-created static PV when the pod first schedules on the pinned node.

### Prometheus remoteWrite fan-out (one edit)

In `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`, add a
**second** entry to the existing `remoteWrite:` list (do not touch the first):

```yaml
        remoteWrite:
          - url: http://victoria-metrics-single-server.monitoring.svc.cluster.local:8428/api/v1/write
          - url: http://victoria-metrics-lt-server.monitoring.svc.cluster.local:8428/api/v1/write
            name: vm-lt
            queueConfig:                     # isolate the slow spinning-disk endpoint
              capacity: 10000
              maxShards: 10
              maxSamplesPerSend: 2000
```

Per-endpoint queues mean a slow pool_0 write backs up its own shard queue without stalling the hot
write or Prometheus scraping.

### Grafana datasource (one edit)

Add a 3rd datasource alongside the existing VM/Prometheus entries (in the kube-prometheus-stack
Grafana `additionalDataSources`, wherever `prometheus-live` is defined — **confirm the exact file
at implementation time**):

```yaml
- name: VictoriaMetrics (long-term)
  type: prometheus
  uid: victoriametrics-lt
  access: proxy
  url: http://victoria-metrics-lt-server.monitoring.svc.cluster.local:8428
  isDefault: false
```

## Backup

- **No Velero change.** The cold volume is excluded automatically: `monitoring` is already excluded
  from all three main schedules, and the `victoria-metrics-b2` schedule's `labelSelector`
  (`app: victoria-metrics`) does **not** match `app: victoria-metrics-lt`. This is why the distinct
  label is load-bearing — verify it's `victoria-metrics-lt` everywhere before merge.
- **Protection = TrueNAS ZFS snapshots** on the pool_0 dataset backing the datastore (configured in
  prereq step 1). Weekly, retain 4–8. History is "nice to have"; a snapshot restore is the recovery
  path, not Velero.

## Verification (post-merge)

1. `flux get hr victoria-metrics-lt -n monitoring` → Ready=True; pod
   `victoria-metrics-lt-server-0` Running on the pinned node.
2. `kubectl get pv victoria-metrics-lt-data` → Bound; `kubectl get pvc -n monitoring` shows the
   templated PVC Bound to it.
3. Prometheus fan-out healthy: on the Prometheus pod, both remoteWrite endpoints show
   `prometheus_remote_storage_samples_failed_total` flat at 0 and
   `prometheus_remote_storage_shards` > 0 for `url=...lt...`.
4. Cold ingest confirmed:
   `curl victoria-metrics-lt-server:8428/api/v1/query?query=vm_rows_inserted_total` rising;
   `.../api/v1/query?query=count(up)` returns data.
5. Grafana: the `victoriametrics-lt` datasource passes "Save & test"; a panel pointed at it renders.
6. NetworkPolicy: confirm Prometheus→`:8428` and Grafana→`:8428` reach the LT pod (same namespace
   + same container port as the working hot tier, so existing monitoring policies should cover it —
   verify, don't assume; see `.claude/rules/networkpolicy.md`).
7. Kyverno/CI: `kyverno-cli test` green (labels present, no hostPath/`:latest`, resources set).

## Risks & rollback

- **Single point of failure:** cold tier is one pod, one node, one local disk, no HA. Acceptable —
  it's non-critical history, snapshot-protected. Hot tier + alerting are unaffected by its loss.
- **Slow disk backpressure:** mitigated by the isolated remoteWrite queue. If pool_0 ever can't
  keep up, the LT queue drops samples for the LT stream only; hot tier and scraping are unharmed.
- **Rollback:** because it's purely additive, revert = remove the LT remoteWrite entry + the LT
  datasource + delete the `victoria-metrics-lt` HelmRelease/PVC/PV. Hot tier returns to today's
  exact behavior with zero data loss.

## Out of scope (candidate v2)

- OSS `vmagent` **stream-aggregation downsampling** on the LT stream (e.g. 1-minute) to shrink the
  volume severalfold and speed year-range queries.
- Fronting hot + cold with a single `vmauth` endpoint so Grafana has one URL.
- VictoriaLogs (the same pattern for Loki log retention) — its own future spec.

## Implementation sequencing

1. **Manual VMware/TrueNAS prereq** (datastore + 750Gi VMDK + ext4 mount + ZFS snapshot task);
   record final node + mount path.
2. **One PR, its own branch** (`feat/victoria-metrics-cold-tier`) — all Flux files above +
   the `monitoring/kustomization.yaml`, `kube-prometheus-stack` remoteWrite, and Grafana datasource
   edits. One concern, one PR.
3. Merge → Flux reconciles → run the verification checklist.
