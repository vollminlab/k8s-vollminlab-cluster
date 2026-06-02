# 1Password + External Secrets Operator — Design Spec

**Created:** 2026-05-26  
**Status:** Draft — post-audit revision  
**Scope:** Full migration of all SealedSecrets to ESO-managed ExternalSecrets backed by 1Password Connect

---

## 1. Problem Statement

The current SealedSecrets workflow has three compounding pain points:

1. **Rotation friction.** Rotating any credential requires re-sealing and re-committing a file. There is no automated path from a 1Password update to a live K8s Secret update.
2. **DR complexity.** Cluster rebuild requires restoring the sealing key from 1Password before Flux can decrypt anything. The sealing key restore is a manual, error-prone step with stale runbook risk.
3. **Drift and no audit trail.** There is no way to verify that a SealedSecret in git matches the current value in 1Password. Credential drift is invisible until an app breaks.

---

## 2. Goals

- All K8s Secrets sourced from 1Password, synced automatically
- Secret rotation in 1Password propagates to cluster within the configured refresh interval, triggering automatic pod restarts via the existing Reloader deployment
- DR procedure reduces to two manual bootstrap commands followed by full automated reconciliation
- Zero SealedSecrets remaining after full migration (including removing the sealed-secrets controller)

---

## 3. Architecture

### 3.1 Component Overview

| Component | Namespace | Kind | Chart / Source |
|---|---|---|---|
| 1Password Connect Server | `1password` | HelmRelease | `1password/connect` |
| External Secrets Operator | `external-secrets` | HelmRelease | `external-secrets/external-secrets` |
| ClusterSecretStore | cluster-scoped | CR | raw manifest, deployed via `1password` namespace kustomization |

Both `1password` and `external-secrets` are new namespaces following existing cluster conventions: HelmRelease + configmap.yaml + kustomization.yaml, required labels (`app`, `env: production`, `category: security`), no inline values.

**Note on Kyverno compliance (pre-implementation required check):** Both charts create pods. Kyverno enforce-mode requires `app`, `env`, `category` labels and CPU/memory limits on every pod. The chart values must be verified to expose pod label and resource limit overrides before the HelmReleases are written. If the charts do not support this, a Kyverno policy exception must be evaluated before deployment.

### 3.2 Data Flow

```
1Password.com (cloud)
       ↑  HTTPS :443
1password-connect  (1password ns)
  ├── credentials.json + ESO token Secret  ← manually bootstrapped during DR
  └── PVC: 1Gi Longhorn (local cache — serves existing secrets if cloud briefly unreachable)
       ↑  HTTP :8080 (cluster-internal)
ClusterSecretStore  (name: onepassword-cluster-store)
  └── connectTokenSecretRef → namespace: 1password / secret: onepassword-connect-token
       ↑  resolves ExternalSecret CRs
ESO operator  (external-secrets ns)
       ↑
All app namespaces → ExternalSecret CRs → K8s Secrets → pods
                                                          ↓
                                               Reloader detects Secret changes → restarts annotated pods
```

### 3.3 Why ClusterSecretStore (not per-namespace SecretStore)

Per-namespace `SecretStore` is the right choice for multi-tenant platforms where different teams have namespace-scoped `kubectl apply` access and you need to prevent tenant A from pulling tenant B's secrets via the Kubernetes API.

This cluster is single-operator, GitOps-gated. No pod or human can create an `ExternalSecret` without a PR through branch protection. The effective security boundary is git, not the store scope. A `ClusterSecretStore` backed by a single read-only ESO access token (Homelab vault) is the architecturally correct choice: fewer objects, simpler mental model, no per-namespace store maintenance overhead.

### 3.4 1Password Connect — availability characteristics

Connect runs as a single replica with a 1Gi Longhorn PVC acting as a local cache. If 1Password's cloud API is temporarily unreachable, Connect serves cached data and ESO continues syncing from the cache. If the Connect pod itself is down, existing K8s Secrets continue working — apps are not affected. Only new secret syncs and rotation propagation are blocked until Connect recovers. This is acceptable for a single-operator homelab.

