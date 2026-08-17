# Velero Rules

## Current configuration

- **Schedules (4):**
  | Schedule | Cron (UTC) | BSL | TTL |
  |---|---|---|---|
  | `velero-daily-full` | `0 3 * * *` | MinIO | 96h |
  | `velero-daily-b2` | `0 4 * * *` | Backblaze B2 | 2160h |
  | `velero-grafana-b2` | `0 5 * * *` | Backblaze B2 | 2160h |
  | `velero-monthly-b2` | `0 6 1 * *` | Backblaze B2 | 8760h |

  Schedules run **serially** — a backup created while another is running goes to
  `Queued`. There is no cancel field; `itemOperationTimeout` (4h) drains the queue.
- **FSB:** `defaultVolumesToFsBackup: true` on all four, `parallelFilesUpload: 2`
- **Excluded namespaces:** `kyverno`, `longhorn-system`, `metallb-system`, `minio`,
  `monitoring`, `tofu`, `trivy-system`, `velero`
- **Resource policy:** `velero-skip-smb-policy` — skips `smb` StorageClass volumes (NAS
  shares) **and all `emptyDir` volumes**. Applied to `daily-full`, `daily-b2` and
  `monthly-b2`; `grafana-b2` has none. Name is historical, scope is wider.

## A Velero backup that matches nothing still reports Completed

`velero-victoria-metrics-b2` selected `app: victoria-metrics`, which no pod or PVC in
`monitoring` has ever carried — the chart forces `app: server`. It matched zero volumes
for its entire life and reported `Completed`, `0 errors`, `4 items` every single night.
Nothing alerts on this: phase is the only signal Velero exposes, and an empty backup is a
successful one. `velero_backup_items_total` reads 0 for every schedule here, so it cannot
be used as a content guard either.

**So: for any schedule with a `labelSelector`, the selector is only verified by checking
that a PodVolumeBackup was actually created.**

```bash
kubectl get podvolumebackups.velero.io -n velero \
  -l velero.io/backup-name=<backup-name> --no-headers | wc -l   # 0 means it backed up nothing
```

**A selector must match a label carried by both the pod and the PVC.** The pod alone gets
you a PVB (the data), the PVC alone gets you the object needed to restore into. Matching
only the pod silently produces an unrestorable backup. `app.kubernetes.io/name` is usually
on both; the hand-applied `app` label is usually pod-only. Verify, don't assume:

```bash
kubectl get pvc -n <ns> <pvc> -o jsonpath='{.metadata.labels}' | python3 -m json.tool
```

## The monitoring namespace is deliberately mostly unbacked

`monitoring` is in `excludedNamespaces` on `daily-full`, `daily-b2` and `monthly-b2`.
That is correct — the metrics are the bulk of it and Velero is the wrong tool:

- **victoria-metrics-lt** (cold, 395d, 750Gi) is backed up by its own `vmbackup` CronJob
  to `s3://vollminlab-k8s-backups/victoria-metrics-lt`. vmbackup asks VictoriaMetrics for
  a real snapshot first; FSB would walk a directory that background merges are actively
  rewriting and produce a torn copy.
- **victoria-metrics (hot, 30d)** is intentionally **not** backed up. Prometheus
  remote-writes the same stream to both tiers (measured 7d: 10.915B samples each, 0
  dropped, 0 failed), so the hot tier is an exact subset of the cold tier.
- **prometheus** (24h retention) is a scrape buffer — both tiers hold the same data longer.
- **grafana** IS backed up (`grafana-b2`), because grafana.db holds UI-created dashboards,
  users and API keys that exist nowhere else. The 35 sidecar dashboards are
  ConfigMap-provisioned and reproducible from git; grafana.db is not.

Before excluding a namespace, ask which of its PVCs hold state that is not reproducible
from git and not already stored elsewhere. Here that was exactly one, and it was the one
thing the broken schedule wasn't covering.
- **Node-agents:** DaemonSet with DMZ toleration; 9 pods (6 workers + 3 CPs)
- **`loadConcurrency` is pinned at the default 1 per node — do not raise it.** All worker
  VMDKs share one datastore; a full FSB pass already drives PSI full I/O-stall to 26-48%
  on every general worker. `parallelFilesUpload: 2` is the only remaining throttle.
