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

## Permanent clear: restart the instance-manager

A new instance-manager starts with empty tgt state. This is **proven**, not assumed: across all 7
affected instance-managers every leaked target postdates its pod's `creationTimestamp`, so no leak
has ever survived a restart.

A restart kills every engine and replica process on that node, so **drain the node first** and it
costs nothing beyond the drain:

```bash
kubectl cordon <node>
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data   # volumes move off
kubectl delete pod -n longhorn-system <instance-manager-on-that-node>
kubectl uncordon <node>
```

**Do this as part of routine node maintenance.** The ansible playbooks already cordon and drain with
a Longhorn gate; adding the instance-manager delete means leaks can never accumulate across cycles.

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
