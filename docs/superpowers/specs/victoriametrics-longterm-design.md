# VictoriaMetrics Long-Term Metrics — Design

**Created:** 2026-05-30 (architecture decided 2026-05-28)
**Finalized:** 2026-05-31 (open questions resolved in brainstorm)
**Status:** Finalized — ready for implementation plan.
**Sequence:** ESO / 1Password Connect is already complete (PRs #818–#830, sealed-secrets
controller removed 2026-05-31), so the B2-via-ESO credential path this design relies on already
exists.

## Problem

Prometheus retention was cut **15GB → 3GB / 7d** (PR #782) to stop the TSDB OOM-growing on the
memory-constrained worker nodes. That stopped the crashes but throws away long-term metrics
history. We want months of history back without buying hardware or consuming shared VMware
datastore space.

The cluster has no spare RAM headroom (see the worker02 memory-pressure work / the N+1 memory
alert sitting ~1.9 GiB above its floor), so the fix must *reduce* the metrics stack's footprint,
not grow it.

## Goal

Run **Prometheus and VictoriaMetrics together** so Prometheus keeps doing everything it does
today while VictoriaMetrics owns long-term, compressed storage — net-lighter on memory than
today, with a cheap B2 archive.

## Architecture

```
targets → [Prometheus: scrape + alert + eval PrometheusRules, 24h local]
                 │ remote_write (continuous)
                 ▼
         [VictoriaMetrics single: 90d, 30Gi/2-replica Longhorn, :8428 PromQL]
                 │                         │ PVC (RWO)
                 │ PromQL                  ▼ Velero node-agent (Kopia FSB), daily
         [Grafana dashboards]        [Backblaze B2 (existing Velero BSL) ← ttl retention]
         [Alertmanager ← fired by Prometheus, not VM]
```

- **Prometheus stays the scraper/alerter.** All scraping, service discovery, alerting,
  recording rules, ServiceMonitors, and existing dashboards remain unchanged. Local retention
  drops to **24h** (enough for alerting + rule evaluation). Prometheus is **not** redundant — it
  remains the sole scraper and the entire alert/recording-rule evaluation path. VictoriaMetrics
  is a dumb PromQL storage backend that never scrapes or evaluates rules.
- **Prometheus `remote_write` → in-cluster single-node VictoriaMetrics.** VM holds 90 days at
  ~3× better on-disk efficiency than Prometheus and speaks **native PromQL**, so every existing
  dashboard, alert, and `PrometheusRule` (including the vSphere N+1 alerts) keeps working
  against it.
- **Velero → Backblaze B2** for the long-term archive. A dedicated Velero `Schedule` scoped to
  just the VM PVC (via `includedNamespaces: [monitoring]` + a `labelSelector` on the VM pod)
  overrides the blanket `monitoring` exclusion on the existing schedules, and backs the volume up
  daily to the **existing Velero B2 BSL** using the already-running node-agent (Kopia FSB).
  Retention is the Schedule's `ttl`. This needs **no new B2 bucket, 1Password item, or
  ExternalSecret** — it reuses the backup platform of record. Chosen over a `vmbackup` sidecar
  because the sidecar would add an always-on container (counter to the memory goal) and a second
  restore path; `vmbackupmanager`'s scheduler is Enterprise-only with no permanent free license.
  Crash-consistent FSB is acceptable: VM recovers from crash-consistent on-disk state, and the
  live store is already protected by 2 Longhorn replicas + continuous remote_write.
- **Grafana** reads history from VictoriaMetrics. VM holds near-real-time data (via continuous
  remote_write) *and* full history — a strict superset of Prometheus's local 24h — so a single
  VM datasource serves dashboards for any time range.
- **In-cluster, not a dedicated VM.** A separate VictoriaMetrics VM was considered and rejected:
  VMware HA requires shared storage (defeating the goal of offloading shared-datastore
  pressure), and a local-disk VM would be pinned to one ESXi host with no HA. In-cluster is
  *lighter* overall and Longhorn provides the HA. No separate VM to manage.
- **VictoriaMetrics single-node, not Thanos.** Thanos's multi-component overhead isn't justified
  at homelab scale; VM single-node is the right fit.

### Memory math (the justification)

| Component | Current | With VictoriaMetrics |
|-----------|---------|----------------------|
| Prometheus | 2400Mi | ~400Mi (24h retention only) |
| VictoriaMetrics | — | ~400Mi (90d, ~3× compression) |
| **Total** | **2400Mi** | **~800Mi** |

Nets **~1.6GB back** on a worker node *and* restores long-term retention. (Figures are the
2026-05-28 estimates; re-measure live TSDB size + ingest rate once VM is running to right-size
VM's PVC and resources — the Prometheus image has no shell, so this could not be measured during
planning.)

## Decisions (settled in brainstorm 2026-05-31 — do not re-litigate)

| Question | Decision | Rationale |
|----------|----------|-----------|
| **Chart / deployment shape** | `victoria-metrics-single` Helm chart (no Operator, no CRDs). Backups handled externally by Velero, not the chart's enterprise sidecar. | The Operator's main value (turning ServiceMonitors into scrape configs) is redundant — Prometheus stays the scraper. Single chart matches the existing HelmRelease pattern and avoids a controller pod. |
| **remote_write topology** | Prometheus `remote_write` → VM. No `vmagent`. | Prometheus remains the only scraper; VM is a pure storage sink. |
| **Grafana datasources** | VM owns `uid: prometheus` (display name "VictoriaMetrics", `isDefault: true`). Real Prometheus added as "Prometheus (live)" `uid: prometheus-live`. Loki unchanged. | Existing dashboards pin `uid: prometheus` (e.g. 84 panels in the MinIO dashboard). Pointing that UID at VM gives every dashboard full history with **zero edits**. The secondary datasource is a debugging escape hatch for inspecting Prometheus's freshest scrape. |
| **PVC sizing + StorageClass** | 30Gi PVC, **2 Longhorn replicas**, 90d VM retention. Longhorn online-resize if ingest grows. | Live capacity measured 2026-05-31: general workers have 53–155Gi free, so 2×30Gi fits easily. 2 replicas balances HA against the tight cluster, justified because the store is also protected by remote_write replay + layered B2 backups. |
| **Backup to B2** | Dedicated Velero `Schedule` (daily) scoped to the VM PVC → existing Velero B2 BSL, retention via `ttl` (~90d). No new bucket/credentials. | Reuses the backup platform of record and its already-running node-agent (zero new always-on footprint — decisive under the memory goal), with one restore runbook. A `vmbackup` sidecar was rejected (always-on container, second restore path) and `vmbackupmanager` is Enterprise-only with no permanent free license. Crash-consistent FSB is fine for VM; the live store also has 2 Longhorn replicas + remote_write. |
| **Migration / cutover** | Two-stage: (1) deploy VM + remote_write + datasources with Prometheus still at 7d; verify ingestion; (2) drop Prometheus to 24h. No backfill of the existing 7d. | Avoids a history gap during transition. `remote_write` only forwards new samples; the old 7d ages out naturally. VM history begins at cutover — acceptable, since the goal is restoring *future* long-term retention. |
| **Resource requests** | VM start: requests `cpu 100m / mem 256Mi`, limits `cpu 1000m / mem 1Gi`. | The ~400Mi figure is an estimate; re-tune after live TSDB measurement once VM is ingesting. |

### Longhorn capacity (measured 2026-05-31)

| Node | Available | Node | Available |
|------|-----------|------|-----------|
| k8sworker01 | 155 Gi | k8sworker04 | 75 Gi |
| k8sworker02 | 64 Gi | k8sworker05 (DMZ) | 126 Gi |
| k8sworker03 | 53 Gi | k8sworker06 (DMZ) | 125 Gi |

### Backup (Velero)

A dedicated Velero `Schedule` (`victoria-metrics-b2`) backs up only the VM PVC to the existing
Velero **B2** BackupStorageLocation daily, with `defaultVolumesToFsBackup: true` so the
node-agent (Kopia) captures the RWO Longhorn volume. It is scoped with
`includedNamespaces: [monitoring]` plus a `labelSelector` matching the VM pod labels
(`app: victoria-metrics`) so it captures *only* the VM volume and not the (deliberately excluded)
Prometheus/Loki volumes. Retention is the Schedule `ttl` (~90 days = 2160h, matching the existing
`daily-b2`). **No new B2 bucket, 1Password item, or ExternalSecret** — it reuses Velero's existing
B2 credentials and node-agent. Restore uses the standard Velero workflow (`docs/runbooks` + the
velero rules).

## Rejected alternatives

- **Replacing Prometheus** — rejected; they run together (Prometheus owns scrape + alert).
- **Thanos / Mimir / Cortex** — rejected; multi-component overhead not justified at homelab scale.
- **ClickHouse** — rejected. No native PromQL (would break ~25 dashboards + every PrometheusRule,
  or require a translation layer) and a heavier RAM/Keeper footprint — counter to the two goals
  here (PromQL drop-in + reduce memory). ClickHouse fits a from-scratch SQL-native unified
  telemetry platform (SigNoz model), which is a different project. It is also not part of the
  VictoriaMetrics stack (VM and VictoriaLogs use their own engines).

## Future phase (agreed direction, separate spec)

- **VictoriaLogs for Loki:** same vendor and pattern — Promtail → VictoriaLogs, Grafana queries
  it, extending log retention from the current 30d toward ~1 year at far better efficiency.
  **Deliberately decomposed out of this work**: the memory-pressure driver does not apply to logs
  (Loki already stores chunks in MinIO/S3, not constrained local RAM), and the cutover and open
  questions (ingestion path, whether Loki stays or is replaced, LogsQL vs LogQL) are distinct.
  Sequence after the metrics side is stable; it will reuse the operational pattern established
  here.

## Out of scope

- VictoriaLogs (future phase, separate spec — see above).
- Backfilling Prometheus's existing 7d into VM (possible later via `vmctl` import of a
  `promtool tsdb dump`, but not part of this work).
