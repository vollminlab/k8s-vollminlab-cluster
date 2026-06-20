# Storage-Induced Crashloop Resiliency — Design

**Status:** Draft for review (revised after adversarial evaluation)
**Created:** 2026-06-20
**Author:** Scott Vollmin (with Claude)
**Related incident:** Longhorn stale-mount EIO recurrence 2026-06-20 (radarr w03, sonarr w04 crashlooping 2+ days; grafana datasource landmine detonated by the same w04 blip)

## Problem

A Longhorn replica blip — usually triggered by node memory/IO pressure on a worker — can flip a
pod's in-pod ext4 mount into an EIO (errored, read-only) state while Longhorn still reports the
volume healthy and attached. The data layer is fine; only the in-pod mount is stale.

The app reacts by crashing: radarr exits 134 on `I/O error: /config/config.xml`, sonarr segfaults
exit 139 on the SQLite load. Kubernetes restarts the container in place, it hits the same stale
mount, and crashes again — **CrashLoopBackOff**.

**The trap that makes this self-perpetuating:** a CrashLoopBackOff pod never triggers a Longhorn
volume detach. The pod stays scheduled on the same node, the volume stays attached, the stale mount
is never cleared. Kubernetes' built-in restart loop deletes the *container*, never the *pod*, so it
can never escape. The only thing that clears it is a full **detach/reattach** cycle.

