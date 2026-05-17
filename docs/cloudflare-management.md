# Cloudflare Management

All Cloudflare resources for `vollminlab.com` are managed by Terraform via the
`tofu-controller` workspace `cloudflare-config`. The source is
`terraform/cloudflare/` in this repo.

**Do NOT create, modify, or delete any of the resources below in the Cloudflare
dashboard.** Terraform will overwrite manual changes on the next reconciliation
(every 10 minutes). Make all changes in `terraform/cloudflare/` and open a PR.

---

## What is TF-managed

### DNS records (`dns.tf`)

| Record | Type | Target | Notes |
|--------|------|--------|-------|
| `dynamic.vollminlab.com` | A | WAN IP | Updated by DDNS client; TF owns record structure but ignores content changes |
| `vollminlab.com` (apex) | CNAME | `dynamic.vollminlab.com` | |
| `bluemap.vollminlab.com` | CNAME | `dynamic.vollminlab.com` | |
| `mastersleague.vollminlab.com` | CNAME | `dynamic.vollminlab.com` | |
| `minecraft.vollminlab.com` | CNAME | `dynamic.vollminlab.com` | |
| `vpn.vollminlab.com` | CNAME | `vollminlab.com` | |
| `authentik.vollminlab.com` | CNAME | Authentik CF tunnel | proxied |
| `audiobookshelf.vollminlab.com` | CNAME | Audiobookshelf CF tunnel | proxied |
| `filebrowser.vollminlab.com` | CNAME | ClusterNginx CF tunnel | proxied; nginx injects forward-auth headers |
| `jellyfin.vollminlab.com` | CNAME | Jellyfin CF tunnel | proxied |

### Zero Trust Tunnels (`tunnels.tf`)

All tunnels are TF-created (not imported). The CF provider generates the tunnel
secret automatically. Tunnel IDs are written to the `cloudflare-tunnel-ids`
Secret in the `tofu` namespace after each apply via `writeOutputsToSecret`.
Tokens are fetched from the CF API post-apply (see two-PR pattern below).

| TF resource | CF tunnel name | Routes to |
|-------------|---------------|-----------|
| `cloudflare_zero_trust_tunnel_cloudflared.authentik` | `vollminlab-Authentik` | `authentik-server.authentik:80` |
| `cloudflare_zero_trust_tunnel_cloudflared.audiobookshelf` | `vollminlab-Audiobookshelf` | `audiobookshelf.mediastack:10223` |
| `cloudflare_zero_trust_tunnel_cloudflared.jellyfin` | `vollminlab-Jellyfin` | `jellyfin.mediastack:8096` |
| `cloudflare_zero_trust_tunnel_cloudflared.nginx` | `vollminlab-ClusterNginx` | `ingress-nginx-controller.ingress-nginx:80` |

The ClusterNginx tunnel is the generic entry point for any service that uses
nginx forward-auth (Authentik header injection). Add new forward-auth services
to the nginx ingress rules block in `tunnels.tf`, not as a separate tunnel.

---

## What is NOT TF-managed

| Resource | Reason |
|----------|--------|
| `_acme-challenge.*` TXT records | Created/deleted automatically by cert-manager for Let's Encrypt DNS-01. TF must never touch these. |
| Zero Trust Access policies | Not yet in TF scope; managed in CF dashboard. Add to TF if policies become complex. |
| Firewall / WAF rules | Not yet in TF scope. |

---

## Adding a new external service

### Service goes through an existing tunnel (most common)

If the service runs behind nginx (uses Authentik forward-auth), add an ingress
rule to the `cloudflare_zero_trust_tunnel_cloudflared_config.nginx` resource
in `tunnels.tf` and a DNS CNAME pointing to
`${cloudflare_zero_trust_tunnel_cloudflared.nginx.id}.cfargotunnel.com` in
`dns.tf`. No new tunnel needed.

If the service has its own dedicated tunnel (native OIDC like Jellyfin/ABS),
add the full set: tunnel resource, config resource, DNS record, and output.
Then follow the two-PR pattern below to deploy the cloudflared pod.

### Service is a DDNS-relative hostname (direct WAN access, not tunnelled)

Add a CNAME pointing to `dynamic.vollminlab.com` in `dns.tf`. No tunnel needed.

### Two-PR pattern for new tunnels

New tunnel work always requires two PRs because the tunnel token must be
fetched from the CF API and sealed before the cloudflared pod can start.

1. **PR 1 — TF changes**: `tunnels.tf`, `dns.tf`, `outputs.tf`. After merge,
   tofu-controller applies and writes tunnel IDs to the `cloudflare-tunnel-ids`
   Secret in the `tofu` namespace.

2. **Fetch tokens from CF API** (after tofu applies):

   ```bash
   ACCOUNT_ID=9013108406ddceed8abc1a3e2e21907d
   CF_TOKEN=$(OP_SESSION_scottvollmin=<session> op item get "Cloudflare Terraform" \
     --format json | python3 -c "import sys,json; print(next(f['value'] \
     for f in json.load(sys.stdin)['fields'] if f['label']=='credential'))")

   # For each tunnel (authentik, audiobookshelf, jellyfin, nginx):
   TUNNEL_ID=$(kubectl get secret cloudflare-tunnel-ids -n tofu \
     -o jsonpath='{.data.authentik_tunnel_id}' | base64 -d)
   TOKEN=$(curl -s -H "Authorization: Bearer $CF_TOKEN" \
     "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/token" \
     | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['token'])")
   ```

3. **PR 2 — K8s changes**: Seal each token with kubeseal, commit SealedSecret
   + cloudflared Deployment.

---

## Workspace details

- **Workspace CR**: `clusters/vollminlab-cluster/tofu/cloudflare-config/app/terraform-cr.yaml`
- **TF source**: `terraform/cloudflare/`
- **State backend**: MinIO (`terraform-state` bucket, `cloudflare/terraform.tfstate`)
- **Credentials secret**: `cloudflare-tf-credentials` in `tofu` namespace (item: "Cloudflare Terraform" in 1Password)
- **Tunnel ID output secret**: `cloudflare-tunnel-ids` in `tofu` namespace
- **Reconciliation interval**: 10 minutes, `approvePlan: auto`