Deployment must use `strategy: Recreate` (RWO PVC — standard cluster rule per `storage.md`).

### 3.5 NetworkPolicy Requirements

The `1password` and `external-secrets` namespaces require explicit NetworkPolicy rules:

**`1password` namespace:**
- Egress to `1password.com:443` (HTTPS to 1Password cloud API) — **required for Connect to function**
- Ingress from `external-secrets` namespace on port `8080` (ESO → Connect)

**`external-secrets` namespace:**
- Egress to `1password` namespace port `8080` (ESO operator → Connect)
- Egress to Kubernetes API server (for watching ExternalSecret CRs — API server traffic, not pod-to-pod)

Confirm that cluster egress to `1password.com:443` is not filtered at the network level before Phase 0. The existing workloads (Cloudflare tunnels, Velero B2 backups) confirm outbound HTTPS is open cluster-wide, but explicitly verify this for the `1password` namespace.

**DMZ note:** ExternalSecrets in the `dmz` namespace are resolved by the ESO operator pod (which runs in `external-secrets`, not in DMZ). No DMZ NetworkPolicy changes are required for ESO sync.

### 3.6 ClusterSecretStore — namespace placement

The `ClusterSecretStore` is a cluster-scoped resource. Its manifest lives in the `1password-connect` app directory and is deployed via the `1password` namespace kustomization. Ensure that kustomization does **not** apply a `namespace:` override to cluster-scoped resources — doing so causes a Kubernetes API error. If the kustomization applies a namespace override globally, the `ClusterSecretStore` manifest must be in a separate kustomization that does not set namespace.

---

## 4. Connect Server and ESO Token Provisioning

### 4.1 1Password Terraform Provider Limitation

The `1Password/onepassword` Terraform provider exposes **one resource type**: `onepassword_item`. It does not expose a Connect server or access token resource. Connect server creation and ESO token issuance are `op` CLI operations only — they cannot be managed via Terraform.

**Consequence:** The Connect server lifecycle (creation, token rotation) is a manual operation, not a tofu-managed one. This is documented as a one-time bootstrap procedure below.

### 4.2 Connect Server Bootstrap (one-time, manual)

Performed once during initial setup and repeated during DR:

```bash
# Requires an active op session with access to the Homelab vault

# 1. Create the Connect server in 1Password.com
op connect server create "vollminlab-k8s" --vaults Homelab
# Outputs: 1password-credentials.json file

# 2. Create a read-only ESO access token scoped to Homelab vault
op connect token create "eso-access-token" \
  --server "vollminlab-k8s" \
  --vaults Homelab

# 3. Store both in a 1Password vault item for DR recovery
#    Create item "Connect Server Credentials" in Homelab vault with:
#    - field "credentials_json" (type: concealed): contents of 1password-credentials.json
#    - field "eso_token" (type: concealed): the token from step 2
#    Apply "Homelab" tag. Confirm item name with user before creating (per memory conventions).
op item create \
  --vault Homelab \
  --title "Connect Server Credentials" \
  --tags Homelab \
  --category "Secure Note" \
  'credentials_json[concealed]=<contents of 1password-credentials.json>' \
  'eso_token[concealed]=<eso access token>'
```

**This vault item is the DR artifact.** During cluster rebuild, the Connect bootstrap secret is materialized entirely from this item via `op item get`.

### 4.3 ESO Access Token Rotation

Token rotation is a manual operation (two `op` CLI commands) with an audit trail maintained by updating the vault item:

```bash
# Revoke old token and create new one
op connect token revoke --server "vollminlab-k8s" --token <old-token-id>
op connect token create "eso-access-token" --server "vollminlab-k8s" --vaults Homelab
# Update the vault item
op item edit "Connect Server Credentials" --vault Homelab 'eso_token[concealed]=<new-token>'
# Update the cluster secret
kubectl create secret generic onepassword-connect-token -n 1password \
  --from-literal=token=<new-token> --dry-run=client -o yaml | kubectl apply -f -
```