**Why the detach is the hard part (the crux):** the proven manual cure is
`scale 0 → wait until the Longhorn volume reports detached → scale 1`. The load-bearing step is the
**wait**. An errored ext4 filesystem cannot unmount cleanly — the kernel reports the mount `busy`
(`exit status 32: already mounted or mount point busy`), and the detach takes time to complete. Any
remediation that deletes/recreates the pod *without waiting for the volume to fully detach* races the
new pod against the still-attaching volume and re-wedges it in `ContainerCreating`. Kubernetes'
6-minute force-detach (kubernetes#65392) only fires when the **node is unreachable**, not on a hung
unmount on a *healthy* node — and these nodes are healthy. So nothing in the stock system performs
the wait.

On 2026-06-20 this left radarr and sonarr crashlooping for **2+ days** (36–38 restarts each) until a
human ran the manual scale-0→1 recovery. That manual step is the gap this design closes.

This is **not** a node-specific problem and the fix must not be node-specific. Node memory pressure
is *a* trigger, but the resiliency requirement is that the cluster heals itself from a stuck pod
**regardless of which app, which node, or what triggered the stale mount**. The cure must be
app-agnostic and node-agnostic.

## Why not the descheduler (rejected approach)

The obvious low-effort option is to reuse the **descheduler** already running in `kube-system`
(CronJob, chart 0.36.0 / descheduler **v0.36**) and add its `RemovePodsHavingTooManyRestarts`
plugin to evict stuck pods. This was the original draft of this design. Adversarial evaluation
killed it:

- **Eviction does not perform the wait.** Eviction is a graceful pod-delete followed by an immediate
  reschedule (the Deployment's `Recreate` strategy starts the replacement as soon as the old pod is
  gone). There is no knob to make the descheduler block until the Longhorn volume reports `detached`.
  On the *errored/busy-mount* subset — which is precisely the 2-day incident — the replacement pod
  races the still-attaching volume and wedges in `ContainerCreating`, re-triggered every cycle. It
  heals only the *clean-restart* subset and turns the errored subset from a loud CrashLoopBackOff
  into a quieter hang.
- **Its metrics are unobservable here.** The descheduler runs as a CronJob; its job pods live ~4s and
  the chart only renders a Service/ServiceMonitor in `kind: Deployment` mode. The intended
  Layer-3 alert on `descheduler_pods_evicted_total` is therefore unscrapeable without restructuring
  the descheduler itself.
- **No eviction rate caps in the current config** — adding the plugin without
  `maxNoOfPodsToEvictTotal`/per-node/per-namespace caps risks a thundering-herd eviction.

A remediation step that *always* performs the full detach-with-wait strictly dominates: it heals both
the clean and errored subsets. That is what this design builds. The descheduler is left untouched,
continuing to run only its `LowNodeUtilization` balance profile.

## Goal

Make the cluster self-heal from storage-induced crashloops with no human intervention, and make that
self-healing observable. Concretely:

1. **Resiliency (primary):** any workload whose pod is stuck on a stale Longhorn mount past a
   threshold is automatically returned to health by codifying the proven runbook
   (`scale 0 → wait detached → scale to original`). This is the cure and it heals *every* variant —
   every app, every node, every trigger.
2. **Visibility:** when the self-healing fires, an alert surfaces it, so a recurring storage problem
   can't hide behind a system that silently papers over it.

Frequency reduction (making the stale mount happen less often) is explicitly **secondary** and kept
to a zero-risk declarative nudge — see Layer 2. The node RAM rework tracked separately
(`project_worker02_memory_pressure`) remains the real frequency fix and is out of scope here.

## Non-goals

- Fixing the node memory/IO pressure that triggers the blips (separate RAM-rework effort).
- Patching `dataLocality` on existing volumes (triggers background replica rebuilds; deferred to a
  controlled operational step gated on a Longhorn capacity check).
- Healing StatefulSet-backed workloads (v1 targets Deployments only — the arr/monitoring pattern;
  see Open questions).
- Any app-specific or node-specific remediation.

## Approach

A small **`longhorn-mount-healer` CronJob** in `kube-system` (same home as the descheduler) that runs
the known-good runbook on a schedule. It is a kubectl/bash job — no operator framework, no watch
loop — because the failure is a *sustained stuck state*, not a fast transient, so periodic polling is
sufficient and far simpler to reason about and roll back.

### Layer 1 — `longhorn-mount-healer` (the cure) — PRIMARY

New raw-manifest app at `clusters/vollminlab-cluster/kube-system/longhorn-mount-healer/app/`:
`cronjob.yaml`, `rbac.yaml`, `configmap.yaml` (the script), `kustomization.yaml`. Image
`alpine/kubectl:1.33.4` (cluster convention; `apk add jq` in-script if needed). Schedule `*/10`.

**Detection — a workload is "stuck on storage" this run if BOTH hold:**

1. Its pod is in one of:
   - **CrashLoopBackOff** with summed `restartCount > 5` (the radarr exit-134 / sonarr exit-139
     case). Threshold 5 ≈ a few minutes of backoff — long enough to exclude a transient startup
     crash, far short of the 36–38 we hit by hand.
   - **Pending/ContainerCreating** carrying a `FailedMount` event matching
     `already mounted or mount point busy` (or Longhorn attach errors) — the wedged-reattach case.
2. The pod mounts a **Longhorn `ReadWriteOnce` PVC**. Mapping: pod → mounted PVC (pod namespace) →
   `PVC.spec.volumeName` (= PV name) → Longhorn `Volume` CR of the same name in `longhorn-system`.

**Action — the proven runbook, per stuck workload:**

1. Resolve the owning controller (Deployment) from the pod's ownerReferences.
2. Record the controller's current `spec.replicas` in an annotation
   (`mount-healer.vollminlab.com/original-replicas`) **before** scaling.
3. Scale the Deployment to **0**.
4. **Poll** `kubectl get volumes.longhorn.io <vol> -n longhorn-system -o jsonpath='{.status.state}'`
   until it reads `detached`, up to a bounded timeout (e.g. 3 min).
5. Scale the Deployment back to the recorded original replica count.
6. Emit a Kubernetes `Event` (`reason: HealedStaleMount`) on the Deployment and a healer log line.

**Safety properties (load-bearing):**

- **Namespace allowlist** — only acts in the namespaces that actually hold Longhorn RWO app volumes
  (`mediastack`, `monitoring`, `harbor`). Never touches arbitrary workloads. The allowlist is an
  explicit script constant, reviewed in PR.
- **One workload per run** — at most one Deployment is healed per invocation; any other stuck
  workloads wait for the next tick. This is the thundering-herd brake the descheduler approach
  lacked.
- **Per-workload cooldown** — an annotation timestamp (`mount-healer.vollminlab.com/last-healed`)
  suppresses re-healing the same Deployment within a cooldown window (e.g. 6h). A workload that
  crashloops *again* after a heal is a genuine application bug, not a stale mount: the healer leaves
  it alone and Layer 3 pages a human.
- **Crash-safe restore (idempotency)** — at the **start** of every run, before any detection, the
  healer first scans the allowlist for any Deployment carrying
  `mount-healer.vollminlab.com/original-replicas` while sitting at `spec.replicas: 0`, and restores
  it. So a healer job pod that dies between step 3 and step 5 cannot leave a workload parked at zero;
  the next tick repairs it.
- **Detach-timeout fallback** — if the volume never reaches `detached` within the timeout, the
  healer **still restores** the original replicas (never silently parks a workload at 0) and emits a
  `HealedStaleMountTimeout` Event so the alert fires. Down-and-alerting beats down-and-silent.
- **Deployments only** — targets the arr/monitoring pattern (RWO + `strategy: Recreate`, replicas 1).
  StatefulSets are explicitly skipped in v1.

**RBAC** (ServiceAccount in `kube-system` + ClusterRole + ClusterRoleBinding):
`get`/`list` on `pods`, `persistentvolumeclaims`, `persistentvolumes`, `events`;
`get`/`list`/`patch` on `apps/deployments` and `apps/deployments/scale`;
`create` on `events`; `get`/`list` on `volumes.longhorn.io`.

**Why this heals where eviction can't:** every code path performs the explicit *poll-until-detached*
wait before bringing the workload back. That wait is the one ingredient the manual runbook has and
eviction lacks, and it is what clears the errored/busy mount.

### Layer 2 — Data-locality nudge (frequency reduction) — DECLARATIVE ONLY, zero churn

Set Longhorn's `default-data-locality` to `best-effort` so **new and recreated** volumes keep a
replica co-located with the pod that mounts them. A local replica means a remote-node blip is far
less likely to be the replica the pod is actively reading, shrinking the most common trigger for the
stale mount.

**Scope is deliberately limited to declarative-only:**

- Change the global default and/or the StorageClass `dataLocality` parameter so volumes created going
  forward get a local replica.
- **Do not** patch `dataLocality` on existing volumes in this change. That triggers background replica
  rebuilds and must be gated behind a Longhorn capacity check (per `storage.md`). It's a deferred
  operational step, not part of this work.

### Layer 3 — Visibility (symptom-based) — so self-healing isn't silent

Add a `PrometheusRule` (in `monitoring`) that alerts on the **symptom**, using the already-scraped
kube-state-metrics series — no dependency on the descheduler's unscrapeable CronJob metrics:

- Alert on `kube_pod_container_status_restarts_total` increasing rapidly over a window (a pod
  sustaining restarts), at warning severity, routed like other cluster alerts.
- Combined with the healer's `HealedStaleMount` / `HealedStaleMountTimeout` Events, this gives both
  signals: *something got stuck* (the alert) and *it got healed* (the Event). If the alert keeps
  firing despite the healer running, the healer isn't coping (e.g. cooldown suppressed a genuine bug,
  or detach is timing out) → a human investigates the underlying node pressure.

