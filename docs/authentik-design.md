# Authentik SSO Design

**Created:** 2026-05-05
**Status:** Implemented — retained as the original design record

> **Historical document.** This captures the design as approved on 2026-05-05 and is deliberately
> not rewritten. One thing has changed since: every `SealedSecret` referenced below is now an
> `ExternalSecret` sourced from 1Password via ESO — SealedSecrets were retired and the controller
> removed on 2026-05-31. For the secrets workflow as it stands today see `.claude/rules/secrets.md`;
> for the deployed architecture see `docs/cluster-reference.md`.

## Goal

Deploy Authentik as the central identity provider for the vollminlab cluster, replacing all per-service local logins with a single SSO layer. All web UIs — whether externally accessible or internal-only — authenticate via Authentik. External services use Authentik through a Cloudflare Tunnel. Internal services use nginx forward-auth or native OIDC.

---

## Infrastructure

### New namespaces

| Namespace   | Purpose                                                    |
|-------------|------------------------------------------------------------|
| `authentik` | Authentik server, worker, proxy outpost, CNPG Cluster CR   |

### PostgreSQL (`authentik` namespace)

- **Kind:** CNPG `Cluster` CR — uses the existing `cnpg-cloudnative-pg` operator in `cnpg-system`
- **Instances:** 1 primary (scale to 2 for HA later)
- **Dedicated:** Not shared — each CNPG consumer gets its own Cluster CR
- **Credentials:** Bootstrap credentials in a SealedSecret

### Authentik server and worker (`authentik` namespace)

- **Chart:** `goauthentik/authentik` (official chart)
- **Deployments:** `authentik-server` (API + UI) and `authentik-worker` (background tasks)
- **Category:** `security`
- **SealedSecrets required:**
  - Secret key (cryptographic signing — must be generated once and never rotated without planning)
  - Bootstrap admin password (initial setup only)
  - PostgreSQL connection credentials
- **Note:** Authentik 2025.10+ removed Redis entirely. No Redis dependency.
- **Ingress:** `authentik.vollminlab.com` → nginx → Authentik server port 9000
- **TLS:** `wildcard-tls` (existing pattern)
- **Shlink slug:** `authentik`
- **External access:** Cloudflared route added to the existing Cloudflare Tunnel pointing at `authentik.vollminlab.com`

### Proxy outpost (`authentik` namespace)

- **Image:** `ghcr.io/goauthentik/proxy`
- **Purpose:** Handles all nginx forward-auth requests independently of the Authentik server — active sessions survive Authentik restarts and upgrades
- **Service:** Port 9000 — all forward-auth ingress annotations point to this service
- **Outpost token:** Generated in the Authentik UI after first boot, sealed as a SealedSecret
- **Deployment constraint:** Cannot be deployed until Phase 1 manual step is complete (see Rollout section)

### Flux wiring

Both index files updated in the same PR as the app files (enforced convention):

- `flux-system/repositories/kustomization.yaml` — add `authentik-helmrepository.yaml`
- `flux-system/flux-kustomizations/kustomization.yaml` — add `authentik-kustomization.yaml`

---

## Security considerations

### Cloudflared exposure

Authentik's login UI is publicly reachable via the Cloudflare Tunnel. This is intentional — external users authenticating to Jellyfin need to reach the Authentik login page. Risk is low:

- Cloudflare sits in front (DDoS protection, WAF)
- Authentik has built-in brute-force lockout
- MFA (TOTP) enforced for all accounts, especially admin
- The tunnel is outbound-only; no inbound ports are opened

### Admin UI

Authentik's admin interface (`/if/admin/`) is on the same hostname as the login UI. Mitigations:

- Enforce TOTP on the admin account before exposing externally
- Consider a Cloudflare Access policy blocking `/if/admin/` for non-LAN source IPs as an extra layer

### MFA policy

Enforce TOTP (or WebAuthn) as a required stage for all Authentik users. Configure this in the default authentication flow before integrating any services.

### App auth disabled for forward-auth services

Apps protected by nginx forward-auth must have their own authentication disabled. Authentik is the sole gate; the app trusts that nginx already verified the session. This must be done as part of each Phase 4 service integration.

---

## Integration map

### Native OIDC

The app handles the OAuth2/OIDC flow with Authentik directly. Authentik groups map to app roles for authorization granularity.

| Service | Namespace | Chart/App |
|---|---|---|
| Jellyfin | mediastack | Built-in OIDC |
| Jellyseerr | mediastack | Built-in OIDC (new app, replaces Overseerr) |
| Grafana | monitoring | OAuth2 via kube-prometheus-stack Helm values |
| Harbor | harbor | Built-in OIDC |
| Headlamp | flux-system | OIDC via Helm values |
| Portainer | portainer | OAuth2 (CE edition) |
| Audiobookshelf | mediastack | Built-in OIDC (v2.7.0+) |
| MinIO Console | minio | Built-in OIDC |

### Forward-auth (nginx proxy outpost)

Nginx intercepts requests and checks the proxy outpost before forwarding to the app. The app's own authentication is disabled.