Document full procedure in `docs/runbooks/eso-token-rotation.md` before Phase 0 (pre-implementation requirement).

### 4.4 Future: `1password-config` Tofu Workspace

In a future phase, a `1password-config` tofu workspace (using `onepassword_item` resources + the `1Password/onepassword` provider with a `service_account_token`) can be introduced to have tofu workspaces write their own provisioned credentials (Harbor robot accounts, Authentik OAuth clients, CNPG passwords) directly to 1Password vault items. This closes the loop: tofu provisions → writes to 1Password → ESO reads → K8s Secret.

That capability is **out of scope for this design**. It is a follow-on task after the ESO migration is stable.

---

## 5. Secret Classification

### 5.1 refreshInterval Tiers

| Tier | Value | Applies To |
|---|---|---|
| Standard | `1h` | App API keys, OAuth clients, service credentials, exportarr API keys |
| Stable infra | `24h` | Cloudflare tunnel tokens, Tailscale OAuth, Harbor pull secrets, Renovate token |
| Immutable | `"0"` | CNPG passwords, Volsync restic repository passwords |

**Note:** The string `"Never"` is not a valid ESO `refreshInterval` value — it will be rejected by the ESO admission webhook. Use `"0"` (equivalent to `"0s"`) to sync once on creation and never again. In ESO v0.9+, `refreshPolicy: CreatedOnce` is the preferred, more explicit alternative. Use whichever the deployed ESO version supports; verify before writing ExternalSecret CRs.

### 5.2 creationPolicy

`Owner` for all standard ExternalSecrets: ESO owns the K8s Secret lifecycle and deletes it if the ExternalSecret is deleted. This is the correct default.

**Exception — CNPG:** Use `creationPolicy: Merge`. CNPG's controller also writes fields to the same Secret (connection URI, pgpass, jdbc-uri). If ESO uses `Owner`, CNPG's writes conflict with ESO's ownership and CNPG enters an error loop. With `Merge`, ESO only writes the specified fields (e.g., password) without taking ownership. **Consequence of `Merge`:** the Secret is NOT garbage-collected when the ExternalSecret is deleted. Manual cleanup is required.

### 5.3 Special Cases

#### CNPG passwords (`refreshInterval: "0"`, `creationPolicy: Merge`)

CNPG does not watch its credential Secret for live updates. If the K8s Secret changes, the running database password is not updated — applications immediately get auth failures. Rotation requires a coordinated procedure documented in `docs/runbooks/cnpg-password-rotation.md`:

1. Update password in 1Password vault item
2. Force one ESO sync: `kubectl patch externalsecret <name> -n <ns> --type=merge -p '{"spec":{"refreshInterval":"1s"}}'`
3. Confirm sync completed: `kubectl get externalsecret <name> -n <ns>` → `Ready: True`
4. **Immediately** patch back to prevent continuous re-sync: `kubectl patch externalsecret <name> -n <ns> --type=merge -p '{"spec":{"refreshInterval":"0"}}'`
5. Execute CNPG password rotation: `kubectl cnpg psql <cluster> -n <ns>` → `ALTER ROLE <user> WITH PASSWORD '<new-password>';`
6. Verify app connectivity

The window between steps 2 and 5 must be minimized. Perform during a low-traffic window.

#### Volsync restic passwords (`refreshInterval: "0"`)

A restic repository password is set at repository initialization and cannot change afterward without a full repository migration. If the K8s Secret is updated to a different password, all subsequent Volsync backup and restore operations fail permanently for that repository.

These ExternalSecrets must use `refreshInterval: "0"`. Vault item fields for restic passwords must be treated as permanently frozen after initial creation. Never rotate these passwords without a full restic repository migration plan.

#### Harbor pull secrets (6+ namespaces)

`harbor-vollminlab-pull-sealedsecret.yaml` exists in `flux-system`, `monitoring`, `shlink`, `dmz`, and others — all referencing the same Harbor robot account. Create one ExternalSecret per namespace with `refreshInterval: 24h`. All reference the same vault item. Maintain the existing per-namespace pattern; do not introduce cross-namespace secret machinery.

