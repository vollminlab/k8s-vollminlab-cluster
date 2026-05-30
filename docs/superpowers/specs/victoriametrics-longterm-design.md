# VictoriaMetrics Long-Term Metrics — Design

**Created:** 2026-05-30 (architecture decided 2026-05-28)
**Status:** Draft — architecture agreed in principle, not yet implemented. Needs a focused brainstorm pass on the open questions below, then a `writing-plans` implementation plan.
**Sequence:** After the ESO / 1Password Connect migration (`docs/superpowers/plans/1password-eso.md`). VictoriaMetrics' `vmbackup` needs B2 credentials, which should be ESO-managed rather than added as new SealedSecrets.

> This spec was reconstructed from the 2026-05-28 design conversation, which was agreed but never written down. Treat the **Decided** section as settled and the **Open questions** section as the remaining brainstorm scope.

## Problem

Prometheus retention was cut **15GB → 3GB / 7d** (PR #782) to stop the TSDB OOM-growing on the (then 8GB, now 12GB) worker nodes. That stops the crashes but throws away long-term metrics history. We want months of history back without buying hardware or consuming shared VMware datastore space.

The cluster has no spare RAM headroom (see `worker02-scheduling-rebalance` / the N+1 memory alert sitting ~1.9 GiB above its floor), so the fix must *reduce* the metrics stack's footprint, not grow it.

## Goal

Run **Prometheus and VictoriaMetrics together** so Prometheus keeps doing everything it does today while VictoriaMetrics owns long-term, compressed storage — net-lighter on memory than today, with a cheap B2 archive.

## Decided architecture (settled 2026-05-28 — do not re-litigate)

- **Prometheus stays the scraper/alerter.** All scraping, alerting, recording rules, ServiceMonitors, and existing dashboards remain unchanged. Local retention drops to **24h** (enough for alerting + rule evaluation).
- **Prometheus `remote_write` → in-cluster single-node VictoriaMetrics.** VM holds 30–90 days at 5–10× better compression than Prometheus and speaks **native PromQL**, so every existing dashboard, alert, and `PrometheusRule` (including the new vSphere N+1 alerts) keeps working against it.
- **`vmbackup` → Backblaze B2** for long-term archive. B2 is already configured in-cluster; given VM's compression this is pennies/month.
- **Grafana** queries VictoriaMetrics for history and Prometheus for the last 24h (datasource strategy is an open question — see below).
- **In-cluster, not a dedicated VM.** A separate VictoriaMetrics VM was considered and rejected: VMware HA requires shared storage (defeating the goal of offloading shared-datastore pressure), and a local-disk VM would be pinned to one ESXi host with no HA. In-cluster is *lighter* overall and Longhorn provides the HA. No separate VM to manage.
- **VictoriaMetrics single-node, not Thanos.** Thanos's multi-component overhead isn't justified at homelab scale; VM single-node is the right fit.

### Memory math (the justification)

| Component | Current | With VictoriaMetrics |
|-----------|---------|----------------------|
| Prometheus | 2400Mi | ~400Mi (24h retention only) |
| VictoriaMetrics | — | ~400Mi (30+ days, ~10× compression) |
| **Total** | **2400Mi** | **~800Mi** |

Nets **~1.6GB back** on a worker node *and* restores long-term retention. (Figures are the 2026-05-28 estimates; re-measure live TSDB size + ingest rate at planning time to size VM's PVC and resources.)

## Future phase (agreed direction, not part of the first build)

- **VictoriaLogs for Loki:** same pattern — Promtail `remote_write` → VictoriaLogs, Grafana queries it, extending log retention from the current 30d toward ~1 year at far better efficiency. Sequence after the metrics side is stable.

## Open questions (the brainstorm scope before writing the plan)

1. **Chart/deployment shape:** `victoria-metrics-single` Helm chart vs the VictoriaMetrics Operator (`vmsingle`/`vmagent` CRDs). Operator integrates with existing ServiceMonitors but adds CRDs/controller; single chart is simpler. Decide.
2. **remote_write topology:** does Prometheus remote_write to VM, or do we introduce `vmagent` to scrape and feed both? (Decided architecture implies Prom-remote_write; confirm.)
3. **Grafana datasources:** single VM datasource for everything (simplest, since VM can also serve recent data), or keep Prom (24h) + VM (history) as two datasources? Affects dashboard variables.
4. **PVC sizing + StorageClass:** how big, and Longhorn replica count (this interacts directly with the Longhorn capacity rules and the cluster's tight storage — size conservatively, see `.claude/rules/storage.md`).
5. **vmbackup cadence + B2 bucket/credential:** schedule, retention on B2, and the ESO-managed B2 key (depends on ESO landing first).
6. **Migration/cutover:** how to drop Prometheus retention to 24h without a history gap during the transition; backfill considerations.
7. **Resource requests:** real numbers from live TSDB measurement at planning time (the ~400Mi figures are estimates).

## Out of scope

- Replacing Prometheus (explicitly rejected — they run together).
- Thanos/Mimir/Cortex (rejected — overhead not worth it here).
- VictoriaLogs (future phase, separate spec).