| Service | Namespace | Notes |
|---|---|---|
| Longhorn UI | longhorn-system | No native auth |
| Homepage | homepage | No auth today — forward-auth acts as the gate |
| Radarr | mediastack | Disable app auth in ConfigMap values |
| Sonarr | mediastack | Disable app auth in ConfigMap values |
| Bazarr | mediastack | Disable app auth in ConfigMap values |
| Prowlarr | mediastack | Disable app auth in ConfigMap values |
| SABnzbd | mediastack | Disable app auth in ConfigMap values |
| Tautulli | mediastack | Disable app auth in ConfigMap values |
| Shlink Web | shlink | Disable app auth in ConfigMap values |
| Policy Reporter UI | kyverno | No native auth |

### Forward-auth ingress annotation pattern

All forward-auth ingresses get these annotations (pointing at the proxy outpost service):

```yaml
nginx.ingress.kubernetes.io/auth-url: "http://authentik-proxy.authentik.svc.cluster.local:9000/outpost.goauthentik.io/auth/nginx"
nginx.ingress.kubernetes.io/auth-signin: "https://authentik.vollminlab.com/outpost.goauthentik.io/start?rd=$scheme://$http_host$escaped_request_uri"
nginx.ingress.kubernetes.io/auth-response-headers: "Set-Cookie,X-authentik-username,X-authentik-groups,X-authentik-email,X-authentik-name,X-authentik-uid"
nginx.ingress.kubernetes.io/auth-snippet: |
  proxy_set_header X-Original-URL $scheme://$http_host$request_uri;
```

---

## Phased rollout

### Phase 1 — Core infrastructure (PR 1)

**Deliverables:**
- `authentik` namespace, CNPG Cluster CR, Authentik HelmRelease, ingress, cloudflared Deployment, SealedSecrets
- Both Flux index files updated

**Manual step after Phase 1 deploys:**
1. Log into `authentik.vollminlab.com` with the bootstrap admin credentials
2. Enforce MFA on the admin account before proceeding
3. Create an Authentik Proxy Provider for the forward-auth outpost
4. Create an Outpost of type "Proxy" using that provider
5. Copy the outpost token from the Authentik UI
6. Seal the token: `kubectl create secret generic authentik-proxy-token -n authentik --from-literal=token=<value> --dry-run=client -o yaml | kubeseal --cert pub-cert.pem --format yaml > authentik-proxy-token-sealedsecret.yaml`
7. Commit the SealedSecret — this unblocks Phase 2

### Phase 2 — Jellyfin ecosystem (PR 2)

**Deliverables:**
- Proxy outpost Deployment + Service (uses token SealedSecret from Phase 1 manual step)
- Jellyseerr HelmRelease in `mediastack` (new app, OIDC configured)
- Jellyfin OIDC wired to Authentik (ConfigMap values update)
- Overseerr decommissioned (HelmRelease + resources removed)

**Gate:** Unblocks Plex deprecation.

### Phase 3 — OIDC apps (PR 3)

**Deliverables:**

- Grafana OAuth2 (ConfigMap values update + SealedSecret for client_secret)
- Harbor OIDC (UI configuration — no file changes)
- Headlamp OIDC (ConfigMap values update + SealedSecret for client_secret)
- Portainer OAuth2 (UI configuration — no file changes)
- Audiobookshelf OIDC (UI configuration — no file changes)
- MinIO Console OIDC (ConfigMap values update + SealedSecret for client_secret)

Client secrets must never go in ConfigMaps — each app that requires a secret in Helm values gets a SealedSecret.

### Phase 4 — Forward-auth sweep (PR 4)

**Deliverables:**
- Forward-auth annotations added to ingresses: Longhorn, Homepage, Radarr, Sonarr, Bazarr, Prowlarr, SABnzbd, Tautulli, Shlink Web, Policy Reporter UI
- App auth disabled in ConfigMap values for all arr stack + Tautulli + Shlink Web

**Order within PR:** Disable app auth and add forward-auth annotation in the same commit per service — never disable app auth without the forward-auth annotation in place, or the service becomes unauthenticated.

---

## File layout

```
clusters/vollminlab-cluster/
  authentik/
    namespace.yaml
    kustomization.yaml
    cnpg/app/
      cluster.yaml
      kustomization.yaml
      authentik-db-credentials-sealedsecret.yaml
    authentik/app/
      helmrelease.yaml
      configmap.yaml
      ingress.yaml
      kustomization.yaml
      authentik-credentials-sealedsecret.yaml
    cloudflared-authentik/app/
      deployment.yaml
      kustomization.yaml
      cloudflared-authentik-tunnel-sealedsecret.yaml
    authentik-proxy/app/                              ← added in Phase 2
      deployment.yaml
      service.yaml
      kustomization.yaml
      authentik-proxy-token-sealedsecret.yaml        ← added after Phase 1 manual step
flux-system/
  repositories/
    authentik-helmrepository.yaml
  flux-kustomizations/
    authentik-kustomization.yaml
```
