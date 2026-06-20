# Storage-Induced Crashloop Resiliency — Design

**Status:** Draft for review
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
is never cleared. The only thing that clears it is a full detach/reattach cycle — which requires the
pod object to be *deleted and rescheduled* (`scale 0 → wait detached → scale 1`, or an eviction).
Kubernetes' built-in restart loop deletes the *container*, never the *pod*, so it can never escape.

On 2026-06-20 this left radarr and sonarr crashlooping for **2+ days** (36–38 restarts each) until a
human ran the manual scale-0→1 recovery. That manual step is the gap this design closes.

This is **not** a node-specific problem and the fix must not be node-specific. Node memory pressure
is *a* trigger, but the resiliency requirement is that the cluster heals itself from a stuck pod
**regardless of which app, which node, or what triggered the stale mount**. The cure must be
app-agnostic and node-agnostic.

## Goal

Make the cluster self-heal from storage-induced crashloops with no human intervention, and make that
self-healing observable. Concretely:

1. **Resiliency (primary):** any pod stuck in CrashLoopBackOff past a threshold is automatically
   evicted, which forces the Longhorn detach/reattach that clears the stale mount. This is the cure
   and it heals *every* variant of the problem — every app, every node, every trigger.
2. **Visibility:** when the self-healing fires, an alert tells us it happened, so a recurring
   storage problem can't hide behind a system that silently papers over it.

Frequency reduction (making the stale mount happen less often) is explicitly **secondary** and kept
to a zero-risk declarative nudge — see Layer 2. The node RAM rework tracked separately
(`project_worker02_memory_pressure`) remains the real frequency fix and is out of scope here.

## Non-goals

- Fixing the node memory/IO pressure that triggers the blips (separate RAM-rework effort).
- Patching `dataLocality` on existing volumes (triggers background replica rebuilds; deferred to a
  controlled operational step, not part of this change).
- Any app-specific or node-specific remediation.

## Approach

Reuse the **descheduler** already deployed in `kube-system` (CronJob, chart 0.36.0 /
descheduler v0.33). It already runs every 30 minutes with a `DefaultEvictor` + `LowNodeUtilization`
balance profile. We add one deschedule plugin — `RemovePodsHavingTooManyRestarts` — that evicts
pods stuck restarting. Eviction deletes the pod object, which is exactly the trigger Longhorn needs
to detach and reattach the volume on reschedule, clearing the stale mount.

This was chosen over a custom controller or per-app liveness-probe hacks because the mechanism
already exists, is maintained upstream, is app/node-agnostic by construction, and needs only a
configmap change.

### Layer 1 — Self-healing eviction (the cure) — PRIMARY

Edit `clusters/vollminlab-cluster/kube-system/descheduler/app/configmap.yaml`:

- Add a `RemovePodsHavingTooManyRestarts` plugin to the `default` profile:
  - `podRestartThreshold: 10` — a pod that has restarted 10+ times is unambiguously stuck, not
    transiently flapping. At ~5 min max CrashLoopBackOff backoff, 10 restarts ≈ under an hour of
    crashing before the cluster heals it — versus the 2+ days it took by hand.
  - `includingInitContainers: true` — count init-container restarts too.
  - **`states: ["CrashLoopBackOff"]`** — only evict pods whose container is *currently* in
    CrashLoopBackOff. This is the critical safety scope: without it the plugin evicts any pod whose
    *cumulative* restart count exceeds the threshold, including a pod that crashed 11 times last week
    and has run healthy since. Scoping to the live CrashLoopBackOff state means we only ever evict a
    pod that is *right now* stuck. (Supported on descheduler v0.29+; this cluster is on v0.33.)
- Enable it under `plugins.deschedule.enabled`.
- Tighten the CronJob `schedule` from `*/30 * * * *` to `*/15 * * * *` so a stuck pod is healed
  within ~15 minutes of crossing the threshold instead of up to 30.

**Why eviction heals the mount:** the existing `DefaultEvictor` already has
`nodeFit: true` and `podProtections.defaultDisabled: ["PodsWithLocalStorage"]`. `nodeFit` only
evicts a pod that can be scheduled elsewhere; Longhorn RWO volumes can attach to any node, so this
always passes. Eviction → pod deleted → Longhorn detaches the volume → pod reschedules → volume
reattaches with a clean mount (fsck runs on attach). This is the same detach/reattach the manual
`scale 0 → 1` recovery performs, done automatically.

**Safety properties:**
- `DefaultEvictor` protects DaemonSet pods, static/mirror pods, and pods with
  `system-cluster-critical`/`system-node-critical` priority by default — so the descheduler can't
  evict its way into a cluster outage.