#### `flux-system` GitHub App credentials — NOT managed by ESO

The `flux-system` SealedSecret contains `githubAppID`, `githubAppInstallationID`, and `githubAppPrivateKey`. These are read by the Flux `GitRepository` controller via `spec.secretRef.name: flux-system`.

**ESO must never manage this secret.** If ESO is broken or the ClusterSecretStore becomes unavailable, Flux cannot pull from GitHub to apply the fix — creating an unrecoverable deadlock. This secret must remain a manually-bootstrapped credential.

Migration: confirm credentials exist in 1Password as **"Flux GitHub App Credentials"** (Homelab vault, fields: `app_id`, `installation_id`, `private_key`). Remove `flux-system-sealedsecret.yaml` from git. Update DR runbook (Section 9). Verify the item exists before removing the SealedSecret from git.

---

## 6. Vault Item Naming Discipline

After migration, **1Password Homelab vault item names and field labels are cluster infrastructure.** Renaming a vault item or field in the 1Password UI will silently break the corresponding ExternalSecret on the next sync attempt (which triggers an alert within 15 minutes — see Section 8).

**Add to `.claude/rules/secrets.md` before Phase 1 begins (pre-implementation requirement):**

- Never rename a vault item that an ExternalSecret references without first updating the ExternalSecret CR and merging the PR
- Field label names within vault items are locked — use consistent labels: `username`, `password`, `api_key`, `token`, `credentials_json`
- If a vault item must be renamed: update ExternalSecret CR → merge PR → Flux reconciles → verify sync is `Ready` → then rename in 1Password
- Add a note to vault items that are referenced by ExternalSecrets: "Referenced by ExternalSecret — do not rename fields"

---

## 7. Reloader Integration

The existing `reloader` HelmRelease watches both Secrets and ConfigMaps. However, Reloader only restarts pods that carry the `reloader.stakater.com/auto: "true"` annotation (or specific resource annotations). Pods without this annotation are not restarted on secret rotation.

**Pre-implementation requirement (Phase 0):** Audit all workloads that will have secrets migrated to ESO. Confirm each Deployment/StatefulSet/DaemonSet carries `reloader.stakater.com/auto: "true"`. Add the annotation to any that are missing — include this in the same PR as the ExternalSecret CRs for that namespace.

When Reloader is configured correctly, the full rotation path is automated:
```
Update value in 1Password → ESO syncs within refreshInterval → K8s Secret updated → Reloader restarts pods → App uses new credential
```

CNPG and Volsync rotation remain manual by design (see Section 5.3).

---

## 8. Migration Strategy

### 8.1 Prerequisites (Phase 0)

All of the following must be complete before migrating any namespace:

1. Add vault naming discipline rules to `.claude/rules/secrets.md`
2. Write `docs/runbooks/eso-token-rotation.md`
3. Write `docs/runbooks/cnpg-password-rotation.md`
4. Provision Connect server and ESO token via `op` CLI (Section 4.2). Store in 1Password vault item.
5. Deploy `1password-connect` HelmRelease. Apply bootstrap secret from `op`. Verify Connect pod `Running`.
6. Deploy `external-secrets` HelmRelease. Verify ESO operator pod `Running`.
7. Deploy `ClusterSecretStore`. Verify status: `Ready`.
8. Deploy ServiceMonitor for Connect and PrometheusRule for ESO (Section 9). Verify metrics appear in Prometheus and a test alert can be triggered before migration begins.
9. Verify Kyverno compliance: Connect and ESO pods carry required labels and resource limits. If chart defaults are insufficient, override via ConfigMap values and re-deploy before proceeding.
10. Audit all workloads for `reloader.stakater.com/auto: "true"` annotation. Add to any missing.
11. Audit all 1Password Homelab vault items: confirm each SealedSecret targeted in Phase 1 has a corresponding vault item with correct field labels. Fix any gaps before writing ExternalSecret CRs.
12. Confirm `1password.com:443` is reachable from the `1password` namespace: `kubectl run -it --rm debug --image=alpine --restart=Never -n 1password -- wget -qO- https://1password.com` (or equivalent).

