# Longhorn Engine Image Upgrade Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring all 48 Longhorn volumes onto the default engine image (v1.12.1), which drains the duplicate instance-managers and returns ~480m of CPU request per node.

**Date:** Drafted 2026-09-07.

---

## The problem

`concurrent-automatic-engine-upgrade-per-node-limit` is **0**, which disables automatic engine
upgrades. It is Longhorn's shipped default, so this was never a decision — it simply was never
turned on. As a result **45 of 48 volumes run an engine image older than the default**, some by
four minor versions:

| engine | attached | detached | total |
|---|---|---|---|
| v1.8.1 | 7 | 0 | **7** |
| v1.11.1 | 13 | 0 | 13 |
| v1.11.2 | 9 | 13 | 22 |
| v1.12.0 | 3 | 0 | 3 |
| **v1.12.1 (default)** | 2 | 1 | **3** |

### Why it costs CPU

`guaranteed-instance-manager-cpu` is `{"v1":"12","v2":"31"}` — a **percentage of node CPU**, not
millicores. 12% of a 4-core worker is **480m**. Since the 2026-08-17 Longhorn 1.12.1 upgrade every
node runs **two** instance-managers, old image and new, each reserving 480m:

    imi-171634ab  created 2026-08-08   OLD
    imi-663c32a8  created 2026-08-17   NEW

Longhorn cannot garbage-collect the old one while instances still live on it, and instances stay
because their volumes are pinned to old engine images. So **960m/node is committed** while the old
manager on worker03/04 measures 13-24m of actual use.

That matters because worker03/04 sit at **93-94% CPU requests committed against ~18% real
utilisation**. The scheduler places on requests, so those nodes read as full and everything piles
onto worker01-04 while worker05/06 idle at 30-58%.

### Restarting workloads does NOT fix it

Verified 2026-09-07: restarting `sabnzbd` and `bazarr` did **not** recreate their engine CRs
(still dated 2025-05-31 and 2025-10-05). A pod restart moves the *instance* between managers only
if placement happens to change — sabnzbd migrated because its pod moved node, bazarr did not
because it stayed put. The engine's `spec.image` is unchanged either way. Do not plan around
restarts.

### The correlation worth noticing

The seven v1.8.1 volumes are `minio`, `shlink-db`, `radarr`, `sonarr`, `bazarr`, `sabnzbd`,
`prowlarr` — very nearly the exact set behind this month's incidents: radarr's stale mount
(09-07), minio's single-replica scare (09-07), shlink's failed backups (09-05), and the long-lived
engines implicated in the stuck-rebuild deadlocks. A four-minor-old engine is not proven to be the
cause, but it is a common factor and worth holding in mind.

---

## Constraints that shape the sequencing

- **Live upgrade is documented as supported from v1.11.x to v1.12.1.** Longhorn's docs make no
  such statement for v1.8.1, so the seven v1.8.1 volumes must be treated as **offline** upgrades.
- **Detached volumes upgrade offline and instantly**, with no I/O impact at all.
- Automatic upgrade only live-upgrades volumes that are **attached and healthy**; it skips DR
  volumes.
- Applies to **V1 data engine only**. All volumes here are `longhorn.io/data-engine: v1`.
- **Never run any stage inside a backup window.** After #1257: CNPG 20:00-21:00, etcd-defrag
  22:00, VolSync 00:00-02:20, Velero 03:00-05:00. The safe windows are roughly **06:00-19:00**.

---

## Stage 1 — the 13 detached volumes (no downtime, do first)

Zero risk and it proves the mechanism before anything attached is touched.

- [ ] List them: `kubectl get volumes.longhorn.io -n longhorn-system -o json | jq -r '.items[] | select(.status.state!="attached") | "\(.metadata.name) \(.status.currentImage)"'`
- [ ] Upgrade each by patching the volume's desired image:
      `kubectl patch volumes.longhorn.io -n longhorn-system <vol> --type=merge -p '{"spec":{"image":"docker.io/longhornio/longhorn-engine:v1.12.1"}}'`
- [ ] Verify each reports `status.currentImage` = v1.12.1
- [ ] Confirm no volume changed `state` or `robustness`

## Stage 2 — the 22 attached v1.11.x volumes (live upgrade, supported path)

- [ ] Pick a window in 06:00-19:00 with no backup running and someone watching
- [ ] Set the limit to **1**, not the documented recommendation of 3 — this cluster's datastore is
      the source of its instability and one live upgrade at a time is the gentlest form:
      `kubectl patch settings.longhorn.io -n longhorn-system concurrent-automatic-engine-upgrade-per-node-limit --type=merge -p '{"value":"1"}'`
- [ ] Watch: volume count on v1.12.1 rising, `robustness` staying healthy, no node write-latency
      spike, no `LonghornVolumeDegraded`
- [ ] **Expect replica count to double transiently** — that is how live upgrade works, and on this
      storage it is the riskiest moment. Abort by setting the value back to `0` if anything degrades
- [ ] Once v1.11.x is drained, consider raising to 2-3 for the remainder

## Stage 3 — the 7 v1.8.1 volumes (offline, real downtime, one at a time)

Live upgrade from v1.8.1 is not a supported path. Each needs its workload stopped.

Order deliberately from least to most consequential:

- [ ] `mediastack/pvc-prowlarr-config` — single pod
- [ ] `mediastack/pvc-bazarr-config` — single pod
- [ ] `mediastack/pvc-sabnzbd-config` — single pod
- [ ] `mediastack/pvc-radarr-config` — single pod
- [ ] `mediastack/pvc-sonarr-config` — single pod
- [ ] `shlink/shlink-db-1` — CNPG; use a switchover if instances > 1, otherwise accept the outage
- [ ] `minio/minio` — **last**. It is the backup store; everything else depends on it being
      readable, and a failed upgrade here is the worst case in the cluster

For each: scale the workload to 0, confirm the volume reaches `detached`, patch `spec.image`,
confirm `currentImage` is v1.12.1, scale back to 1, confirm the app is healthy and the volume
`healthy`/`attached`.

## Stage 4 — reclaim the CPU

- [ ] Confirm one instance-manager image per node:
      `kubectl get pods -n longhorn-system -l longhorn.io/component=instance-manager -o custom-columns='NAME:.metadata.name,NODE:.metadata.labels.longhorn\.io/node,IMG:.metadata.labels.longhorn\.io/instance-manager-image'`
- [ ] Confirm CPU requests on worker03/04 dropped by ~480m each
- [ ] Re-check whether mediastack still concentrates on worker01-04, or whether the scheduler now
      spreads onto worker05/06

## Stage 5 — stop it recurring

- [ ] Leave `concurrent-automatic-engine-upgrade-per-node-limit` at a non-zero value so future
      Longhorn upgrades do not silently strand volumes again. This whole problem is the cost of
      that setting sitting at its default for four minor releases.
- [ ] Consider an alert on `count(count by (image) (longhorn_volume_actual_size_bytes))` — or any
      metric exposing engine image — being greater than 1 for more than a week.

---

## What this does not fix

The underlying storage. All of it still lands on a two-disk consumer SSD mirror at 64%
fragmentation with 42-216ms write latency. This removes a self-inflicted CPU reservation and gets
volumes onto a supported engine; it does not make the disks faster.
