# Gateway API Migration Design (replacing ingress-nginx)

**Created:** 2026-10-27
**Status:** draft for review. No cluster changes until the proof of concept (phase 1) is approved.
**Tracking:** #1413. Roadmap Phase 7.5. Must finish before the Kubernetes 1.36 hop (#1193).

## Problem

`kubernetes/ingress-nginx` was retired. The SIG Network announcement of 2025-11-11 said there would
be no releases, bugfixes or security fixes after March 2026. The final release, controller v1.15.1 /
chart 4.15.1 (2026-03-19), is what this cluster runs as its only HTTP edge: the LAN VIP
`192.168.152.244`, two Cloudflare tunnel origins, and a Tailscale service. Its support table ends at
Kubernetes 1.35, so the 1.36 hop is blocked on replacing it.

## Decision: Gateway API, implemented by Envoy Gateway

**Gateway API**, because:
- it is the direction the retirement notice recommends;
- the roadmap already plans to land on Gateway API with Cilium (8c), so doing it now means migrating
  once rather than twice.

**Envoy Gateway** is the implementation because:
- it runs on today's CNI;
- it has route-level external auth, which matches Authentik's `/outpost.goauthentik.io/auth/envoy`
  endpoint;
- it is Apache-2.0 and released frequently (v1.9.3 on 2026-10-26).

The `HTTPRoute`s are portable. When Cilium 8a lands, moving to Cilium's Gateway means changing the
GatewayClass and the auth policy, not the routes.

**Rejected alternatives (2026-10-27):**

| Option | Why not |
| --- | --- |
| Traefik with its `kubernetesIngressNGINX` provider | Cheapest migration: it keeps `Ingress` objects and these annotations. But it is a second migration later, and its own docs say forward auth "behaves differently than NGINX". |
| F5 NGINX Ingress Controller | Every annotation renamed. Path-split Ingresses must be restructured. External auth is five months old and had two auth-bypass CVEs fixed on 2026-10-14. Sign-in URIs must be relative, which breaks the absolute Authentik sign-in. |
| Cilium Gateway now | Needs Cilium as the CNI with kube-proxy replacement. That makes CNI migration a prerequisite and delays leaving nginx by months. |
| Chainguard's ingress-nginx fork | Best-effort CVE fixes only, and image availability is unclear. At most a stopgap; nothing here needs one, because the 1.35 hop does not depend on this work. |

## Current state (measured 2026-10-27)

| | |
| --- | --- |
| Ingress objects | 41: 40 hand-written and Flux-applied, 1 created by Authentik's outpost integration (`authentik/ak-outpost-vollminlab-proxy`). **None are templated by Helm charts**, so every route is ours to write. |
| Hosts | 33 hosts. 9 hosts are served by more than one Ingress (path-splits and outpost callbacks). |
| Forward-auth | 23 Ingresses carry `auth-url` / `auth-signin` / `auth-response-headers` and an `auth-snippet` that sets `X-Forwarded-Host`. |
| TLS | 39 use the `wildcard-tls` secret from `cert-manager/wildcard-cert`, plus `vollm-in-tls` and `authentik-outpost-tls`. No cert-manager Ingress annotations. |
| Other annotations | `ssl-redirect` 34, `proxy-buffer-size` 24, `proxy-read-timeout` 12, `rewrite-target` 11, `proxy-send-timeout` 11, `proxy-body-size` 6, `proxy-request-buffering` 4, `proxy-buffering` 2, `configuration-snippet` 2 (FileBrowser: `client_body_timeout 600s`). |
| Things that name ingress-nginx | 9 NetworkPolicy files that admit it, `restrict-loadbalancer` (LoadBalancer allowed only in `ingress-nginx`/`dmz`/`harbor`), 2 privileged-policy exclusions, the Tailscale exception, and 4 tunnel origins in `terraform/cloudflare/tunnels.tf` (authentik, filebrowser, foundry, thecoldwar). |
| DNS | external-dns (Pi-hole, `upsert-only`) watches `ingress` and `service` only. |
| Short links | `shlink-ingress-controller` reconciles only `networking.k8s.io/v1 Ingress` (about 1,000 lines of Go, in its own repo). |

## Target architecture

```
LAN / Pi-hole ──► VIP (new, then .244) ─┐
Cloudflare tunnels (per hostname) ──────┼──► Gateway "edge" (namespace envoy-gateway-system)
Tailscale service ──────────────────────┘      listeners: http :80 (redirect only), https :443 (wildcard cert)
                                                 │
                                   HTTPRoute per app, in the app's namespace
                                   SecurityPolicy (extAuth → Authentik outpost) on protected routes
```