### 8.2 Per-Namespace Migration Procedure

For each namespace, in order:

1. Confirm the 1Password vault item exists with the correct field labels
2. Confirm the target workload has the Reloader annotation
3. Write ExternalSecret CR using the **same `metadata.name`** as the existing SealedSecret's K8s Secret name (consumers reference this name — changing it requires updating all referencing manifests atomically)
4. Set `refreshInterval` per Section 5.1; set `creationPolicy` per Section 5.2
5. Open a PR with: the new ExternalSecret CR and the SealedSecret YAML deletion — both in the same commit
6. **Before merging:** apply the ExternalSecret CR manually and verify sync: `kubectl get externalsecret <name> -n <ns>` → `Ready: True`. Confirm the K8s Secret contains expected fields.
7. Merge the PR. Flux reconciles: ExternalSecret CR is applied (already exists, no change), SealedSecret resource is pruned by Flux, SealedSecret controller removes the old K8s Secret, ESO re-creates it from 1Password.

**Secret-absence window:** Between the SealedSecret controller removing the old Secret and ESO creating the new one, there is a brief window (typically <5 seconds) where the Secret does not exist. Any pod that restarts during this window will fail to mount it. Perform migrations during low-traffic periods. For critical auth-path secrets (Authentik, CNPG), plan for this window explicitly.

**Rollback:** If the ExternalSecret fails to sync or produces incorrect data, re-apply the SealedSecret YAML manually (`kubectl apply -f <saved-sealedsecret>.yaml`) and delete the ExternalSecret. The SealedSecret controller recreates the K8s Secret. Keep a `rollback/pre-eso-migration` remote branch (pushed to origin) containing all original SealedSecret YAMLs until Phase 11 is complete.

### 8.3 Migration Order

Order is by blast radius if migration fails. Phases with multiple sub-steps should complete one sub-step before beginning the next:

| Phase | Namespaces / Targets | Rationale |
|---|---|---|
| 1 | `monitoring` — exportarr API keys, pushover config, vmware-exporter, b2-exporter, grafana admin, loki-minio | Low blast radius; monitoring degradation is acceptable during verification |
| 2 | `external-dns`, `renovate`, `kube-system` | Simple single-secret namespaces, low risk |
| 3 | `mediastack` — SMB credentials, jellystat, qbittorrent-PIA (not volsync restic yet) | High count, most are API keys |
| 4 | `mediastack` — volsync restic secrets only (immutable; special care required) | Separate from phase 3 to limit blast radius |
| 5 | `minio` | Infrastructure credential; MinIO is resilient to brief secret refresh |
| 6 | `harbor` — admin, core, db credentials | Higher risk — Harbor serves image pulls for the whole cluster |
| 7 | `shlink`, `velero` | Infrastructure credentials |
| 8a | `tofu/grafana-config`, `tofu/minio-config` | Start with lower-stakes tofu workspaces; verify workspace reconciles between sub-phases |
| 8b | `tofu/harbor-config`, `tofu/cloudflare-config`, `tofu/b2-config` | |
| 8c | `tofu/authentik-config`, `tofu/prowlarr-config`, `tofu/radarr-config`, etc. | |
| 9 | `authentik` — credentials, db, CNPG-minio, proxy token | Auth path; migrate only after ESO has been stable for ≥2 weeks |
| 10 | `ingress-nginx`, `tailscale`, `dmz`, `actions-runner-system` | Networking infra — migrate after all app secrets confirmed stable |
| 11 | `flux-system` — headlamp-oidc, harbor pull secrets | Last app secrets before cleanup |
| 12 | Cleanup — remove SealedSecrets controller (see Section 8.4) | |

### 8.4 Sealed-Secrets Controller Removal (Phase 12)

The removal sequence in Flux must be performed in order to avoid reconciliation race conditions:

