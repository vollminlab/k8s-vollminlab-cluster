# Cluster Security & Improvement Audit

**Date:** 2026-05-29
**Scope:** Full repo (static manifest analysis) + live cluster (read-only `kubectl`)
**Cluster:** 9 nodes (3 control-plane, 6 workers incl. 2 DMZ), 35 namespaces
**Method:** Six parallel read-only audit agents (workload security contexts, network exposure & ingress auth, Kyverno posture, secrets & RBAC, reliability, Flux/supply-chain), followed by manual verification of every HIGH/MEDIUM claim against the live cluster and repo. No cluster mutations were made.

## Important caveats

- **External exposure is partially opaque.** `cloudflared-nginx` runs with a remote-managed config (`tunnel run` + token); which public hostnames Cloudflare routes to the nginx ingress is configured in the Cloudflare dashboard and **cannot be enumerated from the cluster**. Confirmed tunnels exist for: `authentik`, `ingress-nginx` (the `*.vollminlab.com` front door), `audiobookshelf`, `jellyfin`. Severity ratings below assume that anything fronted by nginx *may* be internet-reachable — verify the Cloudflare public-hostname list to confirm.
- This audit reflects cluster state on 2026-05-29 and the `main` branch at that time.

---

## Executive summary

Overall posture is **solid for a homelab**: SealedSecrets-only with gitleaks CI, all ingresses TLS, a genuinely well-built DMZ (default-deny + explicit allows), strong GitOps discipline (no `:latest`, exact version pins, complete Flux indexes, no suspended resources), and a real Kyverno enforce baseline.

The material gaps are **architectural, not "anyone can walk in"**:

1. **No intra-cluster network segmentation** outside the DMZ — 28 of 35 namespaces have zero NetworkPolicy (default-allow lateral movement). *Biggest gap.*
2. **No Pod Security Standards enforcement on app namespaces** — no Kyverno or PSA enforcement of non-root, dropped capabilities, seccomp, or read-only rootfs for application workloads; ~83% of containers ship no explicit securityContext.
3. **Image supply chain is unpinned** — no registry allowlist and only ~17% of images are digest-pinned.

