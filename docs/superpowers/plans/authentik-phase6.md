# Authentik Phase 6 — NPM Forward-Auth + vCenter OIDC

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Authentik forward-auth to internal services proxied through NPM (Nginx Proxy Manager), and add vCenter as a native OIDC identity source.

**Architecture:** The existing `vollminlab-forward-auth` domain-wide ProxyProvider covers `*.vollminlab.com` — no new outpost or provider is needed for the 4 NPM services. NPM's Advanced tab injects nginx `auth_request` directives pointing to `https://authentik.vollminlab.com/outpost.goauthentik.io/auth/nginx`, which the nginx ingress routes to the standalone proxy outpost via path-split (the Authentik ingress routes `/outpost.goauthentik.io` → `authentik-proxy:9000`, `/` → `authentik-server:80`). vCenter gets a dedicated OAuth2 provider and uses native OIDC alongside (not replacing) existing vsphere.local accounts.

**Tech Stack:** OpenTofu IaC (`terraform/authentik/`), kubeseal + 1Password CLI (`op`), NPM UI (manual), vCenter SSO UI (manual).

---

## File map

| File | Change |
|------|--------|
| `terraform/authentik/applications.tf` | Add 5 `authentik_application` resources (haproxy, npm, pihole, truenas, vcenter) |
| `terraform/authentik/providers_oauth2.tf` | Add `authentik_provider_oauth2.vcenter` |
| `terraform/authentik/variables.tf` | Add `vcenter_client_secret` variable |
| `clusters/vollminlab-cluster/tofu/authentik-config/app/authentik-oauth-client-secrets-sealedsecret.yaml` | Merge `vcenter_client_secret` encrypted key into existing SealedSecret |

No new files. No import blocks needed — all 5 resources are new and will be created by tofu on next reconcile.

---

## Pre-work: Create branch and gather inputs

- [ ] **Create a fresh branch from main:**

```bash
git checkout main && git pull
git checkout -b feat/authentik-phase6
```

- [ ] **Verify the 5 service hostnames below match the actual NPM proxy host configurations:**

| Service | Assumed hostname |
|---------|-----------------|
| Pi-hole | `pihole.vollminlab.com` |
| TrueNAS | `truenas.vollminlab.com` |
| HAProxy | `haproxy.vollminlab.com` |
| NPM | `npm.vollminlab.com` |
| vCenter | `vcenter.vollminlab.com` |

Update the hostnames in the Terraform resources in Tasks 1–2 if any differ.

- [ ] **Get the vCenter OIDC redirect URI before writing the vCenter provider.**

In vCenter: Administration → Single Sign-On → Configuration → Identity Provider → Change Identity Provider → select OpenID Connect. The wizard shows the redirect URI vCenter will use. For vSphere 8.x this is typically `https://vcenter.vollminlab.com/ui/login/oauth2/authcode/callback` — but confirm from the wizard since it differs between minor releases. Note it for Task 2.

- [ ] **Generate a vCenter OAuth2 client_id** (public value — safe to commit):

```bash
python3 -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40)))"
```

Note the 40-char output for Task 2.

---

## Task 1: Add NPM forward-auth application entries

**Files:**
- Modify: `terraform/authentik/applications.tf`

These 4 applications use forward-auth only (no `protocol_provider` field — same pattern as `alertmanager`, `bazarr`, etc.).

Insertions are alphabetical within the existing file. Current order: `grafana … harbor … minio … policy_reporter … portainer … sonarr … vollminlab_forward_auth`.

- [ ] **Insert `haproxy` between `grafana` and `harbor` blocks:**

```hcl
resource "authentik_application" "haproxy" {
  name            = "HAProxy"
  slug            = "haproxy"
  meta_launch_url = "https://haproxy.vollminlab.com"
  open_in_new_tab = false
}
```

- [ ] **Insert `npm` and `pihole` between `minio` and `policy_reporter` blocks:**

```hcl
resource "authentik_application" "npm" {
  name            = "Nginx Proxy Manager"
  slug            = "npm"
  meta_launch_url = "https://npm.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "pihole" {
  name            = "Pi-hole"
  slug            = "pihole"
  meta_launch_url = "https://pihole.vollminlab.com"
  open_in_new_tab = false
}
```

- [ ] **Insert `truenas` between `sonarr` and `vollminlab_forward_auth` blocks:**