1. Verify zero SealedSecrets remain: `kubectl get sealedsecrets -A` must return empty
2. Remove the SealedSecret YAML files from the sealed-secrets kustomization resource list (not the HelmRelease yet). PR + merge. Flux prunes the remaining SealedSecret objects (none should remain).
3. Remove the sealed-secrets HelmRelease from its kustomization. PR + merge. Flux uninstalls the Helm release.
4. Remove the kustomization entry from `flux-kustomizations/kustomization.yaml` and the sealed-secrets namespace. PR + merge.
5. Migrate the `1password-config` service account token from SealedSecret to ExternalSecret (the final SealedSecret — now safe to remove since the SealedSecrets controller is still running until step 3).
6. Mark "Sealed Secrets Sealing Key" in 1Password as deprecated in item notes. Do not delete it — it may be needed to decrypt historical git content.

---

## 9. Monitoring and Alerting

### 9.1 ServiceMonitor — 1Password Connect

Connect exposes Prometheus metrics at `:8080/metrics`. Add a `ServiceMonitor` in the `1password` namespace.

**Pre-implementation:** Verify actual metric names by scraping Connect's metrics endpoint on a test deployment before writing the ServiceMonitor. The following are expected but must be confirmed:
- `op_connect_api_requests_total` — request rate to 1Password cloud
- Cache hit/miss metrics (name varies by Connect version)

Include a Connect health panel in Grafana alongside existing Longhorn and Velero dashboards.

### 9.2 PrometheusRule — ESO Sync Failures

Two alerts are required. Both must be validated in a test environment (manually set an ExternalSecret to reference a non-existent vault item and confirm alerts fire) before Phase 1 migration begins.

**`ExternalSecretSyncError`** — Any ExternalSecret in `Ready=False` for more than 15 minutes:
```yaml
- alert: ExternalSecretSyncError
  expr: externalsecret_status_condition{type="Ready",status="False"} > 0
  for: 15m
  labels:
    severity: warning
  annotations:
    summary: "ExternalSecret sync failure"
    description: "ExternalSecret {{ $labels.name }} in namespace {{ $labels.namespace }} has not synced successfully for 15 minutes."
```

**`ExternalSecretSyncErrorCritical`** — Auth-path namespaces (shorter threshold):
```yaml
- alert: ExternalSecretSyncErrorCritical
  expr: externalsecret_status_condition{type="Ready",status="False",namespace=~"authentik|harbor|shlink"} > 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Critical ExternalSecret sync failure"
    description: "ExternalSecret {{ $labels.name }} in auth-path namespace {{ $labels.namespace }} has not synced for 5 minutes."
```

**Note on metric names:** `externalsecret_status_condition` is documented in ESO's metrics reference. Verify the label key names (`type`, `status`, `name`, `namespace`) match the deployed ESO version before writing the PrometheusRule. The `ExternalSecretStale` alert based on sync call counters is deferred — valid PromQL for staleness detection using ESO's counter metrics requires time-window analysis that should be prototyped against a real ESO deployment, not specified in advance.

---

## 10. Disaster Recovery

### 10.1 Full Cluster Rebuild Procedure