> **Major correction (2026-05-29) — the authentication findings were withdrawn.** The parallel agents (and my first synthesis) flagged Grafana/Headlamp/Portainer/MinIO/Audiobookshelf/Jellyfin as missing authentication, rated up to CRITICAL. **This was wrong.** All of these have dedicated Authentik OIDC providers (verified live via `ak shell` and Grafana's SSO API), and every other app ingress carries forward-auth. The blind spot: **app-level OIDC is provisioned out-of-band via Terraform through each app's API and stored in the app's database**, so it's invisible to manifest/config inspection. As a result **H3, M1, and M2 were all withdrawn as false positives.** The remaining real findings (H1, H2, M3–M7) are infrastructure-level facts verified directly against the cluster. A "DMZ broken labels" agent finding was also a false positive. Full corrections are documented inline and at the end.

---

## HIGH

### H1 — No NetworkPolicy in 28 of 35 namespaces (flat network, lateral movement)
- **Category:** missing_netpol
- **Evidence:** Only `dmz` (full default-deny + allows), `flux-system` (allow-lists), `minio` (console only), `actions-runner-system` (egress only), and `calico-apiserver` have any NetworkPolicy. Namespaces with **zero**: `authentik`, `harbor`, `cnpg-system`, `monitoring`, `portainer`, `sealed-secrets`, `mediastack`, `longhorn-system`, `velero`, `tofu`, `ingress-nginx`, `kyverno`, and ~16 others.
- **Risk:** Kubernetes default is allow-all. Any single compromised pod (e.g. a media app pulling from the internet) can reach every Service in every namespace — Authentik, the CNPG databases, MinIO/S3, the sealing-secrets controller, Longhorn, etc. — enabling secret exfiltration and pivoting. The DMZ is correctly isolated; the *internal* network is wide open.
- **Recommendation:** Roll out a `default-deny` + explicit-allow baseline per namespace, prioritizing the crown jewels: `authentik`, `sealed-secrets`, `cnpg-system`, `minio`, `harbor`, `monitoring`, `portainer`, `tofu`. Template it with a shared kustomize component (allow DNS to kube-dns, allow ingress-nginx → app port, allow needed egress). Start with the sensitive namespaces, then fill in the rest. The DMZ policies are a good internal model.

### H2 — No Pod Security Standards enforcement on application namespaces
- **Category:** policy_gap / missing_coverage
- **Evidence:** Kyverno enforces `restrict-privileged`, `restrict-hostpath`, `require-resources`, `restrict-latest-tag`, `restrict-default-namespace`, `restrict-loadbalancer-services`, `dmz-restrict-external-access`. It does **not** enforce (or even audit) `runAsNonRoot`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`, `readOnlyRootFilesystem`, or `allowPrivilegeEscalation: false`. Pod Security Admission labels exist only for infra exceptions (`calico-system`, `metallb-system`, `tigera-operator` = `privileged`) and **DMZ = `enforce: baseline`** (baseline does *not* require non-root/seccomp/caps). **No application namespace enforces `restricted`.** ~383 of 464 containers (~83%) carry no explicit securityContext; ~28 explicitly set `runAsNonRoot: false`.
- **Risk:** A compromised container is far more likely to be running as root with the full capability set and a writable rootfs, turning an app-level RCE into node-level escalation and persistence.
- **Recommendation (two paths):** *(a, easiest)* set `pod-security.kubernetes.io/enforce` to `baseline` then `restricted` on app namespaces incrementally, starting with `warn`/`audit` to surface violators — no policy authoring, and DMZ already does baseline. *(b)* Kyverno policies Audit→Enforce with infra exclusions: `require-run-as-non-root` first, then `require-drop-all-capabilities` + `require-seccomp-runtime-default`, then `readOnlyRootFilesystem` (needs per-app `emptyDir` for `/tmp`). Mind autogen per `.claude/rules/kyverno.md`.

> _H3 (Portainer cluster-admin) was withdrawn — see the corrected LOW item below and the Executive summary correction note. Portainer authenticates via Authentik OIDC; its cluster-admin SA is inherent to a k8s management UI, as accepted for Headlamp._

---

## MEDIUM

> **M1 & M2 (ingress auth) were WITHDRAWN as false positives — verified 2026-05-29.** A full reconciliation of every ingress against the live Authentik provider list found **no genuinely unprotected sensitive endpoint**. Apps with dedicated Authentik OIDC providers: Audiobookshelf, Grafana, Harbor, Headlamp, Jellyfin, MinIO, Portainer, Seerr. All other app ingresses carry nginx forward-auth (`vollminlab-forward-auth`). The OIDC config is provisioned **out-of-band via Terraform through each app's API (DB-stored)** — e.g. Grafana's live SSO API reports `generic_oauth … source=database … enabled=True … name="Authentik"` — which is why it is invisible to manifest/`grafana.ini` inspection and tripped up the agents (and my first pass). Adding nginx forward-auth on top of these OIDC apps would be **redundant double-auth** and is explicitly discouraged in `.claude/rules/authentik-akshell.md`. The only outlier, `s3.vollminlab.com`, correctly uses S3 access-key (SigV4) auth (see L9). **The cluster's authentication posture is sound and matches its documented design.**

### M3 — No image-registry allowlist and no digest pinning (supply chain)
- **Category:** supply_chain
- **Evidence:** No Kyverno policy restricts source registries; images may be pulled from any registry. Only ~6 of ~35 images are digest-pinned (`@sha256:…`); the rest are tag-only (tags are mutable and can be re-pushed). Registries in use are all reputable (docker.io, ghcr.io, quay.io, harbor.vollminlab.com).
- **Risk:** A compromised or typosquatted registry, or a re-pushed tag, can inject a malicious image at the next reconcile with no integrity check.
- **Recommendation:** Add a `restrict-image-registries` Kyverno policy allowlisting the known registries (Audit → Enforce). Enable Renovate's `pinDigests` to add digest pins automatically, prioritizing infra (Flux, Kyverno, cert-manager, ingress-nginx, CNPG, Velero). Optionally a `require-image-digest` policy later.

### ~~M4~~ — RESOLVED 2026-06-02 (PR #846)
`require-standard-labels` promoted from Audit → Enforce. Zero violations confirmed before promotion. Policy now blocks unlabelled workloads at admission.

### ~~M5~~ — RESOLVED 2026-06-02
All four stuck HelmReleases cleared: bazarr/sonarr rate-limiter errors self-resolved; jellystat self-resolved; velero fixed by upgrading to chart 12.0.2 (PR #844). All HelmReleases `Ready=True` as of 2026-06-02.

### ~~M6~~ — RESOLVED 2026-06-02 (PR #843)
ingress-nginx controller memory raised from 128Mi → 256Mi (request 64Mi → 128Mi). No OOMKills since.

### ~~M7~~ — RESOLVED 2026-06-02 (PRs #847, #848)
SA token automounting disabled cluster-wide via two Kyverno mutate policies:
- `mutate-default-sa-automount`: sets `automountServiceAccountToken: false` on every `default` SA in non-system namespaces (SA-level); all 31 existing SAs patched directly on 2026-06-02
- `mutate-default-sa-pod-automount`: overrides pod-level `true` for pods using the default SA, covering charts that hardcode it (e.g. bazarr)
RBAC audit confirmed zero bindings on any default SA — no legitimate API access disrupted.

---

## LOW

- **L1 — WebSocket/upload path-split ingresses without forward-auth** (`*-signalr`, `*-socketio`, `filebrowser-tus`). This is the **documented, intentional** pattern (`.claude/rules/authentik-akshell.md`: forward-auth can't proxy WebSockets, so a path-split ingress is used). App-layer auth still applies. *Action:* confirm each arr app has its own authentication enabled so the un-forward-auth'd path isn't an open door; otherwise no change.
- **~~L2~~** — RESOLVED 2026-06-02. **False positive.** The "orphaned" Longhorn volumes are VolumeSync restic cache PVCs (`volsync-src-*-restic-cache`) that are intentionally detached when no sync is running. All confirmed bound to live PVs.
- **~~L3~~** — RESOLVED 2026-06-02 (PR #842). `.gitignore` updated; 18 previously untracked plan/spec docs committed to repo.
- **~~L4~~** — RESOLVED 2026-06-02 (PR #850). Root cause: VolumeSync mover pods run at 3am concurrent with Velero's `daily-full`. Velero FSB found these pods and tried to access their temporary `data` PVC after VolumeSync had already deleted it — 36 errors. Fixed by adding `velero.io/exclude-from-backup: "true"` to `moverPodTemplateSpec` on all 13 ReplicationSources.
- **L5 — Flux controllers restart counts.** Still open. Current rate: 2–4 restarts per pod over 3 days (similar rate to audit). *Action:* monitor; investigate if rate increases.
- **L6 — `longhorn-support-bundle` SA has cluster-admin.** Still open. *Action:* scope to read-only on pods/nodes/PV(C)/events if feasible.
- **~~L7~~** — RESOLVED 2026-06-02 (PR #849). gluetun: 128Mi → 256Mi; bazarr-exportarr: 64Mi → 128Mi.
- **~~L8~~** — RESOLVED 2026-06-02 (PR #845). Portainer `hide_internal_auth = true` set via tofu. Internal login form no longer shown; Authentik OIDC is the only authentication path.
- **~~L9~~** — RESOLVED 2026-06-02. Verified: zero anonymous bucket policies on all four MinIO buckets (velero, loki, cnpg-backups, terraform-state).

---

## Accepted / expected (NOT findings — documented to prevent re-flagging)

- **`gluetun` NET_ADMIN capability** — required for the qBittorrent VPN sidecar; already scoped with `allowPrivilegeEscalation: false`.
- **Privileged/hostPath/hostNetwork infra** — calico, longhorn, metallb, velero node-agent, tigera. Covered by Kyverno PolicyExceptions; expected for CNI/CSI/storage/LB/backup.
- **cluster-admin for Flux controllers and Velero** — required for GitOps reconciliation and cluster-wide backup/restore.
- **Headlamp cluster-admin for `oidc:scottvollmin@gmail.com`** — owner access via OIDC; ensure MFA on the Google account.
- **Shlink public shortlinks** (`go.vollminlab.com`, `vollm.in`) and **Authentik's own ingress** — intentionally unauthenticated by design.
- **Positives worth keeping:** **comprehensive authentication** — every sensitive ingress is gated by either a dedicated Authentik OIDC provider or the domain-wide forward-auth, consistent with the documented design; all ingresses TLS; zero plain `kind: Secret` (SealedSecrets only) + gitleaks CI; complete Flux indexes, exact version pins, no `:latest`, no suspended resources; Kyverno autogen-safety annotations correctly applied; **excellent DMZ default-deny segmentation + PSA baseline**; `wildcard-cert` healthy and auto-renewing.

## Corrected agent over-flags (false positives caught in verification)

- **"CRITICAL unprotected Grafana/Headlamp/Portainer/MinIO" + "Portainer cluster-admin (HIGH)"** → **fully withdrawn** (H3/M1/M2). Every one of these apps has a dedicated Authentik OIDC provider (verified live), provisioned out-of-band via Terraform/app-API (DB-stored) and therefore invisible to manifest inspection. The auth posture matches the documented design; there was no open access and no doc drift. *Lesson: in this cluster, app-level OIDC and PSA live outside the manifests — always check the live Authentik provider list and namespace PSA labels, not just YAML.*
- **"DMZ pods missing labels — likely breaking functionality"** → **false positive.** All 4 DMZ pods are `Running` (7d19h) using dedicated app-specific NetworkPolicies (masters-league, masters-redis, minecraft) rather than the generic label-based allow policies. Nothing is broken.
- **Per-app "missing securityContext" HIGHs** (authentik, audiobookshelf, etc.) → folded into the systematic PSS gap (H2) rather than counted individually.
- **"Wildcard cert expiring in 53 days"** → non-issue; `Ready=True`, cert-manager auto-renews at the 24-day mark.

---

## Remaining open items (as of 2026-06-02)

| ID | Severity | GitHub Issue | Status |
|----|----------|-------------|--------|
| H1 | HIGH | #795 | Open — multi-PR effort |
| H2 | HIGH | #796 | Open — multi-PR effort |
| M3 | MEDIUM | #800 | Open |
| L5 | LOW | — | Open — monitor |
| L6 | LOW | — | Open — low priority |

## Suggested remediation order

1. **H1** — default-deny NetworkPolicies for the sensitive namespaces first (authentik, cnpg-system, minio, harbor, monitoring, portainer, tofu). *(biggest gap, GitHub #795)*
2. **H2** — PSS on app namespaces (PSA labels warn→audit→enforce, or Kyverno non-root first). *(GitHub #796)*
3. **M3** — registry allowlist + Renovate digest pinning. *(GitHub #800)*
4. **L6** — scope longhorn-support-bundle SA down from cluster-admin.