```hcl
resource "authentik_application" "truenas" {
  name            = "TrueNAS"
  slug            = "truenas"
  meta_launch_url = "https://truenas.vollminlab.com"
  open_in_new_tab = false
}
```

- [ ] **Run `tofu fmt` to normalize formatting:**

```bash
cd terraform/authentik && tofu fmt
```

- [ ] **Commit:**

```bash
git add terraform/authentik/applications.tf
git commit -m "feat(authentik-tf): add NPM forward-auth application entries (pihole, truenas, haproxy, npm)"
```

---

## Task 2: Add vCenter OAuth2 provider + application

**Files:**
- Modify: `terraform/authentik/providers_oauth2.tf`
- Modify: `terraform/authentik/applications.tf`

- [ ] **Append to `terraform/authentik/providers_oauth2.tf`:**

Replace `<CLIENT_ID>` with the 40-char string from Pre-work.
Replace `<VCENTER_REDIRECT_URI>` with the URI from vCenter's SSO wizard.

```hcl
resource "authentik_provider_oauth2" "vcenter" {
  name               = "vCenter"
  client_id          = "<CLIENT_ID>" # gitleaks:allow
  client_secret      = var.vcenter_client_secret
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  signing_key        = data.authentik_certificate_key_pair.self_signed.id
  sub_mode           = "hashed_user_id"

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      url           = "<VCENTER_REDIRECT_URI>"
    }
  ]

  property_mappings = local.common_property_mappings
}
```

- [ ] **Insert `vcenter` into `applications.tf` between `truenas` and `vollminlab_forward_auth` (alphabetical: v-c before v-o):**

```hcl
resource "authentik_application" "vcenter" {
  name              = "vCenter"
  slug              = "vcenter"
  protocol_provider = authentik_provider_oauth2.vcenter.id
  meta_launch_url   = "https://vcenter.vollminlab.com"
  open_in_new_tab   = false
}
```

- [ ] **Add `vcenter_client_secret` to `terraform/authentik/variables.tf`** (append at the end):

```hcl
variable "vcenter_client_secret" {
  description = "OAuth2 client secret for the vCenter OIDC identity source in Authentik"
  type        = string
  sensitive   = true
}
```

- [ ] **Run `tofu fmt` across the three files:**

```bash
cd terraform/authentik && tofu fmt
```

- [ ] **Commit:**

```bash
git add terraform/authentik/providers_oauth2.tf terraform/authentik/applications.tf terraform/authentik/variables.tf
git commit -m "feat(authentik-tf): add vCenter OIDC OAuth2 provider and application"
```

---

## Task 3: Generate vCenter client secret + seal it

- [ ] **Generate the client secret:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Note the output — do NOT commit it anywhere.

- [ ] **Save it to the existing "Authentik OAuth2 Client Secrets" 1Password item:**

```bash
op item edit "Authentik OAuth2 Client Secrets" --vault Homelab "vcenter client secret[password]=<GENERATED_SECRET>"
```

- [ ] **Verify the field was saved:**

```bash
op item get "Authentik OAuth2 Client Secrets" --vault Homelab --format json | python3 -c "
import json, sys
item = json.load(sys.stdin)
for f in item['fields']:
    if f.get('label') == 'vcenter client secret' and 'value' in f:
        print('Saved OK — length:', len(f['value']))
        break
"
```

Expected output: `Saved OK — length: <non-zero number>`

