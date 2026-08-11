# Velero Rules

## Current configuration

- **Schedules (4):**
  | Schedule | Cron (UTC) | BSL | TTL |
  |---|---|---|---|
  | `velero-daily-full` | `0 3 * * *` | MinIO | 96h |
  | `velero-daily-b2` | `0 4 * * *` | Backblaze B2 | 2160h |
  | `velero-victoria-metrics-b2` | `0 5 * * *` | Backblaze B2 | 2160h |
  | `velero-monthly-b2` | `0 6 1 * *` | Backblaze B2 | 8760h |

  Schedules run **serially** — a backup created while another is running goes to
  `Queued`. There is no cancel field; `itemOperationTimeout` (4h) drains the queue.
- **FSB:** `defaultVolumesToFsBackup: true` on all four, `parallelFilesUpload: 2`
- **Excluded namespaces:** `kyverno`, `longhorn-system`, `metallb-system`, `minio`,
  `monitoring`, `tofu`, `trivy-system`, `velero`
- **Resource policy:** `velero-skip-smb-policy` — skips `smb` StorageClass volumes (NAS
  shares) **and all `emptyDir` volumes**. Applied to `daily-full`, `daily-b2` and
  `monthly-b2`; `victoria-metrics-b2` has none. Name is historical, scope is wider.
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

```bash
# Find the stuck PVB and its node (.status.node is NOT populated — use .spec.node)
kubectl get podvolumebackups.velero.io -n velero \
  -o custom-columns='NAME:.metadata.name,PHASE:.status.phase,NODE:.spec.node,POD:.spec.pod.name' \
  | grep -Ev 'Completed|Failed'

kubectl delete pod -n velero -l name=node-agent --field-selector spec.nodeName=<node>
```

There is no upstream fix as of v1.18.2 — the changelog contains no node-agent datapath work.

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
