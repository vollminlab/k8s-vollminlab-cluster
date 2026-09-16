# Longhorn stale iSCSI targets

A volume becomes **un-attachable on one specific node** while remaining perfectly healthy
everywhere else. Confirmed on 2026-09-12, when it kept `prowlarr` down for 3h34m.

## Symptom

Pod stuck `ContainerCreating`, recreated every ~40s (Longhorn's
`auto-delete-pod-when-volume-detached-unexpectedly: true`), with:

```
FailedAttachVolume ... volume <pvc> is not ready for workloads:
  waiting for the volume to fully detach. current state: detaching
```

The volume cycles `attaching -> faulted -> detaching` forever.

## Three things that look like the cause and are not

- **`robustness: faulted`.** Cosmetic while no engine runs. Check the replicas: if they read
  `failedAt: ""` and `state: running`, **no data is at risk**.
- **`RollingUpdate` on an RWO PVC.** Check it — but `Recreate` was already set here.
- **multipathd.** The node had `multipathd` active *and* the correct blacklist, no multipath maps,
  no `/dev/longhorn` entry and nothing mounted. The Longhorn node condition
  `Multipathd=False / MultipathdIsRunning` is normal with the blacklist in place.

**A clean detach does not fix it.** Scaling to 0 reaches `detached` with zero attachment tickets,
and the next attach fails identically. That is the tell.

## The real error, which is inside the instance-manager

```bash
IM=$(kubectl get pods -n longhorn-system -o wide --no-headers \
      | awk '$7=="<node>" && /instance-manager/ {print $1}' | head -1)
kubectl logs -n longhorn-system $IM --tail=2000 | grep -i 'failed to init frontend'
```

```
failed to delete target iqn.2019-10.io.longhorn:<pvc>:
  tgtadm --op delete --mode logicalunit --tid N --lun 1
  stderr: tgtadm: this logical unit is still active   (exit status 22)
```

`tgtd` runs **inside** the instance-manager and still holds an I_T nexus, while the host has **no
matching iSCSI session** (`iscsiadm -m session | grep <pvc>` returns nothing). A half-closed nexus:
the initiator tore its side down, tgtd never noticed. `/var/run/longhorn-<pvc>.sock` is still there,
dated whenever the engine died.

## Fix: start the engine on another node

The engine runs wherever the **pod** is scheduled and does not need a local replica, so another
node means another instance-manager with clean tgt state.

```bash
kubectl cordon <bad-node>
kubectl scale deploy -n <ns> <app> --replicas=0     # wait for state `detached`
kubectl scale deploy -n <ns> <app> --replicas=1     # lands elsewhere
kubectl uncordon <bad-node>
```

Measured: `attached/healthy` and `2/2 Running` in **33 seconds**, after 3.5 h of failing.

**Then fix the HelmRelease**, which a long attach failure also breaks:
`Upgrade "x" failed: client rate limiter Wait returned an error: rate: Wait(n=1) would exceed
context deadline`. Per `.claude/rules/flux.md`, **`helm rollback` first, then reconcile** — a bare
reconcile replays the stale failure. **Keep the bad node cordoned across both**, because each one
replaces the pod and it can land straight back.

## What does NOT work

Surgical tgt cleanup. Deleting the connection succeeds and changes nothing; both deletes still fail:

```bash
tgtadm --lld iscsi --op delete --mode conn --tid N --sid S --cid 0   # succeeds, no effect
tgtadm --lld iscsi --op delete --mode logicalunit --tid N --lun 1    # "still active", exit 22
tgtadm --lld iscsi --op delete --mode target --tid N                 # "still active", exit 22
```

Harmless to attempt — all 12 volumes on the node stayed `attached/healthy` — but it does not clear
the leak.

## Permanent clear: no node drain required

**Executed 2026-09-12: 17 leaks -> 3, zero outages.** Total disruption was ~24 s on one CNPG
*replica*, one bazarr restart and two alertmanager pod restarts.

### Why it is cheap

There are two instance-manager generations. New instances always go to the **current** generation,
so the **old** IMs slowly empty out. On 2026-09-12 the old IMs held **14 of the 17 leaks** but
hosted only **4 engines and 5 replicas between them**. You are moving a handful of processes, not
draining nodes.

**Longhorn deletes an old IM by itself once it is empty**, taking its leaked targets with it.
Observed twice during this run — the w03 and w02 old IMs vanished before the delete command ran.

### Procedure

**Restart the WORKLOAD first, then deal with the IM.** A workload restart detaches and reattaches
the volume, which both moves the engine into a current-generation IM and gives it a fresh engine
process.

```bash
# 1. list the old-generation IMs and what pins each one alive
for IM in $(kubectl get pods -n longhorn-system --no-headers | grep instance-manager | awk '{print $1}'); do
  kubectl get pod -n longhorn-system $IM \
    -o jsonpath='{.metadata.creationTimestamp}{"  "}{.spec.nodeName}{"  "}{.metadata.name}{"\n"}'
done | sort            # older timestamps = old generation

# 2. for each, find its running engines (= workloads) and replicas (= rebuilds)
#    then restart those workloads ONE AT A TIME, confirming attached/healthy after each

# 3. an old IM left holding only replicas: delete the pod directly
kubectl delete pod -n longhorn-system <old-instance-manager>
```