```bash
# Step 1: Rebuild cluster (kubeadm, CNI — per existing DR procedure)

# Step 2: Sign in to 1Password
# User runs: ! op signin

# Step 3: Apply Connect bootstrap secret
kubectl create namespace 1password

CREDS_JSON=$(op item get "Connect Server Credentials" \
  --vault Homelab --format json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='credentials_json'))")

ESO_TOKEN=$(op item get "Connect Server Credentials" \
  --vault Homelab --format json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='eso_token'))")

kubectl create secret generic onepassword-connect \
  -n 1password \
  --from-literal=1password-credentials.json="$CREDS_JSON" \
  --from-literal=token="$ESO_TOKEN"

# Verify the secret exists before continuing
kubectl get secret onepassword-connect -n 1password

# Step 4: Bootstrap Flux using a GitHub PAT
# A short-lived PAT for DR bootstrap is stored in 1Password as "Flux Bootstrap PAT" (Homelab vault).
# This PAT only needs repo read access and is used once.
GITHUB_TOKEN=$(op item get "Flux Bootstrap PAT" \
  --vault Homelab --format json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='token'))")

export GITHUB_TOKEN

flux bootstrap github \
  --owner=vollminlab \
  --repository=k8s-vollminlab-cluster \
  --branch=main \
  --path=clusters/vollminlab-cluster \
  --token-auth

# Step 5: Apply the flux-system GitHub App secret
# This switches Flux from PAT auth to GitHub App auth (the long-term credential)
APP_ID=$(op item get "Flux GitHub App Credentials" \
  --vault Homelab --format json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='app_id'))")

INSTALL_ID=$(op item get "Flux GitHub App Credentials" \
  --vault Homelab --format json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='installation_id'))")

PRIVATE_KEY=$(op item get "Flux GitHub App Credentials" \
  --vault Homelab --format json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='private_key'))")

# Write private key to tmpfs (never to disk)
KEYFILE=$(mktemp /dev/shm/flux-key.XXXXXX)
echo "$PRIVATE_KEY" > "$KEYFILE"

kubectl create secret generic flux-system \
  -n flux-system \
  --from-literal=githubAppID="$APP_ID" \
  --from-literal=githubAppInstallationID="$INSTALL_ID" \
  --from-literal=githubAppPrivateKey="$(cat $KEYFILE)" \
  --dry-run=client -o yaml | kubectl apply -f -

rm "$KEYFILE"
unset PRIVATE_KEY KEYFILE

# Step 6: Wait for Connect + ESO to come up
flux get kustomizations -A --watch
# Connect and ESO HelmReleases reconcile within ~5-10 minutes

# Step 7: All ExternalSecrets sync automatically. Monitor:
kubectl get externalsecrets -A
```

### 10.2 1Password Vault Items Required for DR

These vault items must exist before DR is possible. Verify their existence periodically:

| Item Name | Vault | Fields Required |
|---|---|---|
| Connect Server Credentials | Homelab | `credentials_json`, `eso_token` |
| Flux GitHub App Credentials | Homelab | `app_id`, `installation_id`, `private_key` |
| Flux Bootstrap PAT | Homelab | `token` |

### 10.3 Comparison: Current vs. New DR

| Step | Current | New |
|---|---|---|
| 1 | Rebuild cluster | Rebuild cluster (unchanged) |
| 2 | Restore sealing key from 1Password (manual, error-prone) | `op signin` → apply Connect bootstrap secret (3 kubectl commands, values from `op`) |
| 3 | `flux bootstrap` | `flux bootstrap` with PAT from `op` + apply GitHub App secret |
| 4 | Flux reconciles, SealedSecrets decrypt | Flux reconciles, Connect + ESO start, all ExternalSecrets sync automatically |
| 5 | Apps come up | Apps come up |

The sealing key restore step (the most failure-prone part of current DR) is replaced by structured `op item get` calls against well-named vault items. The procedure is more mechanical, less dependent on tribal knowledge, and fully rehearsable without a real DR event.

### 10.4 Velero Restore Behavior

Velero does not restore K8s Secrets that have an `ownerReference` set — ESO-owned secrets are skipped during Velero restore by design (Velero defers to the owning controller to recreate them). After a Velero namespace restore, ESO must be running and able to reach Connect for secrets to materialize. Do not perform a Velero namespace restore expecting secrets to be immediately present; wait for ESO to sync first.

---

## 11. Security Model

### 11.1 Access Boundaries

- **ESO access token**: read-only, scoped to Homelab vault only. Provisioned via `op connect token create`. Stored in 1Password and in cluster as a manually-applied Secret in `1password` namespace.
- **ClusterSecretStore**: any namespace can create ExternalSecrets referencing the Homelab vault. Mitigated entirely by GitOps gating — no pod has `kubectl apply` access, all changes require a PR through branch protection. This is the correct threat model for this cluster.
- **tofu state in MinIO**: contains secret values as it does today for all tofu workspaces. Not a new risk surface introduced by this design.
- **ESO ClusterRole**: ESO's ClusterRole grants read/write access to Secrets across all namespaces. This is required for ESO to create Secrets in any namespace. It cannot be scoped further without switching to per-namespace SecretStores. The GitOps gate (not the RBAC scope) is the control for this cluster.

