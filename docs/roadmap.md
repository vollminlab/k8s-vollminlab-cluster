# Vollminlab Cluster Roadmap

Living document tracking planned infrastructure work. Update status as projects progress.

**Status key:** `planned` | `in-progress` | `done` | `blocked` | `deferred` | `superseded`

**Last verified against the live cluster: 2026-08-22.** Node versions, deployed namespaces and
app directories in this document were re-measured on that date. Where a status was wrong it has
been corrected in place with the evidence; see the "Corrections" note at the end.

---

## Phase 1 — Foundations (Prerequisite for everything else)

### 1.1 Backup Stack — MinIO + Velero + Backblaze B2

**Status:** `done`

- MinIO deployed in-cluster as the primary (fast) backup target
- Velero with two BackupStorageLocations: `minio` (default, daily at 02:00 UTC) and `b2` (off-site, daily at 04:00 UTC)
- Backblaze B2 bucket: `vollminlab-k8s-backups`, region `us-west-000`
- Credentials in ExternalSecrets (originally SealedSecrets); validation frequency tuned to 1h to limit B2 Class C API calls
- Circular backup fixed (PR #410): `minio` namespace excluded from FSB on both schedules
- Scoped MinIO access key for Velero deployed (PR #362). Root credentials are no longer used
- **Test restore validated:** Minecraft namespace restored successfully from Velero backup, confirming Velero can restore a running workload with PVC data intact. Phase 8 Velero gate is cleared.

---

### 1.2 GitHub Actions Runner Migration

**Status:** `done`

Migrated to ARC v2 (`gha-runner-scale-set-controller` + `AutoscalingRunnerSet`). Legacy summerwind resources removed. Both ARC HelmReleases use `OCIRepository` + `spec.chartRef` per current Flux best practice. Single `vollminlab` runner pool.

---

### 1.3 Renovate Bot — Automated Helm Chart Updates

**Status:** `done`

Self-hosted Renovate deployed as a Kubernetes CronJob in the `renovate` namespace. Runs nightly at 02:00 ET. Covers:

- All `HelmRelease` chart versions (`spec.chart.spec.version`) across all namespaces
- All `OCIRepository` tag versions (`spec.ref.tag`): TrueCharts mediastack apps, ARC, Renovate itself
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

Flux IUA's value is specifically for clusters that build and push custom container images. It scans a registry, detects new tags, and commits the update back to Git automatically (zero-touch CD for custom images). This cluster runs exclusively upstream Helm charts; Renovate already handles version bumps with human review, which is preferable. Revisit only if a CI pipeline starts building and pushing custom images.

---

### 1.6 Volsync — Continuous PVC Replication

**Status:** `done` — PRs #728–#732

Restic-based PVC replication to Backblaze B2 for 13 PVCs (CNPG clusters, Harbor registry, Longhorn app volumes). `ReplicationSource` CRs with 15-min sync interval. Scoped B2 application key. CSI VolumeSnapshot CRDs deployed separately. Metrics endpoint secured after TargetDown alert (PR #732).

---

## Phase 2 — Observability Stack

**Goal:** Build a production-grade SRE observability platform to upskill and to support everything that follows (Chaos Mesh, SLOs). Istio was on this list too, until Phase 5 was dropped on 2026-08-22.

### 2.1 Prometheus + Grafana (kube-prometheus-stack)

**Status:** `done`

`kube-prometheus-stack` deployed in `monitoring` namespace:

- Prometheus scraping cluster metrics, Grafana as the unified UI, Alertmanager → Pushover notifications via ExternalSecret
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

**Status:** `planned` — **re-scoped 2026-08-22, tracked as issue #1194**

The original plan (OTLP traces from instrumented apps and Istio → Grafana Tempo) does not hold:
**there are no instrumented apps.** Everything user-facing is upstream third-party and emits no
traces; the only in-house candidate is vollmint, whose `go.mod` has zero OTel dependencies and
which is an API + SPA + database, not a distributed system. "From Istio" is doubly dead: Phase 5
was dropped outright on 2026-08-22.

**The real driver is unrelated to tracing:** `monitoring/promtail` is on chart 6.17.1 and Grafana
has deprecated Promtail in favour of **Alloy**, which is an OpenTelemetry Collector distribution.
The Collector arrives regardless, as a log shipper rather than a trace pipeline.

Re-scoped to:

1. **Drop** OTLP tracing and the Tempo export (recorded as rejected, with reasons, in #1194)
2. **Migrate** Promtail → Alloy, label-for-label, driven by upstream deprecation (**issue #1197**)
3. **Defer** Hubble flow export until Phase 8a (#1190) lands. There is nothing to receive until then

### 2.5 VictoriaMetrics — Long-Term Metrics Store

**Status:** `done` — PRs #812 (design spec), #831, #837

VictoriaMetrics single-node (`victoria-metrics-single`) deployed in the `monitoring` namespace as the long-term metrics store.

- kube-prometheus-stack `remote_write`s into VM; Prometheus local retention trimmed to 24h while VM holds long-term history
- Grafana's `prometheus` datasource UID points at VM, so existing dashboards query the long-term store transparently
- VM self-metrics scraped via ServiceMonitor (PR #837)

---

## Phase 2.6 — Flux Upgrade (v2.4 → v2.8)

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

**Status:** `superseded` by Phase 8b (issue #1191)

Superseded 2026-08-22. The router-side BGP configuration is the hard part and is identical whether
the peer is MetalLB or Cilium, so migrating MetalLB to BGP and then deleting MetalLB is wasted
work. The problem statement below is still accurate and is carried into #1191.

**Problem:** k8sworker04 shows the MetalLB VIP (ingress-nginx LoadBalancer IP) in the UDM console instead of its actual node IP. In L2 mode, MetalLB answers ARP for VIPs from whichever node is the current leader; the UDM sees this ARP and maps that node's MAC to the VIP address, shadowing the real node IP.

**Fix:** Switch MetalLB from L2 advertisement to BGP peering with the UDM Pro. MetalLB advertises VIP routes over BGP; the router learns them as routes (not ARP entries) and routes VIP traffic at L3. Node IPs are unaffected. Also enables ECMP across multiple nodes for better load distribution.

**Note on Cilium overlap:** Cilium (Phase 8) has native BGP support (`CiliumBGPPeeringPolicy`) and a built-in L4LB that can replace MetalLB entirely. If Phase 8 is imminent, it may be cleaner to skip this and migrate BGP as part of the Cilium rollout. Decide at the start of Phase 8 planning.

---

### 3.3 Personal Media Services — External Access

**Status:** `done`, then `superseded` — **Plex was removed from the cluster on 2026-05-09** (commit
`f02501f8`, "remove plex from cluster — migrating to Jellyfin"). This section described a running
Plex deployment for roughly 3.5 months after it was deleted. Corrected 2026-08-22.

Historical record of the Plex era, kept because the tunnel pattern it established is still in use:

- Plex was migrated from TrueNAS into `mediastack` (PRs #439, #442), using the existing SMB CSI
  mounts (`pvc-movies`, `pvc-tv`) plus a 20Gi Longhorn PVC for config/metadata.
- `cloudflared` ran as a plain Deployment in `mediastack` (PR #440). Outbound-only tunnel, no open
  ports. **This pattern survived Plex** and is now used by `cloudflared-jellyfin`,
  `cloudflared-audiobookshelf`, `cloudflared-nginx` and `cloudflared-authentik`.

Current state: **Jellyfin is the only media server** (3.4). Overseerr was replaced by Seerr
(PR #494), which has Authentik OIDC SSO and forward-auth (PR #600).

### 3.4 Jellyfin — Free External Streaming for Friends

**Status:** `done`

- Jellyfin deployed in `mediastack`. Official `jellyfin/jellyfin` chart v3.2.0. Originally deployed
  alongside Plex; **sole media server since Plex was removed 2026-05-09** (see 3.3).
- Uses the `pvc-movies` and `pvc-tv` SMB RWX mounts (read-only access, UID/GID 568).
- Dedicated `pvc-jellyfin-config` 20Gi Longhorn RWO.
- Separate `cloudflared-jellyfin` Deployment with its own tunnel, originally for blast-radius
  independence from Plex; now one of four independent tunnels.
- Route: `jellyfin.vollminlab.com → http://jellyfin.mediastack.svc.cluster.local:8096`.
- Security gate: Jellyfin built-in auth only. No Cloudflare Access policy (native apps cannot complete browser auth challenge). Public signup disabled; accounts managed manually.
- Hardware transcoding deferred, CPU only. See roadmap for follow-up.

**Deferred follow-ups:**

- Hardware transcoding (`/dev/dri` device mount): requires evaluating Kyverno `hostPath` audit policy impact
- Jellyfin metrics / Grafana dashboard: **superseded by Jellystat** (3.5)

### 3.5 Jellystat — Media Play Metrics (was: Tautulli / Plex)

**Status:** `done`

**Jellystat** is the live service: Jellyfin-native play history and stats, CNPG-backed PostgreSQL
(`jellystat-db`), Homepage widget integration (PR #526, 2026-05-12).

Tautulli was the original implementation, deployed against Plex, and was removed by the same PR.
Section retitled 2026-08-22; the old heading named a service that has not run here since May.

---

### 3.6 Harbor Network Isolation — LoadBalancer Expose

**Status:** `done` — PR #591

Harbor migrated to `expose.type: loadBalancer` with dedicated MetalLB VIP `192.168.152.245`. Harbor's own nginx handles TLS via cert-manager wildcard cert. The `arc-runners-egress` NetworkPolicy uses `ipBlock: 192.168.152.245/32` for genuine Harbor-specific isolation. external-dns manages the `harbor.vollminlab.com` A record automatically. GHA robot accounts managed via tofu (`terraform/harbor/`); post-migration issue with robot import blocks resolved in PR #612.

---

### 3.7 Reloader (Stakater)

**Status:** `done` — this PR

Stakater Reloader deployed in `reloader` namespace, watching all namespaces. Resources opt in via `reloader.stakater.com/auto: "true"` annotation on Deployments, StatefulSets, and DaemonSets. ConfigMap or Secret changes trigger automatic rolling restarts without manual
`kubectl rollout restart`. (Originally written as "SealedSecret". The SealedSecrets controller was
removed 2026-05-31 and all Secrets are now ESO-materialized from 1Password. See 3.8.)

---

### 3.8 external-secrets + 1Password Connect

**Status:** `done` — PRs #818–#830

Replaced the SealedSecrets workflow entirely. External Secrets Operator syncs 1Password vault items directly into Kubernetes Secrets and rotates them automatically when the source changes, with no `kubeseal` step.

- 1Password Connect server deployed in the `1password` namespace; External Secrets Operator in `external-secrets`
- `ClusterSecretStore` backed by 1Password Connect; all secrets migrated to `ExternalSecret` CRs incrementally
- `sealed-secrets` controller **removed 2026-05-31** once all secrets were migrated; cluster is fully on ESO + 1Password Connect
- Vault item names and field labels are now cluster infrastructure. See the 1Password naming rules in `secrets.md`

---

### 3.9 Trivy Operator

**Status:** `done` — PR #721

Trivy Operator deployed in `trivy-system` namespace alongside Goldilocks. Scans all running workloads continuously; generates `VulnerabilityReport` and `ConfigAuditReport` CRs. DMZ and control-plane node tolerations added (PR #722). MinIO concurrent scan jobs throttled to prevent timestamp ordering issues (PR #724).

---

### 3.10 Tailscale

**Status:** `done` — PR #740 (2026-05-24). Corrected 2026-08-22; this read `planned` for three
months after it shipped.

Tailscale Kubernetes operator deployed, providing private WireGuard-based access to cluster
services from any tailnet device: non-HTTP protocols, SSH to nodes, and access during a
Cloudflare outage.

Live components:

- `tailscale/tailscale-operator` — the operator itself
- `tailscale-connector/app` — subnet router (`connector.yaml` is the active path)
- `ingress-nginx/tailscale-svc` — service exposed into the tailnet
- `tofu/tailscale-config` — ACLs and tailnet configuration as IaC

---

### 3.11 East-West Network Policies (Non-DMZ)

**Status:** `in-progress` — tracked as issue **#795**. Measured 2026-08-20: default-deny present in
**10 of 37 namespaces**. Related: **#796** (Pod Security Standards, enforce in 18 of 37).

Both issues were closed once while this document still said PARTIAL, and were reopened 2026-08-20.
Do not close either without re-measuring.

All namespaces outside the DMZ are fully open east-west. A compromised pod in `mediastack` can reach Harbor, CNPG, MinIO, or Authentik. Minimum scope:

- `harbor` — restrict inbound to ingress-nginx VIP + ARC runner egress only
- `mediastack` — restrict outbound to known upstreams (indexers, Usenet, download targets)
- `monitoring` — restrict write access (Prometheus scrape sources only)

**Note:** Harbor's dedicated LoadBalancer VIP (3.6) makes its network policy straightforward, using `ipBlock: 192.168.152.245/32` instead of the shared nginx VIP. Sequence 3.11 after 3.6 completes.

---

## Phase 4 — Infrastructure Diagrams

**Goal:** Create living architecture diagrams for every repo in the org once observability and
security are settled, so diagrams reflect a stable system and don't need immediate revision.

**Met, by a different route.** See 4.0. Diagrams are inline in the docs they explain rather than in
per-repo `diagrams/` folders.

### 4.0 Diagram Creation — All Repos

**Status:** `done` — **delivered in a different shape than specified below.** Measured 2026-08-22:
**all 14 org repos carry mermaid diagrams, 22 in total**, as inline ```` ```mermaid ```` fences in
markdown rather than standalone `.mmd` files in a `diagrams/` directory.

| Repo | Diagrams | | Repo | Diagrams |
| --- | ---: | --- | --- | ---: |
| k8s-vollminlab-cluster | 10 | | homelab-obsidian-vault | 10 |
| longhorn-rebalancing-controller | 2 | | ansible-playbooks | 1 |
| dot-github | 1 | | github-admin | 1 |
| groupme_exporter | 1 | | homelab-infrastructure | 1 |
| masters-league | 1 | | pihole-flask-api | 1 |
| shlink-ingress-controller | 1 | | VMDeployTools | 1 |
| vollmint | 1 | | | |

In this repo they live in `README.md`, six runbooks (`kyverno-recovery`,
`longhorn-ext4-corruption`, `etcd-local-nvme-migration`, `harbor-dockerhub-proxy-cache`,
`ci-runner-breakglass`, `expose-dmz-service`) and two design specs.

**The inline placement is better than the `diagrams/` folder this section originally specified, and
the plan should be considered improved on rather than partially met.** A diagram sitting beside the
runbook it explains gets revised in the same PR as the procedure; a diagram in a separate top-level
folder has no such forcing function and rots quietly, which is precisely how the rest of this
document drifted. Mermaid still renders natively in GitHub and previews in VS Code, so nothing is
lost by not having discrete files.

**Maintenance rule going forward:** a new runbook or design doc that describes a flow gets its
diagram in the same file, in the same PR. Do not create per-repo `diagrams/` directories.

Create a `diagrams/` folder in each repo with declarative Mermaid diagrams covering the full system as it exists at that point. Scope:

- `k8s-vollminlab-cluster` — cluster topology (nodes, namespaces, networking), Flux reconciliation flow, storage layout (Longhorn, MinIO), backup data path (Velero → B2), DMZ isolation
- `homelab-infrastructure` — Terraform resource graph, network topology, VM/node inventory
- `github-admin` — repo/branch protection structure
- Any other repos as they exist

**Format:** `.mmd` files (Mermaid: declarative, committable to Git, rendered natively in GitHub PRs/issues, previewable in VS Code with the Mermaid extension). Matches the declarative/GitOps ethos of the cluster. Diagram types: `graph TD` for topology, `flowchart` for data flows, `sequenceDiagram` for reconciliation flows.

**Maintenance:** diagrams live in `<repo>/diagrams/` and are updated as the system changes.

---

## Phase 5 — Service Mesh (dropped)

### 5.1 Istio

**Status:** `dropped` — decided 2026-08-22. Not deferred, and not pending a Phase 8 evaluation.

This is a single-tenant homelab with roughly 30 services and one human. Cluster-wide mTLS and
traffic management carry a large, permanent operational cost against a threat model where
east-west traffic is not the live risk, and sidecar injection would interact with Kyverno policies
on a fail-closed webhook.

**The actual east-west gap is #795** — default-deny NetworkPolicy exists in only 10 of 37
namespaces. That is the real exposure, it is far cheaper to close, and a service mesh would have
sat on top of a flat network rather than fixing it.

Planned and not being built: Helm-based Istio install, mTLS between all services, weighted routing
and circuit breaking, Kiali topology visualization, OTLP tracing to Grafana Tempo. The tracing half
is separately dead — see 2.4 and issue #1194, there is nothing instrumented to trace.

**If a service mesh is ever wanted again**, evaluate Cilium Mesh (Phase 8a, #1190) rather than
reopening this. It ships mTLS, traffic management and Hubble L7 observability on the CNI that is
already planned, so it carries none of Istio's sidecar cost. That evaluation is not scheduled and
gates nothing.

---

## Phase 6 — SRE Practice

### 6.1 SLOTH — SLO-based Alerting

**Status:** `planned` — value is **skill-building, not operational**, and that is a legitimate
reason here given the roadmap's stated upskilling goal. Worth being explicit that error budgets over
single-user traffic will not drive real decisions.

**Sequence after #1185 (black-box probing).** SLOs need an availability SLI, and there is currently
no signal anywhere in the cluster that measures whether a service actually answers. Every one of
the 62 alert rules reads Kubernetes state instead. SLOTH before #1185 would generate burn-rate
alerts over metrics that cannot observe an outage.

Use SLOTH to generate SLO alert rules from a declarative YAML spec:

- Define SLIs/SLOs for key services (ingress latency, Shlink availability, etc.)
- SLOTH generates Prometheus recording rules + multi-burn-rate alerts
- Dashboards in Grafana

### 6.2 Chaos Mesh

**Status:** `deferred`

Controlled fault injection for resilience testing (pod kill, network partition, CPU/memory stress). No immediate plans; incidents are being handled well without it. Revisit after SLOs (6.1) are established so there are clear baselines to validate against.

---

## Phase 7 — Node Maintenance Window

**Status:** `in-progress`
**Risk:** Medium. Rolling node reboots; cluster stays available if done one node at a time

Normalize all nodes to current versions before the CNI migration. Three sub-items sequenced to allow bundling reboots efficiently.

**Current state (2026-08-22, verified all 9 nodes via live `kubectl get nodes`):**

K8s, kernel, containerd **and Ubuntu patch level are now uniform cluster-wide**. The control-plane
userspace drift noted in the 2026-06-12 pass is closed. The only remaining Phase 7 work is the
Kubernetes version itself (7.1, issue **#1193**).

| Node | K8s | Kernel | Ubuntu | containerd |
| --- | --- | --- | --- | --- |
| k8scp01 | v1.34.8 | 6.8.0-137 | 24.04.4 | 2.2.4 |
| k8scp02 | v1.34.8 | 6.8.0-137 | 24.04.4 | 2.2.4 |
| k8scp03 | v1.34.8 | 6.8.0-137 | 24.04.4 | 2.2.4 |
| k8sworker01 | v1.34.8 | 6.8.0-137 | 24.04.4 | 2.2.4 |
| k8sworker02 | v1.34.8 | 6.8.0-137 | 24.04.4 | 2.2.4 |
| k8sworker03 | v1.34.8 | 6.8.0-137 | 24.04.4 | 2.2.4 |
| k8sworker04 | v1.34.8 | 6.8.0-137 | 24.04.4 | 2.2.4 |
| k8sworker05 | v1.34.8 | 6.8.0-137 | 24.04.4 | 2.2.4 |
| k8sworker06 | v1.34.8 | 6.8.0-137 | 24.04.4 | 2.2.4 |

### 7.1 Kubernetes upgrade (1.32 → current stable)

**Status:** `in-progress` — tracked as issue **#1193**

Upgrade all nodes through four minor-version hops. K8s 1.33 goes EOL 2026-06-28; the cluster is
already off it (on 1.34.8), so that deadline is cleared. Hops 3–4 remain.

**Measured 2026-08-22:** all 9 nodes on **v1.34.8**; latest upstream release is **v1.36.4**
(v1.35.8 and a v1.34.11 patch also available). The cluster is two minors behind, still inside the
N-2 support window, but 1.34 leaves support when 1.37 ships. **Do this before Phase 8a (#1190)**:
8a's acceptance includes revalidating all 19 NetworkPolicy files, which is worth doing once against
the final Kubernetes version.

| Hop | Target | Method | Status |
| --- | --- | --- | --- |
| 1 | 1.33.12 | Manual node-by-node | `done` — 2026-05-19 |
| 2 | 1.34.8 | Ansible `k8s-upgrade.yml` | `done` — all 9 nodes |
| 3 | 1.35.x | Ansible `k8s-upgrade.yml` | `planned` — target available (v1.35.8) |
| 4 | 1.36.x | Ansible `k8s-upgrade.yml` | `planned` — target available (v1.36.4) |

Playbook hardening from hop 1: `serial: 1`, `--disable-eviction` on all drain commands, Longhorn volume health gate after each uncordon (waits for zero degraded volumes before proceeding to next node).

**Compatibility note:** K8s 1.33 introduced `ServiceCIDR`/`IPAddress` networking types that Kyverno's catch-all webhook intercepts and rejects (HTTP 400, not a timeout, so `failurePolicy: Ignore` has no effect). Fixed via `matchConditions` CEL expressions + `resourceFilters` entries in the Kyverno ConfigMap (PR #630). Verify this fix is present and survives each subsequent hop.

### 7.2 containerd normalization

**Status:** `done` (2026-05-30)

All 9 nodes are on containerd **v2.2.4** (docker.com apt repo). The 6 workers were upgraded during the 2026-05-30 maintenance; the 3 control-plane nodes (k8scp01–03, previously on 1.7.27) were upgraded via the `cp-containerd-upgrade.yml` playbook in `ansible-playbooks` (PR #11). The control-plane upgrade was serial:1 with etcd endpoint-health gates before and after each node; etcd/controller-manager/scheduler bind addresses and the Harbor pull-through mirror config (`config_path = certs.d`) were verified unchanged afterward.

### 7.3 Kernel and OS normalization

**Status:** `done` — verified 2026-08-22

> **Verified 2026-08-22, live `kubectl get nodes` across all 9 nodes:** Ubuntu **24.04.4**, kernel
> **6.8.0-137-generic**, containerd **v2.2.4**, uniform everywhere. The control-plane userspace
> drift recorded below (24.04.1 / 24.04.2) is closed, and the kernel has moved on from the
> 6.8.0-117 this document previously recorded.

> *Superseded status (2026-06-12):* kernel uniform at 6.8.0-117-generic, containerd uniform at
> v2.2.4, with the three control-plane nodes lagging at 24.04.1 / 24.04.2 against the workers'
> 24.04.4.

- One node at a time; same Longhorn health gate as the other node-maintenance playbooks

> **Playbook consolidation (proposed):** `ansible-playbooks` now has three node-maintenance playbooks that share the same drain → act → verify → gate → uncordon skeleton: `k8s-upgrade.yml` (kubeadm/kubelet, runs kubeadm so it re-binds CP metrics), `containerd-dockerhub-mirror.yml`, and `cp-containerd-upgrade.yml`. These should be unified: either a single parameterized node-maintenance playbook (with `kubeadm`, `containerd`, `apt`/kernel toggles) covering all 9 nodes with one wait-logic implementation, or a shared task-include the others import. This kernel/userspace pass is a good candidate to fold into that consolidation rather than write a fourth one-off.

Do not bundle 7.2/7.3 with the Cilium migration. Use separate maintenance windows.

---

## Phase 8 — CNI Migration (Calico → Cilium)

**Status:** `planned` — **split into 8a / 8b / 8c on 2026-08-22.** As originally written this phase
bundled three separately-risky changes into one window, and two of them are blocked on design work
rather than migration work.

| Sub-phase | Scope | Issue | State |
| --- | --- | --- | --- |
| **8a** | Cilium CNI only — keep MetalLB, keep nginx | **#1190** | executable now |
| **8b** | Cilium L4LB + BGP, drop MetalLB (supersedes 3.2) | **#1191** | blocked on UDM router config |
| **8c** | Gateway API, decommission nginx-ingress | **#1192** | blocked — see below |

**Two blockers 8c must clear before any `HTTPRoute` migration:**

1. **Gateway API has no forward-auth, and 19 manifests carry `auth-url` annotations.** The entire
   SSO model (one `forward_domain` ProxyProvider plus nginx `auth_request` plus the mandatory
   `auth-snippet` `X-Forwarded-Host` header) is nginx-specific. Decommissioning nginx logs every
   forward-auth service out permanently.
2. **`shlink-ingress-controller` only reconciles `Ingress`** (`cmd/main.go:66`), and **27 manifests
   carry `shlink.vollminlab.com/slug`**. Migrating to `HTTPRoute` does not error. Short-link
   creation just silently stops.

**Dependencies (re-verified 2026-08-22):**

- ~~1.1 test restore~~ ✓ validated (Minecraft restore)
- Phase 2 observability ✓ done
- Phase 7 node maintenance: **7.2 and 7.3 are done; all 9 nodes measured uniform** at Ubuntu
  24.04.4 / kernel 6.8.0-137 / containerd 2.2.4. Only 7.1 (K8s hops 3–4, issue **#1193**) remains,
  and that should land before 8a.

**Risk:** High. CNI replacement requires a full cluster maintenance window

Cilium offers significant advantages over Calico for this use case:

- **Hubble** — built-in L4/L7 network observability (flows, DNS, HTTP)
- eBPF-native (better performance, richer policy)
- Native Gateway API support
- Industry direction for SRE/platform engineering roles

**Expanded scope. Cilium enables a full networking stack simplification:**

- **CNI replacement:** Calico → Cilium (eBPF-native, richer policy, better performance)
- **MetalLB replacement:** Cilium L4LB + BGP peering replaces MetalLB entirely. Pair with UDM BGP configuration (see the 3.2 note: skip the standalone MetalLB BGP migration and do it here instead)
- **Gateway API adoption:** Cilium's native Gateway API implementation replaces nginx-ingress. All `ingress.yaml` files migrate to `HTTPRoute` resources. nginx-ingress is decommissioned at the end of this phase.
- **kube-proxy replacement (optional):** eBPF-based routing; evaluate during planning
- **Hubble:** L4/L7 network observability (flows, DNS, HTTP); feeds the Phase 2.4 OpenTelemetry pipeline
- **Istio:** already dropped (Phase 5, 2026-08-22), so this phase has no mesh decision left to make. If a mesh is ever wanted, evaluate Cilium Mesh on its own merits then.

**The detail below is retained as the original plan. The authoritative, corrected scope now lives in
#1190 / #1191 / #1192.**

Migration approach:

1. ~~Confirm Velero backups are healthy and a test restore has been validated~~ done (Minecraft restore)
2. Confirm Phase 2 observability is in place (Prometheus + Loki at minimum)
3. Confirm all nodes are on current, normalized versions (Phase 7)
4. Plan a dedicated maintenance window. CNI replacement and nginx-ingress migration are both disruptive
5. Drain nodes, uninstall Calico, install Cilium
6. Validate network policies and DMZ rules (especially DMZ namespace on k8sworker05)
7. Migrate all Ingress resources to HTTPRoute; validate each service
8. Decommission MetalLB and nginx-ingress HelmReleases
9. Update `bootstrap/calico/` → `bootstrap/cilium/` references

This is a cluster rebuild risk event. Do not attempt without working backups.

---

## Deferred / Under Evaluation

| Item | Notes |
| --- | --- |
| Dynatrace / Dash0 | Homegrown stack (Prometheus + Loki + Grafana) is now established — evaluate if a managed platform adds value |
| Tekton | Not needed for dependency updates (Renovate covers that); revisit if building/pushing custom images |
| Crossplane | Potential future IaC-as-Kubernetes for cloud resources — redundant with tofu-controller today |

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
| external-secrets + 1Password Connect | PRs #818–#830 — ESO + 1Password Connect replaced SealedSecrets entirely; `sealed-secrets` controller removed 2026-05-31 |
| VictoriaMetrics long-term metrics store | PRs #812/#831/#837 — `victoria-metrics-single` in `monitoring`; Prometheus remote_write + 24h retention, Grafana datasource swap, self-metrics ServiceMonitor |
| K8s upgrade hop 2 (1.33.12 → 1.34.8) | All 9 nodes upgraded via hardened Ansible `k8s-upgrade.yml` (`serial:1`, `--disable-eviction`, Longhorn health gate); cleared the 1.33 EOL deadline |
| Foundry VTT | Deployed to `foundry` namespace, `category: gaming`, 10Gi Longhorn PVC. Authentik forward-auth (domain-wide provider, blank per-world Foundry passwords). Exposed via the shared `nginx` Cloudflare tunnel. Backup via VolSync clone-based restic to B2 follows in a separate PR. |
| **— Backfilled 2026-08-22 —** | *The rows below were shipped but never recorded here. Found by diffing the 106 live app directories against this document; 41 had no mention at all.* |
| Authentik SSO + forward-auth architecture | Domain-wide `vollminlab-forward-auth` ProxyProvider on the `vollminlab-proxy` outpost; per-app Applications; native OIDC for Grafana, Harbor, Headlamp, Jellyfin, MinIO, Portainer, Audiobookshelf. Config is tofu-managed (`tofu/authentik-config`). Rules: `.claude/rules/authentik-akshell.md` |
| Authentik recovery links | Self-service recovery link issuance via the Authentik REST API (PR #1065). Uses the API rather than `ak shell` because the flow needs a `FlowToken` carrying a pickled plan — **and because there is no SMTP path at all** (issue #1186) |
| karma | Alertmanager dashboard UI in `monitoring` (PR #980) |
| Policy Reporter | Kyverno policy report UI + metrics in `kyverno` namespace |
| Descheduler | `LowNodeUtilization` rebalancing (PRs #999, #1000, #1031). Classifies on **requests**, not usage; short-lived CronJobs need `backoffLimit > 0` |
| longhorn-rebalancing-controller | In-house Go controller, `longhorn-system` (PR #881, now v0.4.0). Convergence verified 2026-07-25: peak node utilization 90.8% → 74.0% |
| Longhorn trim CronJob | Daily filesystem trim to reclaim thin-provisioned space (PR #869). Longhorn never auto-trims. Was silently trimming **zero** volumes after the 1.12.1 NetworkPolicy change — fixed in #1086 |
| Longhorn snapshot retention | `snapshot-delete` RecurringJob capping VolSync clone orphans (PR #931) |
| longhorn-mount-healer | `kube-system` CronJob auto-clearing Longhorn stale-mount crashloops |
| velero-pvb-healer | `velero` CronJob healing the node-agent datapath-slot leak that freezes PVBs in `Prepared` (PRs #1054, #1055; OOM fix #1056). **Was 100% non-functional — exit 137 on every run — while Flux reported success** |
| velero-backup-content-guard | Alerts when a schedule captures nothing or stops running (PR #1079). Exists because an empty Velero backup reports `Completed / 0 errors` |
| VictoriaMetrics cold tier | `victoria-metrics-lt`, 395d retention, 750Gi, off-Longhorn on `pool_0` (PR #923). Backed up by its own `vmbackup` CronJob to a **dedicated** B2 bucket — sharing Velero's bucket root killed all three B2 schedules on 2026-08-17 |
| CNPG observability | PodMonitors + dashboards for all CNPG clusters (PR #1012). Gotcha: `podMonitorSelector: {}` |
| vcenter-credential-age | CronJob warning before the vCenter metrics password expires (PR #1102). Built **after** a silent 90-day SSO expiry took out vmware-exporter — see issue #1188 on generalizing this |
| vollmint | In-house budgeting app (Go API + SPA + CNPG DB + SimpleFIN sync CronJob), own namespace, Authentik SSO, default-deny NetworkPolicies |
| Audiobookshelf | Audiobook server in `mediastack`; Authentik OIDC with `authOpenIDAutoRegister: true`, group claim sets the role; own Cloudflare tunnel |
| Shlink web UI | `shlink-web` frontend alongside the Shlink API |
| masters-league | Fantasy golf dashboard in `dmz` (in-house, own repo) |
| kubeadm cert monitor + renew | Automated control-plane certificate expiry monitoring and renewal (PR #540) |
| Harbor registry retention | 8Gi + retention policy (PR #1040). Longhorn schedules against its own ledger, not physical free space |
| Cloudflare Bot Fight Mode fix | `fight_mode = false` pinned in tofu (PR #1172). A WAF skip rule runs in a different phase and can **never** reach Bot Fight Mode |
| Kyverno `-batch` policies | Five Job/CronJob counterparts to the Enforce five, in Audit (PR #1182). Enforce policies match only Deployment/StatefulSet/DaemonSet; autogen derives controller rules from Pod rules, never the reverse |
| tofu-controller modules | 11 Terraform CRs reconciling Authentik, B2, Cloudflare, Grafana, Harbor, MinIO, Tailscale and the four arr apps. **Plan approval cannot go through git** — the plan id embeds the source sha, so every repo commit invalidates it |

---

## Corrections — 2026-08-22 audit

This document was re-measured against the live cluster and repo on 2026-08-22. Six status fields
were wrong; all are corrected in place above, with the evidence, rather than silently overwritten.

| Section | Was | Now | Evidence |
| --- | --- | --- | --- |
| 3.3 Plex | `done`, describing a running Plex | Plex **removed 2026-05-09** (`f02501f8`) | zero grep hits for `plex` in `clusters/` |
| 3.4 Jellyfin | "alongside Plex", "shares mounts with Plex" | sole media server | same |
| 3.5 | titled "Tautulli / Plex Metrics Dashboard" | retitled to Jellystat | Tautulli removed by PR #526, 2026-05-12 |
| 3.7 Reloader | "SealedSecret changes trigger restarts" | Secret | SealedSecrets controller removed 2026-05-31 (3.8) |
| 3.10 Tailscale | `planned` | `done` — PR #740, 2026-05-24 | operator + connector + `tofu/tailscale-config` all live |
| 7.3 | "CP nodes on 24.04.1 / 24.04.2" | all 9 uniform at 24.04.4 | live `kubectl get nodes` |
| 4.0 Diagrams | `planned` | `done`, shape changed | 22 mermaid diagrams across all 14 repos |

**A note on how 4.0 was nearly mis-audited.** The first pass of this audit searched for `.mmd`
files and `diagrams/` directories (the artifacts this section *specifies*), found almost none, and
concluded the phase was not started. It had in fact been delivered in full, as inline mermaid.
Searching for the prescribed mechanism rather than the intended outcome produced a confidently
wrong answer, which is the same class of error as a NetworkPolicy rule naming a service port: the
check runs, reports cleanly, and measures the wrong thing. **When auditing a roadmap item, search
for what the goal would look like if it were met by any means, not for the implementation the plan
happened to name.**

**Two structural problems, not just stale fields:**

1. **41 of 106 live app directories appeared nowhere in this document.** Partially addressed by the
   backfill above. The Completed table had drifted roughly three months behind what was shipped.
2. **Phase 8 bundled three separately-risky changes into one window**, two of which are blocked on
   design work rather than migration work. Split into #1190 / #1191 / #1192.

**How this drifted:** every wrong field described something that was *replaced* rather than
*removed*: Plex by Jellyfin, Tautulli by Jellystat, SealedSecrets by ESO. The replacement got
documented in its own new section while the old section kept its `done`. A `done` status records
that something was built, not that it is still running; sections describing live infrastructure
need re-verifying against the cluster, not just against the PR that shipped them.
