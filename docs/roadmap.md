# Vollminlab Cluster Roadmap

Living document tracking planned infrastructure work. Update status as projects progress.

**Status key:** `planned` | `in-progress` | `done` | `blocked` | `deferred`

---

## Phase 1 — Foundations (Prerequisite for everything else)

### 1.1 Backup Stack — MinIO + Velero + Backblaze B2

**Status:** `done`

- MinIO deployed in-cluster as the primary (fast) backup target
- Velero with two BackupStorageLocations: `minio` (default, daily at 02:00 UTC) and `b2` (off-site, daily at 04:00 UTC)
- Backblaze B2 bucket: `vollminlab-k8s-backups`, region `us-west-000`
- Credentials in SealedSecrets; validation frequency tuned to 1h to limit B2 Class C API calls
- Circular backup fixed (PR #410): `minio` namespace excluded from FSB on both schedules
- Scoped MinIO access key for Velero deployed (PR #362) — root credentials no longer used
- **Test restore validated:** Minecraft namespace restored successfully from Velero backup — confirmed Velero can restore a running workload with PVC data intact. Phase 8 Velero gate is cleared.

---

### 1.2 GitHub Actions Runner Migration

**Status:** `done`

Migrated to ARC v2 (`gha-runner-scale-set-controller` + `AutoscalingRunnerSet`). Legacy summerwind resources removed. Both ARC HelmReleases use `OCIRepository` + `spec.chartRef` per current Flux best practice. Single `vollminlab` runner pool.

---

### 1.3 Renovate Bot — Automated Helm Chart Updates

**Status:** `done`

Self-hosted Renovate deployed as a Kubernetes CronJob in the `renovate` namespace. Runs nightly at 02:00 ET. Covers:

- All `HelmRelease` chart versions (`spec.chart.spec.version`) across all namespaces
- All `OCIRepository` tag versions (`spec.ref.tag`) — TrueCharts mediastack apps, ARC, Renovate itself
- GitHub Actions `uses:` version pins in all workflow files

All updates require manual review (no automerge). Dependency Dashboard issue maintained automatically in GitHub.

**Added:** `kubernetes` datasource via a custom regex manager targeting `.kubernetes-version` at the repo root. Renovate opens a PR when a new stable K8s version is released; the existing Ansible playbook (`k8s-upgrade.yml`) remains the upgrade executor. Pre-releases filtered via `allowedVersions` regex.

---

### 1.4 Kyverno Policy Violations Cleanup

**Status:** `done`

Fixed all outstanding Kyverno policy violations to establish a clean baseline (PRs #221, #229, 2026-04-04). Label injection via mutate policies, autogen disabled to prevent webhook breakage.

---

### 1.5 Flux Image Update Automation

**Status:** `deferred`

Flux IUA's value is specifically for clusters that build and push custom container images — it scans a registry, detects new tags, and commits the update back to Git automatically (zero-touch CD for custom images). This cluster runs exclusively upstream Helm charts; Renovate already handles version bumps with human review, which is preferable. Revisit only if a CI pipeline starts building and pushing custom images.

---

### 1.6 Volsync — Continuous PVC Replication

**Status:** `done` — PRs #728–#732

Restic-based PVC replication to Backblaze B2 for 13 PVCs (CNPG clusters, Harbor registry, Longhorn app volumes). `ReplicationSource` CRs with 15-min sync interval. Scoped B2 application key. CSI VolumeSnapshot CRDs deployed separately. Metrics endpoint secured after TargetDown alert (PR #732).

---

## Phase 2 — Observability Stack

**Goal:** Build a production-grade SRE observability platform to upskill and to support everything that follows (Istio, Chaos Mesh, SLOs).

### 2.1 Prometheus + Grafana (kube-prometheus-stack)

**Status:** `done`

`kube-prometheus-stack` deployed in `monitoring` namespace:

- Prometheus scraping cluster metrics, Grafana as the unified UI, Alertmanager → Pushover notifications via SealedSecret
- ServiceMonitors: ingress-nginx (built-in), Longhorn, Velero, cert-manager
- Control plane metrics: etcd, controller-manager, scheduler, kube-proxy all bound to `0.0.0.0` and scraped
- Node-exporter hostname relabeling: `instance` label is node hostname, not `ip:port`
- Custom `PrometheusRule`: cert-manager certificate expiry (14d warning / 24h critical), Velero backup overdue/failed/metric-missing
- Dashboards: arr-media (Radarr/Sonarr/Bazarr consolidated), exportarr (Radarr/Sonarr/Bazarr/SABnzbd), Longhorn (custom sidecar), Velero (custom sidecar)

### 2.2 Loki + Promtail

**Status:** `done`

- Loki (SingleBinary mode) deployed, MinIO-backed object storage
- Promtail DaemonSet shipping logs from all nodes
- Grafana Loki data source configured and integrated with Grafana from 2.1

### 2.3 Goldilocks — VPA Resource Recommender

**Status:** `done` — PRs #721, #734

Goldilocks VPA recommender deployed in `goldilocks` namespace (PR #721). VPA recommendations enabled for all app namespaces (PR #734). Resource limits right-sized across the cluster based on Goldilocks data (PR #735).

---

### 2.4 OpenTelemetry Collector

**Status:** `planned` (deploy after Phase 5 / Cilium service mesh decision)

Deploy the OpenTelemetry Operator + a collector pipeline:

- Receive OTLP traces from instrumented apps and Istio
- Export to Grafana Tempo (preferred for Grafana integration)
- Foundation for distributed tracing across the service mesh

---

## Phase 2.5 — Flux Upgrade (v2.4 → v2.8)

**Status:** `done`

Cluster upgraded from Flux v2.4.0 to v2.8.6 via two hops (PRs #423, #426, #428).

- 9 OCIRepository files migrated from `source.toolkit.fluxcd.io/v1beta2` → `v1`
- Both hops required manually applying `gotk-components.yaml` with `--server-side --field-manager=kustomize-controller --force-conflicts` to break the bootstrap deadlock (old controller can't apply config that removes its own internal API references)
- Post-hop: patched `ocirepositories` CRD `status.storedVersions` via `--subresource=status` to clear stale `v1beta2` entry

---

## Phase 3 — Security & Access

### 3.0 PKI — Automated Certificate Lifecycle

**Status:** `done` — PR #540

Control plane certs issued by kubeadm expire annually and require manual renewal on each control plane node. This became an incident on 2026-04-14 when all certs expired simultaneously.

**Next expiry: 2027-04-14.** cert-manager cannot write to the control plane node filesystem, so the approach keeps kubeadm as the issuer and wraps the renewal in GitOps-managed CronJobs.

**Implementation (kube-system namespace):**

- `kubeadm-cert-monitor` — monthly CronJob (1st of each month, 09:00 UTC). Uses `kubectl exec` into `kube-apiserver-*` and `etcd-*` static pods to check cert expiry via openssl. Sends Pushover alert at 90-day warning / 30-day critical. No hostPath required.
- `kubeadm-cert-renew-k8scp01/02/03` — three bi-annual CronJobs (April 14 + October 14, staggered 15 min apart). Uses `nsenter -t 1` to enter host namespaces and run `kubeadm certs renew all` + `systemctl restart kubelet`. Sends Pushover notification on success or differentiated alerts on partial failure.
- `exceptions-kubeadm-cert-renew` Kyverno `PolicyException` — preemptively exempts renewal pods from `restrict-privileged` and `restrict-hostpath-usage` policies in case `kube-system` is ever removed from those policies' exclude lists.

---

### 3.1 Authentik — SSO / Identity Provider

**Status:** `done`

Design doc: `docs/authentik-design.md`.

- **Phase 1** `done` — Core infra: CNPG Cluster CR, Authentik server+worker, cloudflared tunnel for `authentik.vollminlab.com`. Redis was not deployed — Authentik 2025.10+ dropped the Redis dependency entirely.
- **Phase 2** `done` — External proxy outpost + Jellyseerr (replaces Overseerr) + Jellyfin OIDC. Plex decommissioned; Jellyfin stable.
- **Phase 3** `done` — Native OIDC: Grafana, Harbor, Headlamp, Portainer, Audiobookshelf, MinIO
- **Phase 4** `done` — Forward-auth sweep: Longhorn, Homepage, arr stack, Tautulli, Shlink Web, Policy Reporter
- **Phase 5a** `done` — tofu-controller deployed in `tofu` ns; MinIO `terraform-state` bucket + scoped IAM user (PRs #539)
- **Phase 5b** `done` — Full Authentik config under OpenTofu IaC: groups, users, OAuth2/proxy providers, scope mappings, applications, outpost, Portainer OAuth settings. All existing objects imported into state. Client secrets sealed. Post-merge fixes: cross-namespace refs (`allowCrossNamespaceRefs: true`, PR #547), flux-system NetworkPolicy for tofu→source-controller (PR #548, #549), Authentik provider 2026.2.x schema (`invalidation_flow` required, `redirect_uris`→`allowed_redirect_uris`, portainer `api_user`/`api_password`, PR #550, #551). tofu-controller reconciling cleanly. (PRs #542, #546–#551)
- **Phase 5c** `done` — `terraform fmt --check` + `tofu validate` CI job for `terraform/**` PRs (PR #558). MinIO IaC: `terraform/minio/` module managing 4 buckets, 4 IAM users, 3 custom policies via `aminueza/minio` provider (PR #559). Harbor IaC: `terraform/harbor/` module managing OIDC config and 2 projects via `goharbor/harbor` provider (PR #560). Grafana IaC: `terraform/grafana/` module managing SSO settings, Pushover contact point, and default notification policy via `grafana/grafana` provider (PR #561). All existing objects imported into state. Legacy Harbor `extraEnvVars` OIDC config and Grafana `[auth.generic_oauth]` ini removed (PRs #571, #572).
- **Phase 5d** `done` — Cloudflare IaC: `terraform/cloudflare/` managing 3 Zero Trust tunnels, 3 tunnel configs, and 3 DNS CNAME records via `cloudflare/cloudflare` v5 provider (PRs #575–#578). Radarr IaC: `terraform/radarr/` managing quality profiles, download client, indexer proxies via `devopsarr/radarr` v2.2 (PR #575). Sonarr IaC: `terraform/sonarr/` managing quality profiles, download client, indexer proxies via `devopsarr/sonarr` v3.3 (PR #575). Backblaze B2 IaC: `terraform/b2/` managing Velero bucket and scoped application key via `Backblaze/b2` v0.8 (PR #575). All 4 tofu-controller CRs reconciling cleanly (`True`). Provider quirks: Cloudflare v5 requires `lifecycle { ignore_changes = all }` on both tunnel and tunnel-config resources (PRs #577, #582); Radarr/Sonarr quality profile `name` returns null on single-quality groups requiring same lifecycle fix (PR #578); provider URLs need explicit ports (PR #577); B2 master key rolled after initial credential rejected (PR #580).
- **Phase 6** `done` — NPM-proxied external services via Authentik `auth_request`: Pi-hole ✅, HAProxy stats ✅, HAProxy DMZ stats ✅. NPM and TrueNAS skipped (can't disable their own auth — double-auth not worth it). vCenter OIDC deferred (no generic OIDC in this vCenter version). Tofu application entries for all five services (PRs #620, #621).

---

### 3.2 MetalLB: L2 → BGP Peering

**Status:** `planned` (low priority — discuss before Phase 8 Cilium migration)

**Problem:** k8sworker04 shows the MetalLB VIP (ingress-nginx LoadBalancer IP) in the UDM console instead of its actual node IP. In L2 mode, MetalLB answers ARP for VIPs from whichever node is the current leader; the UDM sees this ARP and maps that node's MAC to the VIP address, shadowing the real node IP.

**Fix:** Switch MetalLB from L2 advertisement to BGP peering with the UDM Pro. MetalLB advertises VIP routes over BGP; the router learns them as routes (not ARP entries) and routes VIP traffic at L3. Node IPs are unaffected. Also enables ECMP across multiple nodes for better load distribution.

**Note on Cilium overlap:** Cilium (Phase 8) has native BGP support (`CiliumBGPPeeringPolicy`) and a built-in L4LB that can replace MetalLB entirely. If Phase 8 is imminent, it may be cleaner to skip this and migrate BGP as part of the Cilium rollout. Decide at the start of Phase 8 planning.

---

### 3.3 Personal Media Services — External Access (Plex)

**Status:** `done`

- Plex migrated from TrueNAS into `mediastack` namespace (PRs #439, #442). Media files via existing SMB CSI mounts (`pvc-movies`, `pvc-tv`). 20Gi Longhorn PVC for config/metadata.
- `cloudflared` deployed as a plain Deployment in `mediastack` (PR #440). Tunnel connects outbound to Cloudflare edge; routes `plex.vollminlab.com → http://plex.mediastack.svc.cluster.local:32400`.
- Plex's own auth (myPlex accounts) is the sole access gate — no Cloudflare Access policy. Remote access disabled in Plex; port 32400 confirmed closed on public IP.
- Pi-hole DNS updated: `plex.vollminlab.com → 192.168.152.244`. TrueNAS Plex shut down.
- Overseerr replaced by Seerr (Authentik Phase 2, PR #494); Seerr has Authentik OIDC SSO and forward-auth (PR #600).

### 3.4 Jellyfin — Free External Streaming for Friends

**Status:** `done`

- Jellyfin deployed in `mediastack` alongside Plex. Official `jellyfin/jellyfin` chart v3.2.0.
- Shares `pvc-movies` and `pvc-tv` SMB RWX mounts with Plex (read-only access, UID/GID 568).
- Dedicated `pvc-jellyfin-config` 20Gi Longhorn RWO.
- Separate `cloudflared-jellyfin` Deployment with its own tunnel — independent blast-radius from Plex.
- Route: `jellyfin.vollminlab.com → http://jellyfin.mediastack.svc.cluster.local:8096`.
- Security gate: Jellyfin built-in auth only. No Cloudflare Access policy (native apps cannot complete browser auth challenge). Public signup disabled; accounts managed manually.
- Hardware transcoding deferred — CPU only. See roadmap for follow-up.

**Deferred follow-ups:**

- Hardware transcoding (`/dev/dri` device mount) — requires evaluating Kyverno `hostPath` audit policy impact
- Jellyfin metrics / Grafana dashboard (parallel to Tautulli work in 3.5)

### 3.5 Tautulli / Plex Metrics Dashboard

**Status:** `done`

Tautulli deployed in `mediastack`. Metrics dashboard complete. Tautulli later replaced by Jellystat (PR #526) — Jellyfin-native play history and stats with a CNPG-backed PostgreSQL database and Homepage widget integration.

---

### 3.6 Harbor Network Isolation — LoadBalancer Expose

**Status:** `done` — PR #591

Harbor migrated to `expose.type: loadBalancer` with dedicated MetalLB VIP `192.168.152.245`. Harbor's own nginx handles TLS via cert-manager wildcard cert. The `arc-runners-egress` NetworkPolicy uses `ipBlock: 192.168.152.245/32` for genuine Harbor-specific isolation. external-dns manages the `harbor.vollminlab.com` A record automatically. GHA robot accounts managed via tofu (`terraform/harbor/`); post-migration issue with robot import blocks resolved in PR #612.

---

### 3.7 Reloader (Stakater)

**Status:** `done` — this PR

Stakater Reloader deployed in `reloader` namespace, watching all namespaces. Resources opt in via `reloader.stakater.com/auto: "true"` annotation on Deployments, StatefulSets, and DaemonSets. ConfigMap or SealedSecret changes trigger automatic rolling restarts without manual `kubectl rollout restart`.

---

### 3.8 external-secrets + 1Password Connect

**Status:** `planned`

Replaces the SealedSecrets workflow over time. External Secrets Operator syncs 1Password vault items directly into Kubernetes Secrets and rotates them automatically when the source changes — no `kubeseal` step.

- Deploy 1Password Connect server (dedicated namespace)
- Deploy External Secrets Operator with a 1Password `ClusterSecretStore`
- Migrate namespaces incrementally; SealedSecrets and external-secrets can coexist during transition
- Long-term: decommission `sealed-secrets` controller once all secrets migrated

---

### 3.9 Trivy Operator

**Status:** `done` — PR #721

Trivy Operator deployed in `trivy-system` namespace alongside Goldilocks. Scans all running workloads continuously; generates `VulnerabilityReport` and `ConfigAuditReport` CRs. DMZ and control-plane node tolerations added (PR #722). MinIO concurrent scan jobs throttled to prevent timestamp ordering issues (PR #724).

---

### 3.10 Tailscale

**Status:** `planned` (lower priority — Cloudflare tunnels cover HTTP; Tailscale adds private mesh)

Deploy the Tailscale Kubernetes operator. Enables private WireGuard-based access to any cluster service from any tailnet device — useful for non-HTTP protocols, SSH to nodes, and access during Cloudflare outages. Individual services can be tagged into the tailnet without changing ingress config.

---

### 3.11 East-West Network Policies (Non-DMZ)

**Status:** `planned` (security hardening — lower priority)

All namespaces outside the DMZ are fully open east-west. A compromised pod in `mediastack` can reach Harbor, CNPG, MinIO, or Authentik. Minimum scope:

- `harbor` — restrict inbound to ingress-nginx VIP + ARC runner egress only
- `mediastack` — restrict outbound to known upstreams (indexers, Usenet, download targets)
- `monitoring` — restrict write access (Prometheus scrape sources only)

**Note:** Harbor's dedicated LoadBalancer VIP (3.6) makes its network policy straightforward — `ipBlock: 192.168.152.245/32` instead of the shared nginx VIP. Sequence 3.11 after 3.6 completes.

---

## Phase 4 — Infrastructure Diagrams

**Goal:** Create living architecture diagrams for every repo in the org once observability and security are settled — so diagrams reflect a stable system and don't need immediate revision.

### 4.0 Diagram Creation — All Repos

**Status:** `planned`

Create a `diagrams/` folder in each repo with declarative Mermaid diagrams covering the full system as it exists at that point. Scope:

- `k8s-vollminlab-cluster` — cluster topology (nodes, namespaces, networking), Flux reconciliation flow, storage layout (Longhorn, MinIO), backup data path (Velero → B2), DMZ isolation
- `homelab-infrastructure` — Terraform resource graph, network topology, VM/node inventory
- `github-admin` — repo/branch protection structure
- Any other repos as they exist

**Format:** `.mmd` files (Mermaid — declarative, committable to Git, rendered natively in GitHub PRs/issues, previewable in VS Code with the Mermaid extension). Matches the declarative/GitOps ethos of the cluster. Diagram types: `graph TD` for topology, `flowchart` for data flows, `sequenceDiagram` for reconciliation flows.

**Maintenance:** diagrams live in `<repo>/diagrams/` and are updated as the system changes.

---

## Phase 5 — Service Mesh

### 5.1 Istio

**Status:** `planned` (depends on Phase 2 observability being in place)

Deploy Istio via the Helm-based install (not `istioctl`):

- Mutual TLS (mTLS) between all services by default
- Traffic management (weighted routing, retries, circuit breaking)
- Integration with Kiali for topology visualization
- Distributed tracing via OTLP → Grafana Tempo

Note: Istio's sidecar injection will interact with Kyverno policies — review `kyverno.md` before deploying.

**Cilium decision point:** Cilium (Phase 8) ships a native service mesh (Cilium Mesh) with mTLS, traffic management, and Hubble L7 observability. Evaluate at Phase 8 planning time whether Cilium Mesh satisfies these requirements before investing in Istio. If it does, Phases 5 and 8 merge and Istio is dropped entirely.

---

## Phase 6 — SRE Practice

### 6.1 SLOTH — SLO-based Alerting

**Status:** `planned` (depends on Phase 2.1 Prometheus)

Use SLOTH to generate SLO alert rules from a declarative YAML spec:

- Define SLIs/SLOs for key services (ingress latency, Shlink availability, etc.)
- SLOTH generates Prometheus recording rules + multi-burn-rate alerts
- Dashboards in Grafana

### 6.2 Chaos Mesh

**Status:** `deferred`

Controlled fault injection for resilience testing (pod kill, network partition, CPU/memory stress). No immediate plans — incidents are being handled well without it. Revisit after SLOs (6.1) are established so there are clear baselines to validate against.

---

## Phase 7 — Node Maintenance Window

**Status:** `in-progress`
**Risk:** Medium — rolling node reboots; cluster stays available if done one node at a time

Normalize all nodes to current versions before the CNI migration. Three sub-items sequenced to allow bundling reboots efficiently.

**Current state (2026-05-19):**

| Node | K8s | Kernel | Ubuntu | containerd |
| --- | --- | --- | --- | --- |
| k8scp01 | v1.33.12 | 6.8.0-106 | 24.04.2 | 1.7.27 |
| k8scp02 | v1.33.12 | 6.8.0-85 | 24.04.1 | 1.7.27 |
| k8scp03 | v1.33.12 | 6.8.0-87 | 24.04.1 | 1.7.27 |
| k8sworker01 | v1.33.12 | 6.8.0-107 | 24.04.4 | 1.7.27 |
| k8sworker02 | v1.33.12 | 6.8.0-107 | 24.04.1 | 1.7.27 |
| k8sworker03 | v1.33.12 | 6.8.0-107 | 24.04.1 | 1.7.27 |
| k8sworker04 | v1.33.12 | 6.8.0-110 | 24.04.4 | **2.2.3** |
| k8sworker05 | v1.33.12 | 6.8.0-87 | 24.04.3 | 1.7.27 |
| k8sworker06 | v1.33.12 | 6.8.0-106 | 24.04.1 | 1.7.27 |

### 7.1 Kubernetes upgrade (1.32 → current stable)

**Status:** `in-progress`

Upgrade all nodes through four minor-version hops. K8s 1.33 goes EOL 2026-06-28 — hops 2–4 must complete before then.

| Hop | Target | Method | Status |
| --- | --- | --- | --- |
| 1 | 1.33.12 | Manual node-by-node | `done` — 2026-05-19 |
| 2 | 1.34.x | Ansible `k8s-upgrade.yml` | `planned` |
| 3 | 1.35.x | Ansible `k8s-upgrade.yml` | `planned` |
| 4 | 1.36.x | Ansible `k8s-upgrade.yml` | `planned` |

Playbook hardening from hop 1: `serial: 1`, `--disable-eviction` on all drain commands, Longhorn volume health gate after each uncordon (waits for zero degraded volumes before proceeding to next node).

**Compatibility note:** K8s 1.33 introduced `ServiceCIDR`/`IPAddress` networking types that Kyverno's catch-all webhook intercepts and rejects (HTTP 400, not a timeout — `failurePolicy: Ignore` has no effect). Fixed via `matchConditions` CEL expressions + `resourceFilters` entries in the Kyverno ConfigMap (PR #630). Verify this fix is present and survives each subsequent hop.

### 7.2 containerd normalization

**Status:** `planned` (bundle with 7.3 — both require node drain + reboot)

k8sworker04 is on containerd 2.2.3; all other nodes are on 1.7.27. The 2.x line is the current stable track (1.7.x is maintenance-only). Target: upgrade all 1.7.x nodes to containerd 2.x current latest.

> **Status note (2026-05-30):** spot-checked nodes (k8sworker01, k8scp01) are already on containerd **v2.2.4** — this normalization may already be complete from the 2026-05-30 maintenance window. Verify all 9 nodes' versions and update/close this item.

- Drain node → stop kubelet → upgrade containerd → restart containerd + kubelet → uncordon
- One node at a time; Longhorn health gate between nodes

### 7.3 Kernel and OS normalization

**Status:** `planned` (bundle with 7.2)

Kernel versions range from 6.8.0-85 (k8scp02) to 6.8.0-110 (k8sworker04); Ubuntu patch levels range from 24.04.1 to 24.04.4. Run `apt upgrade` on each node to bring kernel and userspace to current; requires full reboot.

- Bundle with 7.2: drain → apt upgrade → reboot → uncordon covers both in one pass
- One node at a time; same Longhorn health gate

Do not bundle 7.2/7.3 with the Cilium migration — separate maintenance windows.

---

## Phase 8 — CNI Migration (Calico → Cilium)

**Status:** `planned`
**Depends on:** ~~1.1 test restore~~ ✓ validated (Minecraft restore), Phase 2 observability ✓ done, Phase 7 node maintenance complete
**Risk:** High — CNI replacement requires a full cluster maintenance window

Cilium offers significant advantages over Calico for this use case:

- **Hubble** — built-in L4/L7 network observability (flows, DNS, HTTP)
- eBPF-native (better performance, richer policy)
- Native Gateway API support
- Industry direction for SRE/platform engineering roles

**Expanded scope — Cilium enables a full networking stack simplification:**

- **CNI replacement:** Calico → Cilium (eBPF-native, richer policy, better performance)
- **MetalLB replacement:** Cilium L4LB + BGP peering replaces MetalLB entirely. Pair with UDM BGP configuration (see 3.2 note — skip the standalone MetalLB BGP migration and do it here instead)
- **Gateway API adoption:** Cilium's native Gateway API implementation replaces nginx-ingress. All `ingress.yaml` files migrate to `HTTPRoute` resources. nginx-ingress is decommissioned at the end of this phase.
- **kube-proxy replacement (optional):** eBPF-based routing; evaluate during planning
- **Hubble:** L4/L7 network observability (flows, DNS, HTTP) — feeds Phase 2.4 OpenTelemetry pipeline
- **Istio decision:** If Cilium Mesh provides sufficient mTLS + traffic management, Phase 5 is dropped. Decide at the start of this phase.

Migration approach:

1. ~~Confirm Velero backups are healthy and a test restore has been validated~~ — done (Minecraft restore)
2. Confirm Phase 2 observability is in place (Prometheus + Loki at minimum)
3. Confirm all nodes are on current, normalized versions (Phase 7)
4. Plan a dedicated maintenance window — CNI replacement + nginx-ingress migration are both disruptive
5. Drain nodes, uninstall Calico, install Cilium
6. Validate network policies and DMZ rules (especially DMZ namespace on k8sworker05)
7. Migrate all Ingress resources to HTTPRoute; validate each service
8. Decommission MetalLB and nginx-ingress HelmReleases
9. Update `bootstrap/calico/` → `bootstrap/cilium/` references

This is a cluster rebuild risk event — do not attempt without working backups.

---

## Deferred / Under Evaluation

| Item | Notes |
| --- | --- |
| Dynatrace / Dash0 | Homegrown stack (Prometheus + Loki + Grafana) is now established — evaluate if a managed platform adds value |
| Tekton | Not needed for dependency updates (Renovate covers that); revisit if building/pushing custom images |
| Crossplane | Potential future IaC-as-Kubernetes for cloud resources — redundant with tofu-controller today |
| Foundry VTT | Self-hosted tabletop game server (`felddy/foundryvtt` image). Very feasible: single stateful web app, ~5Gi PVC, no database, handles its own player auth. Add to `foundry` namespace with `category: apps`. Sequence after Cilium migration so it lands on the final ingress stack. |

---

## Completed

| Item | PR / Notes |
| --- | --- |
| Kyverno policy violations cleanup | PRs #221, #229 — label injection via mutate policies, autogen disabled |
| Shlink Ingress Controller | Custom Go controller: Ingress annotation → auto-create `vollm.in/<slug>` via Shlink API |
| Shlink short link service | Deployed with `vollm.in`, `go.vollminlab.com`, `vl.vollminlab.com` |
| Internal CA issuer | 10-year cert, `internal-ca` ClusterIssuer |
| ARC runner pool cleanup | Removed pool-2, pool-1 bumped to 3 replicas |
| ARC migration to OCIRepository | Migrated `arc-repo` HelmRepository type:oci to two OCIRepository resources (arc-controller-repo, arc-runners-repo) per Flux best practice |
| Renovate Bot | Deployed as CronJob, nightly, covers HelmRelease + OCIRepository + GitHub Actions |
| HelmRepository naming convention | Renamed minio/velero/shlink to use -repo suffix; documented convention in flux.md |
| Kyverno category expansion | Added `media` and `ci` as valid category values |
| Sealed Secrets | Bootstrap procedure + 1Password key backup |
| DMZ namespace + Minecraft | Node-isolated on k8sworker05, Kyverno-enforced |
| kube-prometheus-stack | Prometheus + Grafana + Alertmanager (→ Pushover) in `monitoring` namespace |
| Loki + Promtail | SingleBinary Loki, MinIO-backed; Promtail DaemonSet on all nodes |
| Control plane metrics | etcd/controller-manager/scheduler/kube-proxy bound to `0.0.0.0` and scraped by Prometheus |
| Observability ServiceMonitors + alert rules | Longhorn, Velero, cert-manager scraped; custom `PrometheusRule` for cert expiry + Velero health (PR #395) |
| Node-exporter hostname relabeling | `instance` label is node hostname instead of IP:port (PR #397) |
| Exportarr | Radarr, Sonarr, Bazarr, SABnzbd exportarr exporters + Grafana dashboards (PRs #393–#394) |
| Grafana dashboards | Arr-media consolidated, Longhorn custom sidecar (PR #419), Velero custom sidecar (PR #420) |
| Etcd defrag CronJob | Weekly defrag job in `kube-system` (PR #413) |
| Velero circular backup fix | `minio` namespace excluded from FSB on both schedules; node-agents healthy on all 6 nodes (PR #410) |
| Velero scoped MinIO access key | Replaced root credentials with a least-privilege `velero-svc` MinIO key (PR #362) |
| Flux upgrade v2.4 → v2.8 | Two-hop upgrade via PRs #423, #426, #428; 9 OCIRepository files migrated to v1; bootstrap deadlock fix documented |
| Plex in-cluster + Cloudflare Tunnel | Plex migrated from TrueNAS (PRs #439, #440, #442); outbound-only tunnel, no open ports, Plex auth as sole gate |
| Kyverno K8s 1.33 compatibility fix | `ServiceCIDR`/`IPAddress` excluded via `matchConditions` CEL + `resourceFilters`; fixed apiserver crash on upgrade (PR #630) |
| K8s upgrade hop 1 (1.32 → 1.33.12) | All 9 nodes upgraded manually node-by-node with `--disable-eviction` and Longhorn health gates; Ansible playbook hardened for hops 2–4 (2026-05-19) |
| CNPG native backups | WAL archiving + daily scheduled base backups for all CNPG clusters (authentik-db, harbor-db, shlink-db, jellystat-db) via MinIO barman object store; scoped `cnpg-svc` MinIO user (PR #517; schedule fix PR #655) |
| Jellystat | Replaced Tautulli: Jellyfin-native play stats, CNPG-backed PostgreSQL, Homepage widget (PR #526) |
| FileBrowser | File drop service in `mediastack`; SMB-backed storage (audiobooks-incoming + misc-incoming); Authentik forward-auth; Cloudflare tunnel; tofu group/policy management (PR #629) |
| FlareSolverr + Prowlarr Cardigann indexers | FlareSolverr deployed in `mediastack`; 1337x and EZTV unblocked via indexer proxy; YTS imported into tofu state (PR #688) |
| qBittorrent + gluetun VPN sidecar | Deployed in `mediastack` with gluetun PIA VPN sidecar; port-forwarding via CA Montreal region; Homepage widget (PR #672) |
| Readarr + tofu IaC | Readarr deployed on bookshelf/hardcover fork; `terraform/readarr/` tofu workspace; Prowlarr sync; Homepage widget (PRs #642, #644) |
| Prowlarr tofu IaC | `terraform/prowlarr/` module managing Newznab indexers and app sync connections for Radarr/Sonarr/Readarr; `prowlarr-config` CR reconciling cleanly |
| VMware metrics exporter | `kremers/vmware-exporter` Helm chart in `monitoring`; ServiceMonitor, PrometheusRules for host/datastore alerts, Grafana dashboards for ESXi hosts + datastores/VMs (PRs #700, #705) |
| Homepage B2 + Cloudflare widgets | b2-exporter (custom Python/Prometheus) deployed in `monitoring`; Homepage prometheus widget for B2 bucket stats; three Cloudflare tunnel status widgets (PRs #579, #590) |
| Nginx ssl-redirect loop fix | `use-forwarded-headers: "true"` in nginx configmap; fixed redirect loops for Cloudflare-fronted services (PR #649) |
| Cloudflare WAF bypass for Authentik | WAF skip rule for `authentik.vollminlab.com` to allow Authentik flow executor calls through Cloudflare bot protection (PR #648) |
| Volsync — PVC replication to B2 | PRs #728–#732 — restic ReplicationSources for 13 PVCs, 15-min sync, scoped B2 key |
| Goldilocks VPA recommender | PRs #721, #734, #735 — VPA recommendations enabled cluster-wide, limits right-sized |
| Trivy Operator | PRs #721–#722, #724 — runtime vulnerability + config audit scanning, all nodes tolerated |
| Stakater Reloader | This PR — auto rolling restarts on ConfigMap/Secret changes, opt-in via annotation |
| Harbor Docker Hub pull-through cache | `dockerhub-proxy` Harbor proxy-cache project (authenticated read-only PAT) + containerd registry mirror on all 9 nodes routing `docker.io` through Harbor; eliminates anonymous 429 `ImagePullBackOff` storms on mass reschedule. k8s repo PR #809 (Harbor proxy via tofu) + ansible-playbooks PRs #9/#10 (containerd mirror playbook). Runbook: `docs/runbooks/harbor-dockerhub-proxy-cache.md` |
