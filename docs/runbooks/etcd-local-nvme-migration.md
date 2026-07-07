# Runbook: Migrate etcd/CP storage to per-host local NVMe

**Created:** 2026-07-04
**Owner:** cluster admin
**Risk:** Medium — touches etcd storage. Quorum-safe if the rolling sequence and health gates below are followed exactly.
**Downtime:** None expected. Storage vMotion stuns each VM sub-second; only one etcd member moves at a time so quorum (2/3) never drops.

## Why this exists

The three control-plane VMs (`k8scp01/02/03`) run on a datastore dedicated to CP nodes, but
that datastore is carved from the **same physical ZFS pool that backs every other VM**. etcd's
write path is fsync-bound: every Raft proposal must durably hit disk before it commits. When an
unrelated workload hammers the shared pool (the nightly Velero `daily-full` kopia backup is the
repeat offender), etcd WAL fsync latency climbs from a ~14 ms baseline to 50 ms+, blows the
default leader-election renew deadline (10 s) across dependent controllers, and cascades into a
control-plane restart storm.

Moving each CP VM's disks onto **local NVMe on its own hypervisor** physically isolates etcd from
all shared-pool contention. This is the correct storage tier for etcd (low-latency, no noisy
neighbours) and is a standard pattern.

### Why local NVMe is safe here despite no vSphere HA/DRS across local disks

etcd is **not** protected by vSphere HA — it is protected by **Raft**. Three members, quorum = 2.
If a hypervisor dies, that host's single etcd member is lost, but the remaining two hold quorum and
the cluster keeps serving. When the host comes back (or the VM is rebuilt), the member re-syncs from
the leader. Host-pinning a CP VM to local storage is therefore acceptable **provided the hard
requirement below holds**.

> **HARD REQUIREMENT — anti-affinity: exactly one CP VM per ESXi host.**
> Local NVMe pins each CP VM to a single host. If two CP VMs ever land on the same host, one host
> failure takes out two of three etcd members and the cluster loses quorum. A DRS "must run on
> host" rule per VM (step 5) enforces this. **Confirm the three CP VMs are on three distinct hosts
> before starting** and never colocate them afterward.

## Prerequisites / pre-flight

Run every check below and record the output before touching anything.

1. **etcd DB is small enough to fit local NVMe with headroom.** Current DB is ~305 MB (209 MB in
   use). A 10–20 GiB local-NVMe volume per CP VM is generous. Confirm live:

   ```bash
   # etcd container is distroless — no shell. Call etcdctl directly (v3.6, API v3 default).
   # NOTE arg order: subcommand FIRST, then --cluster and flags.
   C="--cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key"
   kubectl -n kube-system exec etcd-k8scp01 -- \
     etcdctl endpoint status --cluster --endpoints=https://127.0.0.1:2379 $C -w table
   ```

2. **Each target hypervisor has a local-NVMe datastore with free space ≥ the CP VM's total VMDK
   size** (OS + etcd data disk). Check in vCenter → host → Datastores. Do not undersize — leave room
   for the VM's swap (`.vswp`) and any snapshot during the move.

3. **A fresh etcd/Velero backup exists and is `Completed`.** This is the rollback floor.

   ```bash
   kubectl get backups.velero.io -n velero \
     -l velero.io/schedule-name=velero-daily-full \
     --sort-by=.metadata.creationTimestamp \
     -o custom-columns='NAME:.metadata.name,PHASE:.status.phase' | tail -3
   ```

4. **Confirm the three CP VMs are on three distinct ESXi hosts** (vCenter → VMs and Templates, or
   the Host column in the hosts/clusters view). Record which CP VM is on which host.

5. **Map etcd member IP → CP node → host** so you move the right VM's disks each round:

   ```bash
   C="--cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key"
   kubectl -n kube-system exec etcd-k8scp01 -- \
     etcdctl member list --endpoints=https://127.0.0.1:2379 $C -w table
   ```

   At time of writing: `192.168.152.8` = k8scp01, `192.168.152.9` = k8scp02,
   `192.168.152.10` = k8scp03. **Verify — do not assume.**

## Baseline capture (record before, compare after)

```bash
# 1. Cluster health + per-endpoint commit latency (want single-digit ms at a quiet hour)
C="--cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key"
kubectl -n kube-system exec etcd-k8scp01 -- \
  etcdctl endpoint health --cluster --endpoints=https://127.0.0.1:2379 $C
# Baseline 2026-07-04 (quiet): .8=8.5ms .9=9.7ms .10=7.5ms healthy

# 2. WAL fsync p99 over 5m, per member — run in Grafana Explore (Prometheus) or the Prometheus UI:
#    histogram_quantile(0.99, sum(rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])) by (instance, le))
#    Target after migration: p99 < 10 ms even DURING the nightly Velero daily-full window (03:00 UTC).
#    Pre-migration this metric spiked to ~50 ms+ during that window.

# 3. Backend commit p99 (secondary signal):
#    histogram_quantile(0.99, sum(rate(etcd_disk_backend_commit_duration_seconds_bucket[5m])) by (instance, le))
```