**Before restarting a workload, confirm its own volume has no leaked target anywhere** — otherwise
the restart lands in the failure mode at the top of this page. All four candidates were clean.

### Trap: deleting an IM that hosts a REPLICA can deadlock the rebuild

Deleting the w01 old IM killed alertmanager-0's replica process. The rebuild then **hung**:
`rebuildStatus: {}`, only 2 RW against `numberOfReplicas: 3`, volume `degraded`, and the
`FailedStartingSnapshotPurge` counter **resumed advancing** (8105 -> 8114) after having been frozen.

That is the stale is-rebuilding flag in the engine process (see the rebalancer/surge-move runbook
material). Note what it was **not**: the replica count was a correct 3, so there was **no orphan
replica to delete** — the usual fix did not apply.

**The fix is an engine restart: delete the workload pod.** The rebuild then ran
`0 -> 34% -> RW=3, attached/healthy` in about two minutes.

So when an old IM holds a replica, prefer restarting that volume's workload *before* deleting the
IM — and if a rebuild hangs afterwards, restart the workload rather than hunting for an orphan.

**Do not try to predict this from engine age.** The Engine CR's `creationTimestamp` is **not** the
process age: bazarr's CR reads 2025-10-05 while its process had been restarted minutes earlier.
The only thing the CR tells you is which instance-manager the process currently lives in.

### Deleting many CRs fragments etcd

Unrelated to tgt, but it came up in the same session: bulk-deleting custom resources leaves
free-but-unreclaimed space and fires `etcdDatabaseHighFragmentationRatio`. `kube-system/etcd-defrag`
normally runs at 22:00; by hand it takes ~26 s.

```bash
kubectl create job -n kube-system --from=cronjob/etcd-defrag etcd-defrag-manual-$(date +%s)
```

### What is left after the old IMs are gone

Leaks on **current-generation** IMs cannot be cleared this way, because those IMs host live load.
Measured 2026-09-12 for the 3 remaining:

| node | workloads that would lose their disk | replicas that would rebuild |
| --- | --- | --- |
| w04 | 12 | 15 |
| w06 | **0** | 8 |
| w02 | 3 | 24 |

**They clear for free on the next Longhorn chart upgrade**, which recreates every instance-manager —
exactly what the 2026-08-17 upgrade did. Only do one early if it has no engines (w06 above).

## Find them before they cause an outage

A target is leaked if its volume is not currently attached to that instance-manager's node. Every
hit is a landmine: if that volume's pod is ever scheduled onto that node, it cannot start.

```bash
for IM in $(kubectl get pods -n longhorn-system --no-headers \
            | grep instance-manager | grep Running | awk '{print $1}'); do
  NODE=$(kubectl get pod -n longhorn-system $IM -o jsonpath='{.spec.nodeName}')
  kubectl exec -n longhorn-system $IM -- tgtadm --lld iscsi --op show --mode target 2>/dev/null \
    | grep -oE 'pvc-[0-9a-f-]+' | sort -u | while read T; do
      AT=$(kubectl get volumes.longhorn.io -n longhorn-system $T \
           -o jsonpath='{.status.currentNodeID}' 2>/dev/null)
      [ "$AT" != "$NODE" ] && echo "STALE $IM ($NODE) -> $T attachedTo=${AT:--}"
    done
done
```

Measured 2026-09-12: **17 leaked targets across 7 instance-managers.**

## Where they come from

Leaks form in **bursts**, when many volumes move between nodes at once — not as a steady drip. The
backing socket's mtime dates each one:

| when | leaks | what happened |
| --- | --- | --- |
| 2026-08-08 06:57-07:02 | 8 | all 9 nodes rebooted at 06:28-06:39; leaks formed as volumes reattached |
| 2026-08-10, 08-11, 08-22 | 5 | overnight VolSync/backup-window churn |
| 2026-09-09 07:20-07:21 | 2 | two nodes at once |

So the leak rate is a function of how often volumes are forced to move. Fewer node flaps and I/O
stalls means fewer leaks — which is the same argument as every other storage item on this cluster.

Longhorn **1.12.1 is already the latest release**, and `node-drain-policy` is already
`block-if-contains-last-replica`, so there is no version or setting that prevents this today.

## Why it went unnoticed for 3h34m

`PodNotReady` and `KubePodNotReady` both matched — about **200 times** — and every instance died in
`pending`. A pod-name-keyed alert with a `for:` duration **cannot fire** when pods are recreated
faster than the window. `WorkloadReplicasUnavailable` (added with this runbook) keys on the
Deployment instead, whose series is stable across pod churn; backtested against this outage it
matches continuously from 07:00 to 10:15.