- **BackupRepository CRs:** recreated automatically on first backup after any wipe

## Never FSB-back-up an emptyDir

The kubelet deletes an `emptyDir` when its pod is removed, so a restored pod can never
receive the contents — the backup is unrecoverable by construction. Worse, it is the
dominant PVB failure source: when a pod goes away mid-run (CNPG failover, Job completion),
every one of its volumes fails with `error identifying unique volume path on host` or
`pods "<x>" not found`.

This is handled **globally** by the `volumeTypes: [emptyDir]` skip in
`velero-skip-smb-policy`. Do **not** add new per-app
`backup.velero.io/backup-volumes-excludes` annotations for emptyDirs — that was the old
whack-a-mole approach (PRs #1009, #1033, #1034, #1035) and it is superseded.

Velero's FSB path already skips `hostPath`, `secret`, `configMap`, `projected` and
`downwardAPI` unconditionally, so those never need a policy entry.

## Recipe: a PVB frozen in `Prepared` (node-agent datapath-slot leak)

**Symptom.** One PVB sits at `Prepared` for tens of minutes with no error anywhere. Its
exposer pod (same name as the PVB, in `velero`) is `1/1 Running`, 0 restarts, and its log
stops dead at `Running data path service`. The velero server pod is parked at
`backupper.go:235` naming that pod — FSB is **head-of-line blocked**, so the whole backup
stalls until `itemOperationTimeout` expires 4h later.

**Fingerprint.** The node's node-agent re-emits `pod_volume_backup_controller.go:278` /
`exposer/pod_volume.go:253` / `pod_volume_backup_controller.go:303` on an exact **5-second
cadence** with zero error lines. That 5s requeue is the `ConcurrentLimitExceed` branch,
which logs only at Debug — the node-agent's in-memory datapath slot has leaked, so with
`loadConcurrency=1` no PVB on that node can ever acquire one again.

**Fix — restart the node-agent on that node.** State is in-memory only; the PVB completes
within seconds and the backup resumes.

**This is now automated — see the healer below. Only do it by hand if the healer is broken
or you need the slot back sooner than its next 10-minute run.**

```bash
# Find the stuck PVB and its node (.status.node is NOT populated — use .spec.node)
kubectl get podvolumebackups.velero.io -n velero \
  -o custom-columns='NAME:.metadata.name,PHASE:.status.phase,NODE:.spec.node,POD:.spec.pod.name' \
  | grep -Ev 'Completed|Failed'

kubectl delete pod -n velero -l name=node-agent --field-selector spec.nodeName=<node>
```

There is no upstream fix as of v1.18.2 — the changelog contains no node-agent datapath work.

## velero-pvb-healer — the automated form of that recipe

`clusters/vollminlab-cluster/velero/velero-pvb-healer/app/` — a CronJob running the recipe
above every 10 minutes. Manifests: `cronjob.yaml`, `rbac.yaml`, `kustomization.yaml`
(`configMapGenerator` over `heal.sh`), plus `heal_test.sh`.

**What it does each run:** list the PVBs of every non-terminal Backup → find one in `Prepared`
older than `STALL_SECONDS` → confirm that node has no PVB `InProgress` → restart that node's
node-agent, emit a Kubernetes Event, and stop.

**Deliberate constraints — don't loosen without re-reading why:**

- **Heals at most one node per run.** A node-agent restart is disruptive; one per 10 minutes
  bounds the blast radius and lets the next run confirm the first worked.
- **Skips a node with any PVB `InProgress`.** Under `loadConcurrency=1` a node legitimately has
  one PVB running and others waiting in `Prepared`. Without this guard the healer would kill a
  healthy in-flight backup. This is the single most important check in the script.
- **Cooldown annotation** (`pvb-healer.vollminlab.com/last-healed`, default 1h) is stamped on the
  PVB **before** the delete, so a crash mid-heal can't produce a restart loop.
- **The PVB list is scoped by `velero.io/backup-name` to running backups only.** PVBs live as long
  as their backup (2160h on the B2 schedules), so an unscoped list is thousands of objects and
  kubectl's decode OOM-killed the 64Mi container on *every* run (fixed in #1056). Scoping bounds
  it at one backup's PVB count permanently; raising the memory limit only defers the same failure.
  Scoping is not lossy: FSB is head-of-line blocked, so a PVB can only stall a backup that is
  still running — and if a backup does time out leaving orphaned `Prepared` PVBs, the leaked slot
  poisons the *next* backup, whose own `Prepared` PVB the healer catches on the same node.

**Tunables** are env vars on the CronJob: `STALL_SECONDS` (900), `COOLDOWN_SECONDS` (3600),
`NODE_AGENT_SELECTOR` (`name=node-agent`), `DRY_RUN` (`false`).

**Tests:** `sh heal_test.sh` — pure-shell stubs, no cluster needed. Must pass under both `dash`
and `busybox ash` (the image is `alpine/kubectl`). Not wired into CI, same as
`longhorn-mount-healer`.

```bash
# Did it actually run — or just exist? Check exit codes, not the CronJob's Ready status.
kubectl get jobs -n velero -l job-name --sort-by=.metadata.creationTimestamp | grep pvb-healer
kubectl logs -n velero -l job-name=<job> --tail=20

# One-off run without waiting for the schedule
kubectl create job -n velero --from=cronjob/velero-pvb-healer healer-manual-$(date +%s)
```

Two log lines look similar and mean different things: `no stalled PodVolumeBackups` means the
query returned rows and none matched; `no PodVolumeBackups found` means it got an empty set.
Only the first proves the scoped query is working.

**A merged, Ready, reconciled CronJob is not a working CronJob.** Between #1055 and #1056 this
healer was live and 100% non-functional — exit 137 on every invocation — while Flux reported
success throughout. After any CronJob PR lands, check the job pods' exit codes.

## Hard rule: measure before you act

Before diagnosing any backup issue or taking any corrective action, run the storage breakdown first:

```bash
# Bucket breakdown — answers "what is actually consuming space" in one command
kubectl exec -n minio $(kubectl get pods -n minio -l app=minio -o jsonpath='{.items[0].metadata.name}') \
  -- mc du --depth 2 velero-access/velero/

# PVC free space
kubectl exec -n minio $(kubectl get pods -n minio -l app=minio -o jsonpath='{.items[0].metadata.name}') \
  -- df -h /export
```

Never trigger a backup or write operation when storage is < 5% free. Check headroom first.

## Circular backup check

`defaultVolumesToFsBackup: true` backs up **every** pod's volumes by default.  
The namespace that hosts the backup object store (MinIO) **must** be in `excludedNamespaces` on every schedule that uses that BSL — otherwise Velero backs up the backup store into itself.

**On this cluster:** `minio` is excluded from both schedules. Do not remove it.  
If adding a new schedule or BSL, verify: does the BSL's storage namespace appear in `excludedNamespaces`?

## Velero kopia GC timing

Quick maintenance runs hourly (index compaction only). Full maintenance runs ~every 24h and is what actually deletes orphaned content blocks. If you delete backup objects and need space back immediately:

```bash
# Force-purge all versions from a kopia prefix (versioned bucket — delete markers alone don't reclaim space)
kubectl exec -n minio <minio-pod> -- sh -c \
  "mc alias set root http://localhost:9000 root '<rootPassword>' && \
   mc rm --recursive --force --versions root/velero/kopia/<namespace>/"

# Then delete the stale BackupRepository CR so Velero reinitializes clean
kubectl delete backuprepository.velero.io <namespace>-minio-kopia -n velero
```

## Checking backup status

```bash
# All schedules and last backup time
kubectl get schedules.velero.io -n velero

# Recent backup phase (Completed / PartiallyFailed / Failed)
kubectl get backups.velero.io -n velero -l velero.io/schedule-name=velero-daily-full \
  --sort-by=.metadata.creationTimestamp -o custom-columns='NAME:.metadata.name,PHASE:.status.phase,ERRORS:.status.errors'

# BackupRepository health
kubectl get backuprepositories.velero.io -n velero

# Kopia maintenance log for a namespace repo
kubectl logs -n velero $(kubectl get pods -n velero --sort-by=.metadata.creationTimestamp \
  | grep "<namespace>-minio-kopia-maintain" | tail -1 | awk '{print $1}')
```

## Gate for Cilium migration (Phase 8)

Do not start the Cilium CNI migration until a `daily-full` backup shows `Completed` status and a test restore has been validated. First expected clean backup: **2026-04-23 at 2am UTC** (after circular backup fix in PR #410).