## Migration — quorum-safe rolling Storage vMotion

**Golden rule: one member at a time. Never start the next member until the previous one is back
`healthy` and fully caught up (RAFT APPLIED INDEX advancing, matching peers).** This keeps 2/3
quorum throughout, so there is no downtime.

Do the **leader last** (or step it down first) to avoid an extra election mid-move. Identify the
leader with `IS LEADER = true` in the endpoint status table.

For each CP VM, in order `k8scp03 → k8scp02 → k8scp01` (non-leader members first; adjust to your
live leader):

1. **Pre-check quorum is healthy** (all three `is healthy`):

   ```bash
   kubectl -n kube-system exec etcd-k8scp01 -- \
     etcdctl endpoint health --cluster --endpoints=https://127.0.0.1:2379 $C
   ```

2. **Storage vMotion the VM's disks** to the target host's local-NVMe datastore:
   vCenter → right-click the CP VM → **Migrate** → **Change storage only** → select the host's
   local-NVMe datastore → keep the VM on its current host (compute stays put; only storage moves) →
   finish. The VM stays powered on; expect a sub-second stun at cutover.

   > Move **all** of the VM's VMDKs (OS + any dedicated etcd data disk) so nothing is left on the
   > shared pool. If the etcd data lives on a dedicated VMDK, at minimum that disk must move; moving
   > all disks is simpler and keeps the VM fully host-local.

3. **Wait for the member to rejoin and catch up.** The static pod does not restart (storage vMotion
   is transparent to the guest), but verify health and that its RAFT APPLIED INDEX matches the
   other two:

   ```bash
   kubectl -n kube-system exec etcd-k8scp01 -- \
     etcdctl endpoint status --cluster --endpoints=https://127.0.0.1:2379 $C -w table
   kubectl -n kube-system exec etcd-k8scp01 -- \
     etcdctl endpoint health --cluster --endpoints=https://127.0.0.1:2379 $C
   ```

   **Gate:** all three `is healthy`, all three RAFT APPLIED INDEX within a few of each other and
   advancing. Only then proceed to the next member.

4. Repeat for the next member. Do the current leader last.

## Post-migration per VM — pin and de-risk

For each migrated CP VM:

1. **Create a DRS "Must run on this host" VM-to-Host affinity rule** (or Host affinity via a VM
   group of one) tying the VM to the host whose local NVMe now holds its disks. Local storage
   already pins it, but the explicit rule prevents a well-meaning migration attempt and documents
   intent.

2. **Disable vSphere HA restart for the VM** (VM override → HA restart priority = **Disabled**). HA
   cannot restart a VM whose storage is local to a dead host anyway; leaving it enabled just yields
   a failed restart event. Recovery on host loss is Raft re-sync (or rebuild), not HA.

3. **Confirm the anti-affinity invariant still holds** — three CP VMs, three distinct hosts.

## Reclaim the vacated CP datastore

Once **all three** CP VMs are confirmed running from local NVMe (no VMDK, swap, or snapshot left on
the old CP datastore):

1. In vCenter, browse the old CP datastore and confirm it is empty of CP VM files.
2. Unmount / delete the datastore (or repurpose the space back to the shared ZFS pool). This is the
   space reclaim — no more storage permanently tied up for CP nodes on the shared pool.

> **Done 2026-07-07.** All three CP VMs are on local NVMe. The old `ds-k8s-cp-ssd` VMFS datastore —
> carved from the interim `pool_1/vmstorage/k8s-control-plane` zvol (125 GiB) — was unmounted and the
> zvol destroyed, returning that space to the shared ZFS pool. See **Results** below for the
> latency observed across the removal (zero perturbation).

## Verification (the point of the whole exercise)

Re-run the WAL fsync p99 query **during the next nightly Velero `daily-full` window (~03:00 UTC)**,
which was the reliable trigger for the latency spike:

```
histogram_quantile(0.99, sum(rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])) by (instance, le))
```

**Success criteria:** p99 stays < 10 ms across all three members *through* the backup window (vs.
50 ms+ before). Also confirm no `PrometheusTSDBCompactionsFailing`-style storage-contention
knock-ons and no leader-election / controller restarts (`kubectl get pods -n kube-system | grep -E
'controller-manager|scheduler'` restart counts stop climbing).

This runbook is one of two shock absorbers for the same incident class; the other two (liveness-probe
widening and leader-election timeout widening) live in the ansible `harden-cp-probes` work and cover
the case where a latency blip still occurs. Storage isolation removes the trigger; the ansible
hardening tolerates any residual blip.