- **One GatewayClass, one Gateway (`edge`) in `envoy-gateway-system`.** `allowedRoutes` comes from
  namespaces labelled `gateway.vollminlab.com/edge: "true"`, so an unlabelled namespace cannot attach
  a route to the public edge.
- **TLS terminates at the listener.** It references `cert-manager/wildcard-tls` through a
  `ReferenceGrant` instead of copying the secret into 39 namespaces.
- **The Envoy data-plane Service is a MetalLB LoadBalancer** on a new VIP during migration, and takes
  `.244` at cutover.

## Design by concern

### 1. Routing
- One `HTTPRoute` per current Ingress, same namespace, same name with `-ingress` → `-route`.
- `ssl-redirect`: 28 Ingresses set `true` and **6 set `false`**. The `false` ones are the hosts the
  Cloudflare tunnels reach over plain HTTP on port 80 (authentik, filebrowser, foundry, thecoldwar)
  plus jellyfin. Their TLS ends at Cloudflare, and nginx is told the original scheme through
  `use-forwarded-headers: "true"`, the redirect-loop fix from #649.
  - **A blanket HTTP→HTTPS redirect would loop those hosts.** The HTTP listener therefore gets two
    kinds of route: the 28 hosts redirect, and the 6 are served over HTTP like today.
  - The Gateway needs a `ClientTrafficPolicy` that trusts `X-Forwarded-Proto`/`X-Forwarded-For`
    from the tunnel, the equivalent of `use-forwarded-headers`. Like today, that trust also extends
    to LAN clients that can reach the VIP.
- `rewrite-target` becomes the `URLRewrite` filter.

### 2. Forward-auth (the critical path)
- Each protected route gets a `SecurityPolicy` with `extAuth.http`. The backend is the Authentik
  outpost Service, path `/outpost.goauthentik.io/auth/envoy`, and `headersToBackend` lists the
  `X-authentik-*` identity headers the apps use today. One policy per route, so an unprotected route
  is one that simply has no policy. That is how path-splits work (concern 3).
- The `forward_single` hosts (FileBrowser, Foundry) and `authentik.vollminlab.com` need
  `/outpost.goauthentik.io/*` routed to the outpost on their own host. Today that is
  `authentik-proxy-callback-ingress`, plus the Ingress the outpost creates itself. The outpost's
  self-managed Ingress gets replaced by a route we own. Whether Authentik can be told to stop
  creating it is a phase 1 question.

### 3. Path-split bypass
- Seven Ingresses carve one path out of an authenticated host: five `-signalr` and one `-socketio`
  on the arr apps, plus FileBrowser's TUS upload path. Each
  becomes a second `HTTPRoute` with the longer path and **no** `SecurityPolicy`.
- Gateway API gives the longest matching path precedence across routes, so this is the same
  semantics as today. That is in the spec; it still gets tested, because getting it backwards either
  locks out the app or exposes the host.

### 4. Identity-header spoofing
- Apps trust `X-authentik-*`. A client must not be able to send its own. Today nginx's
  `auth_request` overwrites them.
- In Envoy, a client-supplied header the auth service does not return could survive. Phase 1 must
  prove a forged header is either dropped or overwritten, both with a session and without one.
- If the default does not strip it, add a `ClientTrafficPolicy` or route-level header removal. Phase
  1 will find out where in Envoy's filter order that has to sit.

### 5. Annotation equivalents

| nginx | Envoy Gateway |
| --- | --- |
| `proxy-read-timeout` / `proxy-send-timeout` | `HTTPRoute` `timeouts.request` / `backendRequest` |
| `proxy-body-size` | No default body limit unless buffering is enabled. Confirm in phase 1 with a 1 GB upload through FileBrowser's TUS route. |
| `proxy-request-buffering: off`, `proxy-buffering: off` | Envoy streams by default. Nothing to set. |
| `proxy-buffer-size` | Used here to fit large auth response headers. Envoy's header limits apply instead; phase 1 checks a request carrying the full Authentik header set. |
| FileBrowser `client_body_timeout 600s` | `ClientTrafficPolicy` timeout scoped to that route's Gateway listener, or `BackendTrafficPolicy` on the route |

### 6. Short links
- Extend `shlink-ingress-controller` to also reconcile `HTTPRoute` with the same
  `shlink.vollminlab.com/slug` annotation. The host comes from `spec.hostnames[0]`.
- This lands, with tests, before any slugged host moves. Until then a moved host keeps its existing
  short link but gets no new one.

### 7. DNS
- Add `gateway-httproute` to external-dns `sources` (chart RBAC included). `upsert-only` stays.
  While a host is in both, two sources could claim one A record, so a host's Ingress is deleted in
  the same PR that adds its route.