### 11.2 Bootstrap Dependency Chain

The manual bootstrap secret is the invariant that breaks all circular dependencies. This chain must be understood clearly:

```
Manual: apply Connect bootstrap secret (kubectl, values from op)
  → Connect pod starts and reaches 1Password cloud
  → ClusterSecretStore becomes Ready
  → ESO syncs all ExternalSecrets
  → All K8s Secrets materialize (including tofu workspace credentials)
  → tofu-controller can access MinIO state
  → 1password-config workspace can run (future: writes vault items)
```

The Connect bootstrap secret is the one step that **must never become an ExternalSecret.** It is the root of the trust chain.

---

## 12. End State

After Phase 12 (cleanup) is complete:

- **0 SealedSecrets** in the cluster
- **~73 ExternalSecrets** across 17 namespaces (approximately 1:1 with current SealedSecrets)
- **SealedSecrets controller removed** — HelmRelease, namespace, and Flux kustomization entries deleted in a staged sequence (Section 8.4)
- **Sealing key** in 1Password marked deprecated in item notes; retained for historical reference (may be needed to decrypt historical git content)
- **Secret rotation**: update value in 1Password → automatic propagation within `refreshInterval` → Reloader restarts annotated pods → zero manual steps for standard credentials (CNPG and Volsync remain manual by design)
- **DR**: `op signin` → 3 kubectl commands → `flux bootstrap` → automated reconciliation

---

## 13. Pre-Implementation Verification Checklist

These items **must** be resolved before any implementation work begins. They are not deferred to implementation — they are gate conditions.

1. **Kyverno chart compliance**: Verify `1password/connect` and `external-secrets/external-secrets` chart values expose pod label and resource limit overrides. Test on a non-production namespace before writing HelmRelease manifests.

2. **ESO `refreshInterval: "0"` vs `refreshPolicy: CreatedOnce`**: Identify the exact ESO chart version to be deployed and confirm which mechanism to use for immutable secrets. Write this into the ExternalSecret templates before Phase 3/4 migration.

3. **CNPG bootstrap secret behavior**: Run `kubectl describe cluster <cnpg-cluster> -n authentik` to understand whether CNPG creates its own credential secret or reads from an existing one. Confirm `creationPolicy: Merge` does not conflict with CNPG's controller behavior in the deployed CNPG version.

4. **Connect chart secret naming**: Read the `1password/connect` chart values to determine the exact secret name and key names that Connect expects for credentials and token. Align the DR bootstrap procedure (Section 10.1) exactly to these values.

5. **ESO metric names**: Before writing PrometheusRule, deploy ESO in a test context and scrape `/metrics` to confirm `externalsecret_status_condition` label names match what the alerting expressions expect.

6. **Reloader annotation coverage**: Run `kubectl get deployments,statefulsets,daemonsets -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}: {.metadata.annotations.reloader\.stakater\.com/auto}{"\n"}{end}'` to identify all workloads missing the Reloader annotation before Phase 1.

7. **Vault item audit**: Before Phase 1, for every SealedSecret being migrated in Phase 1, confirm the 1Password vault item exists with correct field labels. Do this as a table: SealedSecret name → vault item name → field labels → ExternalSecret field mapping. Fix gaps before writing any ExternalSecret CRs.

8. **GitHub App credentials in 1Password**: Confirm "Flux GitHub App Credentials" and "Flux Bootstrap PAT" vault items exist in Homelab vault before removing `flux-system-sealedsecret.yaml` from git. Do not proceed to Phase 11 until confirmed.

9. **Connect outbound access**: Confirm `1password.com:443` is reachable from cluster nodes before deploying Connect.

10. **Write runbooks**: `docs/runbooks/eso-token-rotation.md` and `docs/runbooks/cnpg-password-rotation.md` must exist and be complete before Phase 0 is declared done.