## Results — verified 2026-07-07

All three CP VMs are running from local NVMe. Steady-state WAL fsync latency has collapsed to
**~1 ms** on every member — roughly two to three orders of magnitude better than the shared-pool
floor that triggered the incident class.

| Era | Storage | WAL fsync p99 |
|-----|---------|---------------|
| Original (incident) | Shared ZFS pool, contended | **350–385 ms** |
| Interim fix | Dedicated `pool_1` zvol (`ds-k8s-cp-ssd`) | 7.6–57 ms |
| **Now (local NVMe)** | Per-host local NVMe | **~1 ms** |

Post-migration steady-state (instant `histogram_quantile(0.99, …)`, last / max over a 30 m window):

| Member | WAL fsync p99 (last / max) | backend commit p99 |
|--------|----------------------------|--------------------|
| `192.168.152.8` (k8scp01) | 1.15 / 2.89 ms | 1.0–4.65 ms |
| `192.168.152.9` (k8scp02) | 0.99 / 1.52 ms | 1.0–4.65 ms |
| `192.168.152.10` (k8scp03) | 1.00 / 1.05 ms | 1.0–4.65 ms |

Cluster health across the whole window: **3/3 members reporting a leader, 0 leader changes.**

**Zero perturbation from the datastore removal.** The old `ds-k8s-cp-ssd` datastore was unmounted and
its `pool_1` zvol destroyed while etcd was watched live (five consecutive clean reads, ~04:52–05:02
UTC). fsync p99 stayed ~1–3 ms throughout the unmount / iSCSI rescan — the CP VMs no longer touch
that storage path at all, so removing it was a no-op for etcd.

> **Caveat — the during-backup-window measurement is still pending a clean nightly capture.** The
> success criterion above (p99 < 10 ms *through* the 03:00 UTC Velero `daily-full` window) could not
> be freshly sampled on 2026-07-07: Prometheus was recovering from a WAL-fill outage (2026-07-03 →
> 07) and its history only reaches back to ~04:42 UTC, after that night's backup window had already
> passed. The ~1 ms steady-state and the zero-perturbation observation strongly imply the window will
> be clean, but confirm it against the next unaffected `daily-full` run before closing this out.

### Query method (for re-running the above)

The Prometheus pod image is distroless (no shell / wget). Query it via the Grafana pod, which has
`/usr/bin/wget` and network access to Prometheus as a datasource:

```bash
GRAFANA_POD=$(kubectl get pods -n monitoring -l app.kubernetes.io/name=grafana \
  -o jsonpath='{.items[0].metadata.name}')
# URL-encode the PromQL, then:
kubectl exec -n monitoring "$GRAFANA_POD" -c grafana -- \
  wget -qO- 'http://kube-prometheus-stack-prometheus:9090/api/v1/query?query=<encoded-promql>'
```

Results nest under `data.result` (not top-level `result`). The etcd fsync series carry
`job="kube-etcd"`, `instance="192.168.152.{8,9,10}:2381"`.

## Rollback

If a member fails to rejoin healthy after its move, or latency regresses:

1. **Do not proceed to the next member.** With 2/3 still healthy you have quorum and time.
2. Storage vMotion the affected VM's disks **back** to the original CP datastore (same
   "change storage only" flow). It rejoins as before.
3. If a member's data is ever suspected corrupt (it should not be — storage vMotion is
   block-consistent), remove it from the cluster and re-add it clean:

   ```bash
   # remove the bad member (run from a HEALTHY member)
   kubectl -n kube-system exec etcd-k8scp01 -- \
     etcdctl member remove <MEMBER_ID> --endpoints=https://127.0.0.1:2379 $C
   # then rejoin per the kubeadm etcd member re-add procedure
   ```

4. Worst case, restore from the Velero/etcd backup captured in pre-flight.

## Gotchas

- **etcd container is distroless** — no `sh`/`bash`. `kubectl exec ... -- sh -c '...'` fails with
  `exec: "sh": executable file not found`. Call `etcdctl` as the exec argv directly.
- **etcdctl arg order:** the subcommand comes first, then flags. `etcdctl --cluster endpoint status`
  errors with `unknown command "status"`; use `etcdctl endpoint status --cluster ...`.
- **Move ALL disks, not just the OS disk.** If a dedicated etcd data VMDK is left on the shared
  pool, the isolation is defeated and the latency spike returns.
- **One member at a time, health-gated.** Moving two at once (or not waiting for catch-up) risks
  dropping below quorum if anything goes wrong mid-move.
- **Never colocate two CP VMs on one host.** This is the single failure mode that turns local
  storage from "fine" into "loses quorum on one host failure."