### 8. Network policies (security-critical)
- Each of the 9 NetworkPolicy files gets a second `from` entry for the Envoy **data-plane pods**,
  selected by pod label, not the whole namespace (the controller pod has no business reaching apps).
- For the proxy-auth apps (FileBrowser, the internal analytics app, vollmint) the edge must remain
  the **only** path to the pod (#1329/#1330). Their policies list exactly the nginx controller and
  the Envoy proxies during migration, and only the Envoy proxies after it.

### 9. Policy and platform
- `restrict-loadbalancer` gains `envoy-gateway-system`.
- The namespace gets the standard `app`/`env`/`category: networking` labels.
- Envoy images come from `docker.io/envoyproxy/*`, which the registry allow-list already admits.
- Envoy's ServiceMonitor replaces the nginx scrape. There are no nginx-specific alert rules to port
  (checked 2026-10-27; only the monitoring NetworkPolicy names nginx).
- The Tailscale service (`ingress-nginx-tailscale`) gets an Envoy equivalent at cutover.

## Phases

Each phase is its own PR or PRs, and live steps happen in a daytime window.

| Phase | What | Exit criterion |
| --- | --- | --- |
| **0. Install** | Envoy Gateway, the Gateway API CRDs, GatewayClass and Gateway `edge` on a **new** VIP. No routes. Policy and NetworkPolicy prerequisites. | Gateway `Programmed=True`; nothing else changed |
| **1. Proof of concept** | A throwaway host, `gw-test.vollminlab.com`, resolved by a Pi-hole record to the new VIP, routed to a test echo backend. Three routes on it: public, protected, and a path-split under the protected host. | The test matrix below passes. **Stop and review before phase 2.** |
| **2. Plumbing** | `shlink-ingress-controller` HTTPRoute support (its own repo, tests, image); external-dns source; the 9 NetworkPolicies widened | Short link created for `gw-test`; DNS record created from the route |
| **3. Unauthenticated hosts** | Hosts with no `auth-url`, one PR per host or small batch: route added, Ingress deleted | Each host serves from Envoy; DNS points at the new VIP |
| **4. Forward-auth hosts** | The 23 protected Ingresses, the domain-wide provider's apps first, `forward_single` hosts last | Test matrix per host |
| **5. Proxy-auth apps + tunnels** | FileBrowser, the internal analytics app, vollmint; tunnel origins moved per hostname in `terraform/cloudflare/tunnels.tf` (tofu plan approval) | External check through Cloudflare, not only from the LAN (the #1058 lesson) |
| **6. Cutover and removal** | Move `.244` to Envoy; Tailscale service; delete ingress-nginx, its HelmRepository, policy exclusions and `allow-snippet-annotations` | No Ingress objects remain except anything deliberately kept |

**Rollback:** until phase 6, rolling back is reverting a host's PR. The Ingress comes back from git,
nginx is still running, and external-dns re-points the record.

## Phase 1 test matrix (the go/no-go)

Run from outside the cluster and through the tunnel path. Each row must hold.

| # | Request | Expected |
| --- | --- | --- |
| 1 | Public route, no cookie | 200 from backend |
| 2 | Protected route, no cookie | Redirect to Authentik sign-in for the original URL; backend not reached |
| 3 | Protected route after login | 200; backend sees the `X-authentik-*` headers from Authentik |
| 4 | Protected route, no cookie, client sends `X-authentik-username: admin` | Still redirected; backend never sees the header |
| 5 | Protected route after login, client sends a forged `X-authentik-username` | Backend sees Authentik's value, not the forged one |
| 6 | Path-split route under the protected host, no cookie | 200 without auth; the parent path still redirects |
| 7 | HTTP on port 80, ordinary host | 301/308 to HTTPS |
| 7b | HTTP on port 80 with `X-Forwarded-Proto: https`, a tunnel-style host | Served, no redirect loop. The app sees `https` as the scheme |
| 8 | 1 GB upload through a route with a 600 s timeout | Completes |
| 9 | `forward_single`-style host: sign-in, callback, return | Lands on the original URL with a host-scoped session |

## Open questions (answered in phase 1, not assumed)

1. Does Authentik's envoy endpoint return the sign-in redirect itself, or does Envoy need a
   redirect-on-401 configuration? (rows 2 and 9)
2. Where in Envoy's filter chain must forged `X-authentik-*` headers be removed so that the auth
   response still lands? (rows 4 and 5)
3. Can the outpost be stopped from creating its own Ingress, or does it need to stay until phase 6?
4. Does external-dns's `gateway-httproute` source take the address from Gateway status? It must not
   publish the controller's ClusterIP.