- `states: ["CrashLoopBackOff"]` means healthy pods are never touched, no matter their history.
- Worst case for a *legitimately* crashlooping app (bad config, not a stale mount): the descheduler
  evicts it, it reschedules, and it crashloops again — i.e. eviction is a no-op for non-storage
  crashloops, not a harm. It neither fixes nor worsens a genuine application bug.

### Layer 2 — Data-locality nudge (frequency reduction) — DECLARATIVE ONLY, zero churn

Set Longhorn's `default-data-locality` to `best-effort` so **new and recreated** volumes keep a
replica co-located with the pod that mounts them. A local replica means a remote-node blip is far
less likely to be the replica the pod is actively reading, shrinking the most common trigger for the
stale mount.

**Scope is deliberately limited to declarative-only:**
- Change the global default and/or the StorageClass `dataLocality` parameter so volumes created
  going forward get a local replica.
- **Do not** patch `dataLocality` on existing volumes in this change. That triggers background
  replica rebuilds and must be gated behind a Longhorn capacity check (per `storage.md`). It's a
  deferred operational step, not part of this PR.

This layer is kept intentionally small so it never competes with or complicates Layer 1. If review
prefers to drop Layer 2 entirely and rely solely on Layer 1's cure, that is acceptable — Layer 1
heals the problem regardless of frequency.

### Layer 3 — Visibility (so self-healing isn't silent)

Add a `PrometheusRule` that alerts when the descheduler evicts pods, so automated healing surfaces a
recurring storage problem instead of masking it.

- Alert on the descheduler's evicted-pods metric (`descheduler_pods_evicted`) showing eviction
  activity over a window — info/warning severity, routed like other cluster alerts.
- Intent: "the cluster healed itself N times today" is a signal worth seeing. A spike means a node
  or volume is misbehaving and the underlying trigger (node pressure) needs attention even though
  users saw no outage.
- Verify the descheduler CronJob's metrics are actually scraped by Prometheus; if the CronJob job
  pods aren't currently a ServiceMonitor/scrape target, wire that up as part of this layer (else the
  metric won't exist). Confirm metric name and exposure during implementation.

## Components changed

| Component | File | Change |
|-----------|------|--------|
| Descheduler policy | `kube-system/descheduler/app/configmap.yaml` | Add `RemovePodsHavingTooManyRestarts` (threshold 10, `states: [CrashLoopBackOff]`, init containers); enable under `plugins.deschedule`; schedule `*/30`→`*/15` |
| Longhorn settings | `longhorn-system/longhorn/app/configmap.yaml` | Set `default-data-locality: best-effort` (new/recreated volumes only) |
| StorageClass(es) | `clusterwide/storageclass-longhorn*.yaml` | Add/confirm `dataLocality: best-effort` parameter where appropriate |
| Alerting | new `PrometheusRule` (monitoring) + scrape wiring if needed | Alert on `descheduler_pods_evicted`; ensure descheduler metrics are scraped |

## Verification

After Flux reconciles:
- Descheduler runs on the 15-min schedule; `kubectl get cronjob descheduler -n kube-system` shows
  the new schedule and recent successful jobs.
- Inspect a descheduler job log to confirm the `RemovePodsHavingTooManyRestarts` plugin loaded and
  that, in steady state with no stuck pods, it evicts nothing.
- Confirm no autogen/eviction of healthy or system-critical pods (descheduler logs list every
  eviction with a reason).
- `default-data-locality` reads `best-effort` in Longhorn settings; existing volumes are
  **unchanged** (no rebuilds kicked off).
- New `PrometheusRule` is loaded (`kubectl get prometheusrule -n monitoring`); the
  `descheduler_pods_evicted` metric resolves in Prometheus.

**End-to-end (optional, controlled):** intentionally induce a crashloop on a throwaway pod past the
threshold and confirm the descheduler evicts it within ~15 min and it reschedules clean. Not
required for merge; Flux reconciliation verification is async.

## Rollout & rollback

- Single PR, all four changes (they're one coherent concern: self-healing + its visibility +
  the zero-risk frequency nudge). Kyverno CI runs the same policies as in-cluster.
- Rollback is a config revert: removing the plugin / restoring `*/30` reverts Layer 1; the
  `default-data-locality` change only affects future volumes so reverting it is also harmless.
- No data risk at any point — eviction performs the same safe detach/reattach as the manual
  recovery, and Layer 2 never touches existing volumes.

## Open questions / to confirm during implementation

1. Exact descheduler metric name and whether its CronJob pods are already a Prometheus scrape
   target (Layer 3 depends on this).
2. Whether `default-data-locality` belongs at the global Longhorn setting, the StorageClass
   parameter, or both — confirm against current `longhorn` configmap and StorageClass definitions.
3. Whether to keep Layer 2 in this PR or split it out — reviewer's call; Layer 1 stands alone.