## Components changed

| Component | File(s) | Change |
|-----------|---------|--------|
| Healer app | new `kube-system/longhorn-mount-healer/app/{cronjob,rbac,configmap,kustomization}.yaml` | CronJob (`*/10`), ServiceAccount + ClusterRole/Binding, script ConfigMap, kustomization |
| Flux wiring | `flux-system/flux-kustomizations/{longhorn-mount-healer-kustomization.yaml, kustomization.yaml}` | New Flux Kustomization CR + add to the resources index. **No HelmRepository** — it is not a chart, so the second (repositories) index does not apply |
| Longhorn settings | `longhorn-system/longhorn/app/configmap.yaml` | Set `default-data-locality: best-effort` (new/recreated volumes only) |
| StorageClass(es) | `clusterwide/storageclass-longhorn*.yaml` | Add/confirm `dataLocality: best-effort` parameter where appropriate |
| Alerting | new `PrometheusRule` (monitoring) | Alert on `kube_pod_container_status_restarts_total` |

## PR scoping

Layer 1 (the cure) is its own concern and stands alone. Per the one-concern-per-PR rule, the proposed
split is:

1. **PR 1 — Layer 1 healer** (primary; lands the cure).
2. **PR 2 — Layers 2 + 3** (frequency nudge + symptom alert), as a follow-up.

