# Plan: Relieve k8sworker02 memory/IO pressure (capacity + scheduling)

**Created:** 2026-05-30
**Status:** Draft — pending review

## Problem

k8sworker02 flaps `NotReady` under memory + storage-IO pressure, taking down every pod on it (authentik, harbor-db, jellyfin, sealed-secrets, etc.). Root cause and full evidence chain captured in memory `project_worker02_memory_pressure`: memory-pressure reclaim thrashing → containerd CRI stall → kubelet misses node-lease renewal → node marked NotReady → all pods restart. authentik "crashlooping" was collateral, never an authentik bug.

## Key finding: this is a capacity problem, not a distribution problem

The general worker pool (worker01–04, 4 CPU / 7.8 GiB each) is request-saturated across the board:

| Node | Mem **requests** | Mem **actual** | CPU requests |
|------|------------------|----------------|--------------|
| worker01 | 82% | 69% | 79% |
| worker02 | 82% | 85% | 88% |
| worker03 | 74% | 53% | 68% |
| worker04 | 73% | 49% | 79% |

**No node has schedulable slack** (all 73–82% mem requests). The kube-scheduler and descheduler bind on *requests*, so there is nowhere to move worker02's tenants. This is why the already-deployed descheduler isn't helping (see below). A scheduling-only change therefore **cannot** relieve the pressure — added/freed request capacity is required first.

Note the gap between requests and actual: worker03 (74% req / 53% actual) and worker04 (73% / 49%) host pods that **over-request memory by ~1.5–2 GiB each**. That inflation is real schedulable headroom locked up by bad requests.

## Why the existing descheduler can't rebalance

`clusters/vollminlab-cluster/kube-system/descheduler/app/` — chart **0.34.0**, runs as a `CronJob` every 30 min.
- `LowNodeUtilization`: `thresholds {cpu:40, memory:50, pods:20}`, `targetThresholds {cpu:70, memory:75, pods:50}`. Uses **requests**, not actual metrics.
- `DefaultEvictor`: `nodeFit: true` — only evicts a pod if it would fit on another node by requests.
- With every general node at 73–82% mem requests, there is **no node below the 50% recipient threshold** and **nodeFit blocks every candidate move**. Result: descheduler is a no-op for this imbalance.
- Changing to metrics-based selection (`metricsUtilization`) would not help: `nodeFit` still checks *requests* for placement, and there's no request slack anywhere. Disabling `nodeFit` would just create `Pending` pods.

## The fix (two levers — do both)

### Lever 1 (primary, durable): RAM rework via VMware — net-neutral on the HV

HV constraint: 3 hosts × 96 GiB = 288 GiB; 205 GiB used, 81 GiB free. Already **past N+1** (surviving 2 hosts = 192 GiB < 205 GiB used → ~13 GiB can't fail over today). So capacity moves must be net-neutral, not net-add.

DMZ nodes are massively over-provisioned: worker05 (32 GiB, minecraft uses 5.2 GiB / 8 GiB limit + masters-league, ~8.6 GiB total), worker06 (32 GiB, ~2.9 GiB used).

Plan (each step = a manual VMware power-cycle; RAM can't be hot-removed — see memory `feedback_vmware_power_cycle`):
1. Shrink worker05 32 → 16 GiB and worker06 32 → 16 GiB → **frees 32 GiB HV**. 16 GiB is ample for 4–5 minecraft players (Java capped at 8 GiB limit + ~2 GiB system on a 16 GiB node). worker06 could even go to 12 GiB for extra cushion.
2. Bump worker01–04 8 → 12 GiB → **costs 16 GiB**. At 12 GiB (~11.7 usable), the 73–82% requests drop to ~50–55% → real schedulable slack, descheduler can then balance, pressure gone.
3. Net HV: −32 + 16 = **+16 GiB free → 81 → 97 GiB**, finally above N+1.

### Lever 2 (complementary, zero-HV): right-size inflated memory requests

Goldilocks/VPA is already running. Trim the over-requesting pods on worker03/04 (req exceeds actual by ~1.5–2 GiB) so the scheduler stops treating those nodes as full. This frees schedulable slack *now*, before the VMware work, and improves bin-packing after. Per-app, PR-based; use goldilocks recommendations as the source of truth, apply conservatively.

## After capacity exists: optional scheduling hardening

Once nodes have slack (post-Lever-1), add soft `topologySpreadConstraints` (`whenUnsatisfiable: ScheduleAnyway`, key `kubernetes.io/hostname`) to the heaviest tenants that lack them — Prometheus, Loki, authentik-server — so they don't re-pack onto one node. Mediastack apps already have spread constraints (added in PR #790). This is hardening, not relief; it only acts at (re)schedule time.

## Separate track: Longhorn IO

PSI io `full avg60=31%` on worker02 indicates heavy Longhorn replica IO. Check whether worker02 hosts a disproportionate replica share and rebalance replicas off the hot node via Longhorn. Independent of the memory work.

## Cautions

- GitOps: changes under `clusters/` auto-reconcile from `main` within 10 min — never `kubectl apply` manually (`.claude/rules/flux.md`). Branch + PR required; never push to `main`; never merge without explicit user approval.
- Preserve required labels (`app`, `env: production`, `category`) on every edited resource.
- Do not pursue a descheduler `metricsUtilization` change as immediate relief — it is ineffective until request slack exists (documented above).
