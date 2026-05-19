# Storage Rules

## HARD CONSTRAINT — verify Longhorn capacity before setting PVC sizes

**Before committing any PVC size in a new app, check that Longhorn can actually schedule it.**

Longhorn uses replica-based storage. The default replica count is **3**. This means a 50Gi PVC requires ~150Gi of free space across the cluster (spread across replica nodes). A PVC that appears small can be unschedulable if free space is fragmented or insufficient.

**Never set a PVC size based on defaults or what "seems reasonable" without verifying.**

### How to check available capacity

```bash
# Check schedulable space per node (look at "schedulable" column)
kubectl get nodes -o custom-columns=\
'NAME:.metadata.name,SCHEDULABLE:.metadata.annotations.node\.longhorn\.io/longhorn-schedulable-storage'

# Or check in the Longhorn UI: Storage → Nodes → available column
# Each node must have free space >= PVC size for a replica to land there
# With 3 replicas, need 3 nodes each with >= PVC size free
```

### What happened on 2026-04-09

Harbor was provisioned with a 50Gi registry PVC (chart default). The cluster did not have 150Gi of free Longhorn space (50Gi × 3 replicas). The PVC sat Pending, which cascaded into stale iSCSI mount errors on k8sworker01 and blocked Harbor, Radarr, and other pods for hours.

Fix: reduced to 25Gi (PR #293). Always size PVCs based on actual need + available capacity, not chart defaults.

### Sizing guidelines for this cluster

| Use case | Reasonable size |
|----------|----------------|
| App config/data (arr stack, small apps) | 1–5Gi |
| Database (CNPG) | 5–20Gi |
| Container registry (Harbor) | 25Gi (expand via Longhorn online resize if needed) |
| Media/download staging | check free space first |

### Longhorn online resize

If a PVC turns out to be too small after the fact, Longhorn supports online expansion:
1. Edit the PVC: `kubectl patch pvc <name> -n <ns> --type=merge -p '{"spec":{"resources":{"requests":{"storage":"<new-size>"}}}}'`
2. Longhorn expands the volume without downtime.

**Start conservative. Expand later. Never provision speculatively large.**

## Deployment update strategy for RWO PVCs

**Any Deployment that mounts a `ReadWriteOnce` PVC must set `strategy: type: Recreate`.**

```yaml
spec:
  replicas: 1
  strategy:
    type: Recreate
```

`RollingUpdate` (the default) starts the new pod before terminating the old one. A RWO volume can only be attached to one node at a time, so the new pod blocks in `ContainerCreating` indefinitely waiting for the volume to detach — which never happens automatically. The result is a frozen rollout that requires manual intervention.

**How to check a PVC's access mode:**

```bash
kubectl get pvc <name> -n <namespace> -o jsonpath='{.spec.accessModes}'
```

`ReadWriteOnce` (RWO) → must use `Recreate`. `ReadWriteMany` (RWX) → `RollingUpdate` is fine.

**In this cluster:**
- Longhorn PVCs: always RWO
- SMB PVCs (`smb` StorageClass): RWO as provisioned (even though SMB supports RWX, the CSI driver defaults to RWO here)
- local-path PVCs: always RWO

The brief downtime during `Recreate` rollouts is acceptable for single-replica stateful apps. If zero-downtime deploys are required, the workload needs RWX storage or a StatefulSet with per-replica PVCs.

## Multipath must be blacklisted on all worker nodes

Longhorn iSCSI volumes conflict with `multipathd` on Ubuntu 24.04 (multipath is enabled by default). This causes `exit status 32` stale mount failures. Every worker node — including DMZ workers — must have Longhorn devices blacklisted in `/etc/multipath.conf`.

**Apply this to every new worker node before it joins the cluster.**

Full procedure: `docs/runbooks/longhorn-multipath-blacklist.md`