(If review prefers a single PR for all three, that is acceptable — they are one coherent resiliency
concern — but the default is to land the cure first.)

## Verification

After Flux reconciles **PR 1**:

- `kubectl get cronjob longhorn-mount-healer -n kube-system` shows the `*/10` schedule and recent
  successful jobs.
- A healer job log in steady state (no stuck pods) reports "no stuck workloads" and scales nothing.
- RBAC: the ServiceAccount can `get` Longhorn volumes and `patch` deployment scale
  (`kubectl auth can-i --as=system:serviceaccount:kube-system:longhorn-mount-healer ...`).
- **End-to-end (controlled):** induce a stale-mount-like crashloop on a throwaway allowlisted
  Deployment (or replay against radarr in a maintenance window), confirm the healer scales it to 0,
  waits for `detached`, scales it back, and emits `HealedStaleMount`. Confirm the crash-safe-restore
  path by killing a healer job mid-run and verifying the next tick restores replicas.

After **PR 2**:

- `default-data-locality` reads `best-effort` in Longhorn settings; existing volumes are
  **unchanged** (no rebuilds kicked off — confirm via Longhorn UI/volume list).
- New `PrometheusRule` is loaded (`kubectl get prometheusrule -n monitoring`); the
  `kube_pod_container_status_restarts_total` expression resolves in Prometheus/VictoriaMetrics.

## Rollout & rollback

- Rollback of Layer 1 is deleting the app dir + its Flux Kustomization (the healer simply stops
  running; nothing it changed persists beyond annotations, which are inert).
- A healer mid-run at rollback time is covered by the crash-safe-restore invariant only while the
  CronJob still exists — so rollback should confirm no Deployment is parked at 0 with the
  `original-replicas` annotation before deleting the app.
- Layer 2's `default-data-locality` change only affects future volumes, so reverting it is harmless.
- No data risk at any point — the healer performs the same safe detach/reattach as the manual
  recovery, and Layer 2 never touches existing volumes.

## Open questions / to confirm during implementation

1. **Pod → Deployment resolution** for the wedged-`ContainerCreating` case: a pod stuck pre-start
   still has ownerReferences (ReplicaSet → Deployment), so resolution holds; confirm against a live
   wedged pod.
2. **jq availability** in `alpine/kubectl:1.33.4` — if absent, either `apk add jq` in-script or rely
   on `kubectl -o jsonpath`/`-o go-template` only.
3. **StatefulSet coverage** — none of the motivating apps are StatefulSets, but Longhorn-backed
   StatefulSets exist elsewhere (e.g. CNPG). Out of scope for v1; revisit if a StatefulSet ever
   exhibits the same stale-mount pattern.
4. **Whether `default-data-locality` belongs at the global Longhorn setting, the StorageClass
   parameter, or both** — confirm against the current `longhorn` configmap and StorageClass
   definitions when building PR 2.