- [ ] **Fetch the sealing cert:**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem
```

- [ ] **Retrieve the secret from 1Password into a shell variable:**

```bash
VCENTER_SECRET=$(op item get "Authentik OAuth2 Client Secrets" --vault Homelab --format json | python3 -c "
import json, sys
item = json.load(sys.stdin)
for f in item['fields']:
    if f.get('label') == 'vcenter client secret' and 'value' in f:
        print(f['value'])
        break
")
```

Verify it's populated: `echo ${#VCENTER_SECRET}` — should print a non-zero number (never print the value itself).

- [ ] **Merge the new key into the existing SealedSecret manifest:**

```bash
kubectl create secret generic authentik-oauth-client-secrets \
  -n tofu \
  --from-literal=vcenter_client_secret="$VCENTER_SECRET" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  --merge-into clusters/vollminlab-cluster/tofu/authentik-config/app/authentik-oauth-client-secrets-sealedsecret.yaml
```

`--merge-into` adds the new encrypted key without touching the existing 8 encrypted values.

- [ ] **Verify the key was added:**

```bash
grep -c "vcenter_client_secret" clusters/vollminlab-cluster/tofu/authentik-config/app/authentik-oauth-client-secrets-sealedsecret.yaml
```

Expected: `1`

- [ ] **Clean up:**

```bash
rm /tmp/pub-cert.pem
unset VCENTER_SECRET
```

- [ ] **Commit:**

```bash
git add clusters/vollminlab-cluster/tofu/authentik-config/app/authentik-oauth-client-secrets-sealedsecret.yaml
git commit -m "feat(authentik-tf): seal vcenter_client_secret into authentik-oauth-client-secrets"
```

---

## Task 4: Push PR and wait for reconciliation

- [ ] **Push branch and open PR:**

```bash
git push -u origin feat/authentik-phase6
```

PR title: `feat(authentik): phase 6 — NPM forward-auth applications + vCenter OIDC`

PR body:
```
## Summary
- Adds Authentik Application entries for Pi-hole, TrueNAS, HAProxy, NPM (forward-auth only — no protocol_provider, covered by domain-wide vollminlab-forward-auth provider)
- Adds Authentik OAuth2 provider + Application for vCenter OIDC
- Seals vcenter_client_secret into authentik-oauth-client-secrets for tofu-controller

## Notes
- No import blocks needed — all 5 resources are new, created by tofu on reconcile
- After merge: configure NPM Advanced tab for each proxy host (manual — see plan)
- After merge: configure vCenter SSO identity source pointing to Authentik (manual — see plan)
- vCenter local accounts (administrator@vsphere.local, vollmin@vsphere.local) are unaffected — OIDC is additive
```

- [ ] **Wait for CI to pass** (terraform fmt --check + tofu validate run automatically).

- [ ] **After user approves and merges: confirm tofu-controller reconciles authentik-config cleanly:**

```bash
kubectl get terraform authentik-config -n tofu -w
```

Wait for `READY=True` and `AGE` to advance past the last run. Then:

```bash
kubectl get terraform authentik-config -n tofu -o jsonpath='{.status.conditions[?(@.type=="Ready")].message}'
```

Expected: `Applied successfully`

- [ ] **Verify the 5 new applications appear in Authentik:**

Open `https://authentik.vollminlab.com/if/admin/` → Applications. Look for: HAProxy, Nginx Proxy Manager, Pi-hole, TrueNAS, vCenter.

---

## Task 5: NPM UI configuration (manual — do after reconcile confirms clean)

**Order matters:** Configure Pi-hole, TrueNAS, and HAProxy BEFORE configuring NPM itself. If something goes wrong with the NPM proxy host entry, you need NPM to still be editable — an unauthenticated session on NPM is your escape hatch until the other 3 services are confirmed working.

For each of the 4 NPM proxy hosts, open the proxy host settings in NPM → **Advanced** tab and paste this exact block:

```nginx
# Authentik forward auth
auth_request        /outpost.goauthentik.io/auth/nginx;
error_page 401    = @goauthentik_proxy_signin;
auth_request_set    $auth_cookie $upstream_http_set_cookie;
add_header          Set-Cookie $auth_cookie;

location /outpost.goauthentik.io {
    auth_request        off;
    proxy_pass          https://authentik.vollminlab.com;
    proxy_http_version  1.1;
    proxy_set_header    Host authentik.vollminlab.com;
    proxy_set_header    X-Original-URL $scheme://$http_host$request_uri;
    proxy_set_header    X-Forwarded-Host $http_host;
    add_header          Set-Cookie $auth_cookie;
    auth_request_set    $auth_cookie $upstream_http_set_cookie;
    proxy_pass_request_body off;
    proxy_set_header    Content-Length "";
}

location @goauthentik_proxy_signin {
    internal;
    add_header Set-Cookie $auth_cookie;
    return 302 https://authentik.vollminlab.com/outpost.goauthentik.io/start?rd=$scheme://$http_host$escaped_request_uri;
}
```

**Key mechanics:**
- `auth_request` at server block level (injected by NPM's Advanced tab) applies to NPM's generated `location /` block
- `auth_request off` in the outpost location prevents an auth-sub-request loop
- `proxy_pass https://authentik.vollminlab.com` (no trailing path) forwards the original URI path intact; the nginx ingress routes `/outpost.goauthentik.io/...` to the standalone proxy outpost
- `X-Forwarded-Host $http_host` sets the original hostname so the outpost can match it to the `vollminlab-forward-auth` domain provider

**Verification for each service after saving:**

1. Open the service URL in a browser (no active Authentik session) → should redirect to `https://authentik.vollminlab.com/...` login page
2. Log in → should redirect back to the service and load normally
3. With active session: reload the service → should load without re-prompting login

**Order:**
- [ ] Pi-hole (`pihole.vollminlab.com`) — configure and test
- [ ] TrueNAS (`truenas.vollminlab.com`) — configure and test
- [ ] HAProxy (`haproxy.vollminlab.com`) — configure and test
- [ ] NPM (`npm.vollminlab.com`) — configure LAST and test

---

## Task 6: vCenter SSO OIDC configuration (manual — do after reconcile confirms clean)

- [ ] **Log into vCenter** as `administrator@vsphere.local` or `vollmin@vsphere.local`.

- [ ] **Navigate to:** Administration → Single Sign-On → Configuration → Identity Provider.

- [ ] **Add OIDC identity source:**
  - Click **Change Identity Provider** → Select the OpenID Connect option
  - **Client Identifier:** the `<CLIENT_ID>` from Pre-work (same value in `providers_oauth2.tf`)
  - **Shared Secret:** the `vcenter_client_secret` generated in Task 3 (retrieve from 1Password: `op item get "Authentik OAuth2 Client Secrets" --vault Homelab --format json | python3 -c "import json,sys; [print(f['value']) for f in json.load(sys.stdin)['fields'] if f.get('label')=='vcenter client secret']"`)
  - **OpenID Address:** `https://authentik.vollminlab.com/application/o/vcenter/`

  vCenter auto-discovers endpoints from `https://authentik.vollminlab.com/application/o/vcenter/.well-known/openid-configuration`.

- [ ] **Confirm the redirect URI shown in the wizard matches `allowed_redirect_uris` in `providers_oauth2.tf`.**

  If there is a mismatch: update the `url` in `providers_oauth2.tf`, push a follow-up commit to the same branch (or open a new PR), wait for reconcile, then retry.

- [ ] **Verify local accounts are untouched:**

  Administration → Single Sign-On → Users and Groups → Users → Domain: `vsphere.local`. Confirm `administrator` and `vollmin` still appear. Do NOT proceed until confirmed.

- [ ] **Test local login still works:**

  Log out. On the vCenter login screen, select `vsphere.local` domain, log in as `administrator@vsphere.local`. Confirm successful login. Log out again.

- [ ] **Test OIDC login:**

  On the login screen, select the Authentik identity source. Log in with Authentik credentials. Note: Authentik users have no vSphere permissions by default — a successful login means the OAuth2 flow worked, but the user will have limited/no access until you add permissions in Administration → Access Control → Global Permissions.

- [ ] **Assign vSphere permissions to OIDC users if needed** (scope: vSphere Admin access for `vollmin`):

  Administration → Access Control → Global Permissions → Add. Select the Authentik identity source, search for the user, assign role (e.g., Administrator).

---

## Self-review

**Spec coverage:**
- ✅ Pi-hole: `authentik_application.pihole` (no protocol_provider) + NPM nginx block
- ✅ TrueNAS: `authentik_application.truenas` (no protocol_provider) + NPM nginx block; LDAP explicitly skipped per spec ("forward-auth is sufficient")
- ✅ HAProxy: `authentik_application.haproxy` (no protocol_provider) + NPM nginx block
- ✅ NPM: `authentik_application.npm` (no protocol_provider) + NPM nginx block, configured LAST for safety
- ✅ vCenter: `authentik_provider_oauth2.vcenter` + `authentik_application.vcenter` (with protocol_provider) + SSO UI steps
- ✅ vCenter local accounts preserved: explicitly verified before and after OIDC in Task 6
- ✅ Break-glass posture documented: all 4 NPM services accessible via direct IP:port; vCenter via vsphere.local accounts
- ✅ No ak shell for persistent changes: all IaC via terraform/authentik/
- ✅ No plain Secret committed: SealedSecret (--merge-into pattern), 1Password for storage
- ✅ No new outpost, no new forward_single provider — vollminlab-forward-auth domain provider used
- ✅ No import blocks for new resources — tofu creates them on reconcile
- ✅ PR required, never push to main
- ✅ NPM self-protection ordering note included
