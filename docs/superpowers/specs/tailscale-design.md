# Tailscale Subnet Router Design

**Date:** 2026-05-24
**Goal:** Deploy Tailscale as a cluster-wide failsafe — when Cloudflare or ingress-nginx is unavailable, connecting to Tailscale restores full access to all services and nodes via the existing ingress stack.

---

## Architecture

A Tailscale subnet router runs as a pod in the `tailscale` namespace, managed by the Tailscale Kubernetes Operator. It advertises two subnets to the tailnet:

- `192.168.152.0/24` — covers all 9 cluster nodes (`.8`–`.16`) and all MetalLB VIPs (`.244`–`.254`)
- `192.168.100.0/24` — covers Pi-hole at `192.168.100.2`

Tailscale split DNS is configured to forward `vollminlab.com` queries to Pi-hole. When connected to Tailscale from anywhere, `*.vollminlab.com` names resolve via Pi-hole to the nginx-ingress VIP (`192.168.152.244`) and route through the subnet — identical to being on the home LAN. SSH to any node IP works directly.

Route auto-approval is managed via the Tailscale ACL (`autoApprovers`), eliminating the manual admin console approval step. Split DNS is also managed via Terraform.

---

## Scope

**What this delivers:**
- All services accessible over Tailscale via existing `*.vollminlab.com` hostnames
- SSH to any cluster node by IP
- No changes to existing services, ingress, or MetalLB config

**Out of scope:**
- Individual per-service Tailscale proxies (`loadBalancerClass: tailscale`)
- Tailscale exit node (routes all internet traffic through the cluster)
- Exposing services to other tailnet members (can be added later via per-service annotations)

---

## Components

### 1. Tailscale Operator (cluster)

Namespace: `tailscale`, category: `networking`

Standard Flux app under `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/`:
- `helmrelease.yaml` — Tailscale operator from `https://pkgs.tailscale.com/helmcharts`
- `configmap.yaml` — Helm values (resource limits; operator does not expose a UI or need an ingress)
- `connector.yaml` — `Connector` CR (see below)
- `operator-oauth-sealedsecret.yaml` — SealedSecret with `client_id` and `client_secret`
- `kustomization.yaml` — lists all four files

HelmRepository and Flux Kustomization CR live in `flux-system/` per standard convention.

### 2. Connector CR

A `tailscale.com/v1alpha1 Connector` CR in the `tailscale` namespace tells the operator to create a subnet router pod:

```yaml
apiVersion: tailscale.com/v1alpha1
kind: Connector
metadata:
  name: vollminlab-cluster
  namespace: tailscale
spec:
  hostname: vollminlab-cluster
  subnetRouter:
    advertiseRoutes:
      - 192.168.152.0/24
      - 192.168.100.0/24
```

The operator creates a Deployment for the connector, authenticates it to Tailscale using the OAuth credentials, and registers it as a tailnet device named `vollminlab-cluster`.

### 3. Operator OAuth SealedSecret

The operator authenticates to Tailscale's control plane via an OAuth client created in the Tailscale admin console (`login.tailscale.com/admin/settings/oauth`). Required scopes: `auth_keys` write (allows the operator to generate device auth keys).

Secret fields: `client_id`, `client_secret`
Secret name: `operator-oauth` (the name the Tailscale operator chart expects by default)
Sealed and committed as `operator-oauth-sealedsecret.yaml`.

Credentials saved to 1Password as **"Tailscale Operator OAuth Client"** (Homelab vault) before sealing.

### 4. Terraform Module (`terraform/tailscale/`)

Manages tailnet configuration via the `tailscale/tailscale` Terraform provider. Uses a dedicated API key (OAuth client with `acls:write` and `dns:write` scopes — separate from the operator's OAuth client for least privilege).

Files:
- `versions.tf` — provider `tailscale/tailscale` pinned version
- `providers.tf` — `tailscale { api_key = var.tailscale_api_key }`
- `acl.tf` — `tailscale_acl` resource: adds `autoApprovers` for both subnet routes for devices tagged `tag:k8s` (the default tag applied by the Tailscale operator to managed devices — verify against chart values during implementation). Also defines `tagOwners` for that tag.
- `dns.tf` — `tailscale_dns_split_nameservers` resource: forwards `vollminlab.com` queries to `192.168.100.2`
- `variables.tf` — `tailscale_api_key`

**Note:** The Tailscale ACL is a single JSON document managed as a whole. The existing ACL (if any) must be imported into state before the first apply to avoid overwriting it.

### 5. Tofu Workspace (`clusters/vollminlab-cluster/tofu/tailscale-config/app/`)

Standard tofu-controller workspace pattern:
- `terraform-cr.yaml` — `Terraform` CR pointing to `./terraform/tailscale`, MinIO S3 backend at key `tailscale/terraform.tfstate`, `varsFrom` the credentials secret
- `tailscale-tf-credentials-sealedsecret.yaml` — sealed `tailscale_api_key`
- `kustomization.yaml` — lists both files

`clusters/vollminlab-cluster/tofu/kustomization.yaml` updated to include `tailscale-config/app`.

Credentials saved to 1Password as **"Tailscale Terraform API Key"** (Homelab vault) before sealing.

---

## Bootstrap Prerequisites (user actions)

Before implementation begins:

1. **Create Operator OAuth client** at `login.tailscale.com/admin/settings/oauth`
   - Scope: `auth_keys` — write
   - Save to 1Password as **"Tailscale Operator OAuth Client"**

2. **Create Terraform API key** at `login.tailscale.com/admin/settings/oauth` (or API keys page)
   - Scopes: `acls` — write, `dns` — write
   - Save to 1Password as **"Tailscale Terraform API Key"**

3. Provide both credentials so they can be sealed.

---

## Deployment Sequence

1. Create `terraform/tailscale/` module with ACL + DNS resources
2. Create `clusters/vollminlab-cluster/tailscale/` with operator + connector + sealed secret
3. Create tofu workspace for `tailscale-config`
4. Wire into Flux indexes
5. Merge PR → Flux reconciles operator → connector pod registers → routes advertised → ACL auto-approves → split DNS active

No manual admin console steps required after merge.

---

## Verification

After Flux reconciles:
```bash
# Operator pod running
kubectl get pods -n tailscale

# Connector device appears in tailnet
# (check Tailscale admin console → Machines, or use tailscale CLI)

# Routes auto-approved (no pending approval in admin console)

# From a Tailscale-connected device:
# nslookup grafana.vollminlab.com     → should return 192.168.152.244
# curl -s https://grafana.vollminlab.com  → should respond (not timeout)
# ssh 192.168.152.8                   → should reach k8scp01
```
