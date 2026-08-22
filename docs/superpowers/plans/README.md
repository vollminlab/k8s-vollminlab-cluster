# Retired implementation plans

Thirty implementation plans lived in this directory. Twenty-nine described work that has
shipped; they were execution scaffolding, not reference material, and were removed on 2026-08-22.
**`foundry-vtt.md` remains** because that work is still in progress.

## Why they were removed

Measured across the 29 retired plans (30,245 lines):

| | Lines | Share |
|---|---:|---:|
| Inside code fences | 17,995 | 57% |
| Blank | 6,012 | 19% |
| Checkbox steps | 1,330 | 4% |
| Headings | 812 | 3% |
| Prose | ~5,330 | 17% |

The 57% in code fences was either superseded by `clusters/` — which Flux reconciles and is therefore
authoritative — or was never built. The `foundry-vtt` plan carried 445 lines of YAML against 193
lines of live manifests, so most plan code did not survive contact with the cluster.

Design rationale had already migrated to its permanent homes. Measured hits per 1000 lines:

| Location | Rationale density |
|---|---:|
| `.claude/rules/` | 54.1 |
| `docs/superpowers/specs/` | 21.5 |
| `docs/runbooks/` | 21.3 |
| `docs/superpowers/plans/` | **5.3** |

Across all 29 there were 7 uses of "Rejected", 2 of "Decision", 1 of "Why not", and **zero** of
"Alternative", "Trade-off" or "considered". The single genuine architectural decision — NFS over
iSCSI for the metrics cold tier — was already recorded in `docs/vm-lt-metrics-storage-path.md` and
in the cold-tier design spec, and the plan itself deferred to the spec for it. Nothing unique was
lost.

They also dominated the Obsidian vault: of 52,817 synced lines, 38,521 were plans — 73% of the org
knowledge base was spent scaffolding.

## Retrieving one

Nothing is destroyed. Every plan is in git history permanently:

```bash
git log --diff-filter=D --format='%h %ad %s' --date=short -- 'docs/superpowers/plans/*'
git show <commit>^:docs/superpowers/plans/<name>.md
```

Each plan also has merged PRs recording what actually shipped, which are a better record of the
outcome than the plan is of the intent.

## Index

| Plan | Added | Lines | Delivered | Documented now in |
|---|---|---:|---|---|
| `1password-eso.md` | 2026-06-01 | 176 | ESO + 1Password Connect, replacing SealedSecrets | `.claude/rules/secrets.md` |
| `audiobookshelf.md` | 2026-06-01 | 606 | Audiobookshelf in `mediastack` | `docs/cluster-reference.md` → Media Stack |
| `authentik.md` | 2026-06-01 | 3018 | Authentik SSO and the forward-auth architecture | `.claude/rules/authentik-akshell.md` |
| `authentik-phase6.md` | 2026-06-01 | 435 | Authentik rollout phase 6 | `.claude/rules/authentik-akshell.md` |
| `cnpg-native-backups.md` | 2026-06-01 | 713 | CNPG WAL archiving + scheduled base backups | `docs/cluster-reference.md` → CNPG |
| `filebrowser.md` | 2026-06-01 | 754 | FileBrowser file-drop service | `docs/cluster-reference.md` → Media Stack |
| `flaresolverr-prowlarr.md` | 2026-05-20 | 384 | FlareSolverr + Prowlarr Cardigann indexers | `docs/cluster-reference.md` → Media Stack |
| `harbor-dockerhub-proxy-cache.md` | 2026-05-30 | 39 | Harbor Docker Hub pull-through cache | `docs/runbooks/harbor-dockerhub-proxy-cache.md` |
| `harbor-lb-migration.md` | 2026-06-01 | 476 | Harbor on a dedicated MetalLB VIP | `docs/cluster-reference.md` |
| `homepage-b2-cloudflare.md` | 2026-06-01 | 699 | b2-exporter + Homepage B2/Cloudflare widgets | `docs/cluster-reference.md` → Monitoring |
| `longhorn-mount-healer.md` | 2026-06-20 | 876 | longhorn-mount-healer CronJob | `docs/cluster-reference.md` → Storage |
| `observability-enhancement.md` | 2026-06-01 | 639 | Observability additions on kube-prometheus-stack | `docs/roadmap.md` Phase 2 |
| `observability-stack.md` | 2026-06-01 | 1057 | kube-prometheus-stack, Loki, Promtail | `docs/roadmap.md` Phase 2 |
| `phase-5c-iac-expansion.md` | 2026-06-01 | 1384 | tofu-controller module expansion | `docs/cluster-reference.md` → Infrastructure as Code |
| `phase5d-iac-modules.md` | 2026-06-01 | 1365 | Further tofu modules | `docs/cluster-reference.md` → Infrastructure as Code |
| `prowlarr-terraform.md` | 2026-06-01 | 451 | `terraform/prowlarr` module | `docs/cluster-reference.md` → Infrastructure as Code |
| `reloader.md` | 2026-05-23 | 411 | Stakater Reloader | `docs/cluster-reference.md` → Infrastructure Services |
| `tailscale.md` | 2026-05-24 | 732 | Tailscale operator, connector and tofu module | `docs/cluster-reference.md` → Infrastructure Services |
| `victoriametrics-cold-tier.md` | 2026-07-07 | 235 | VictoriaMetrics 395d cold tier on `pool_0` | `docs/vm-lt-metrics-storage-path.md` + `docs/superpowers/specs/victoriametrics-cold-tier-design.md` |
| `victoriametrics-longterm.md` | 2026-05-31 | 497 | VictoriaMetrics hot tier + remote_write | `docs/cluster-reference.md` → Monitoring |
| `vmware-metrics.md` | 2026-06-01 | 601 | vmware-exporter, ServiceMonitor, dashboards | `docs/cluster-reference.md` → Monitoring |
| `vollmint-api-frontend.md` | 2026-07-25 | 4566 | vollmint HTTP API + React SPA | `vollmint` repo |
| `vollmint-backend.md` | 2026-07-22 | 2090 | vollmint backend and store layer | `vollmint` repo |
| `vollmint-deploy.md` | 2026-07-25 | 937 | vollmint namespace, CNPG DB, ingress, netpols | `docs/cluster-reference.md` → Applications |
| `vollmint-forecasting-splits-insights.md` | 2026-08-02 | 3799 | vollmint forecasting, splits, insights | `vollmint` repo |
| `vollmint-networth-snapshots.md` | 2026-08-10 | 1620 | vollmint net-worth snapshots | `vollmint` repo |
| `vollmint-recurring-trends-rules.md` | 2026-08-17 | 1361 | vollmint recurring detection, trends, rules | `vollmint` repo |
| `volsync-mover-security-context.md` | 2026-06-01 | 262 | VolSync mover securityContext fix | `docs/cluster-reference.md` → Storage |
| `worker02-scheduling-rebalance.md` | 2026-06-01 | 62 | k8sworker02 scheduling/memory rebalance | `.claude/rules/storage.md` |

**Total retired: 30,245 lines across 29 plans.**

## Going forward

A plan is scaffolding with a lifecycle. When the work ships, the durable knowledge belongs in
`.claude/rules/` (operational rules and traps), `docs/runbooks/` (procedures), `docs/cluster-reference.md`
(inventory) or `docs/superpowers/specs/` (design rationale) — and the plan can go. See
`.claude/rules/docs.md` for which document a change belongs in.
