# Runbook: Longhorn Volume ext4 Corruption

## Why this is easy to miss

Longhorn replicates at the **block layer**. It verifies that replicas agree with each other — it does
**not** verify that the bytes form a valid filesystem. A volume whose ext4 metadata is corrupt will
still report `robustness: healthy`, because every replica holds an identical copy of the corruption.

The result is a volume that looks fine in every Longhorn view while carrying a latent defect at the
filesystem layer.

Worse, a running pod will not notice. Linux serves the damaged directory block from page cache, so an
app can run for weeks on a corrupt volume with no symptom at all. **The defect only surfaces on a cold
remount**, when the CSI driver runs `fsck -a` before mounting. Preen mode (`-a`) refuses to repair a
corrupt directory, so it bails and the mount fails.

That makes this a landmine: it detonates on a node reboot, a Kubernetes upgrade, or any pod
reschedule — i.e. at the least convenient possible moment, unplanned.

---

## Symptoms

Any of these, especially in combination:

- A VolSync/restic backup for one PVC is stale for days while every other backup succeeds
- The VolSync mover pod is stuck or CrashLooping; deleting the clone PVC and letting VolSync
  regenerate it does **not** help
- Mover or CSI logs show a specific, repeatable `fsck` failure, e.g.
  ```
  Directory inode 399758, block #0, offset 0: directory corrupted
  ```
- `kubectl get volumes.longhorn.io -n longhorn-system` reports `robustness=healthy` for the volume
- The owning pod is `Running` and healthy, and has been for a long time

---

## Diagnosis — is it the source volume or just a bad clone?

This is the key branch. A single failed clone can be a one-off torn write and is not worth a repair
window. Corruption in the **source** volume is.

**The test: does a freshly regenerated clone fail at the exact same offset?**

Delete the clone PVC and the stuck mover pod so VolSync builds a new clone from a new snapshot, then
compare the failure.

- **Different error, or success** → transient. The clone was bad; nothing further to do.
- **Byte-identical error — same inode, same block, same offset** → the corruption is stable on-disk in
  the source volume. Two independently-taken snapshots cannot reproduce the same defect by chance.
  Proceed to repair.

Record the exact inode/offset string from both failures before moving on; it is the evidence, and
you want it for the incident note.

---

## Repair procedure (offline e2fsck)

Operational (kubectl + Longhorn UI), **not** GitOps. Do not try to express any of this in the repo.

Requires **brief downtime** for the owning workload — budget ~5 minutes for a small config volume,
longer for a large one. Schedule it; do not improvise it during an outage.

Substitute the real workload, namespace, and PV name throughout.

### 1. Suspend the HelmRelease

Flux will fight a manual scale-down and re-scale the Deployment out from under you.

```bash
flux suspend helmrelease <app> -n <namespace>
```

### 2. Scale to zero and confirm the volume fully detaches

```bash
kubectl scale deploy <app> -n <namespace> --replicas=0
kubectl get volumes.longhorn.io -n longhorn-system <pv-name> \
  -o jsonpath='{.status.state}{"\n"}'
```

Wait for `detached`. **Do not proceed while the volume is still attached** — running `e2fsck` against
a mounted or attached filesystem will make the damage worse, not better.

### 3. Attach in maintenance mode and repair

In the Longhorn UI: Volume → **Attach in Maintenance Mode**, pick a node. (Maintenance mode attaches
the block device without mounting a filesystem, which is exactly what `e2fsck` needs.)

Then, on that node:

```bash
e2fsck -fy /dev/longhorn/<pv-name>
```

`-f` forces a check even if the superblock looks clean; `-y` auto-answers yes. Expect it to clear or
rebuild the offending directory inode. Capture the full output — it is the record of what was lost.

### 4. Detach and restore service

Detach the volume in the Longhorn UI, then:

```bash
kubectl scale deploy <app> -n <namespace> --replicas=1
flux resume helmrelease <app> -n <namespace>
```

### 5. Verify

Do not call this done until all three pass:

```bash
# a) workload is back
kubectl get pods -n <namespace> -l app=<app>

# b) the app is actually reachable, not just Running

# c) the backup that was failing now completes
kubectl get replicationsource -n <namespace> <name> \
  -o jsonpath='{.status.lastSyncTime}{"\n"}'
```

`lastSyncTime` must advance to the current date. Until it does, the volume is repaired but unproven —
the clone mounting `fsck`-clean is the only real confirmation the defect is gone.

---

## Notes

- **`e2fsck` can lose data.** Rebuilding a corrupt directory inode may orphan its entries into
  `lost+found`. For a config volume this is usually acceptable; for anything else, take a Longhorn
  snapshot before step 3 so there is a rollback point.
- **Repair proactively.** The whole point is choosing the downtime window instead of discovering the
  problem during an unplanned reboot.
- **Longhorn's health status is not a filesystem check.** Do not treat `robustness=healthy` as
  evidence that a volume is intact.

---

## Worked example — jellyfin-config, 2026-07

Origin: the 2026-07-07 03:00 UTC VolSync backup storm, when all 13 `ReplicationSource`s fired
simultaneously against a near-full worker04.

`jellyfin-config` backups wedged from 2026-07-07. Deleting the corrupt clone and letting VolSync
regenerate produced a new clone that failed at the *identical* defect —
`Directory inode 399758, block #0, offset 0: directory corrupted` — confirming stable corruption in
the source volume `pvc-039f0692-6a94-4428-bf1c-b30b0e4193e2`. Longhorn reported the volume healthy
throughout, and jellyfin had been `Running` for 38 days on it.

Offline `e2fsck` per the procedure above cleared it. `jellyfin-config-restic` resumed syncing and was
confirmed current as of 2026-07-21.

The storm itself was fixed separately by staggering the 13 `ReplicationSource` schedules 10 minutes
apart across 00:00–02:00 UTC (previously all `0 3 * * *`), which also separates VolSync from the
Velero windows at 03:00 and 04:00.
