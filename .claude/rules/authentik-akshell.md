# Authentik Management via akshell

All Authentik configuration changes must go through `ak shell` — never the UI unless akshell cannot accomplish the task.

## Running commands

```bash
# Get the server pod
AUTHENTIK_POD=$(kubectl get pods -n authentik -l app.kubernetes.io/name=authentik,app.kubernetes.io/component=server -o name | head -1 | cut -d/ -f2)

# Run a one-liner
kubectl exec -n authentik $AUTHENTIK_POD -- ak shell -c "<python>"

# The shell prints JSON log lines to stderr and your print() output to stdout.
# Ignore the JSON bootstrap noise — your output appears after "authentik shell" banner.
```

## Required nginx ingress annotation on every protected Ingress

Every Ingress with `auth-url` pointing to the Authentik outpost **must also have**:

```yaml
nginx.ingress.kubernetes.io/auth-snippet: |
  proxy_set_header X-Forwarded-Host $http_host;
```

The Authentik outpost (2026.2.2+) uses `X-Forwarded-Host` to match the incoming auth request to a registered provider. nginx ingress always sets `Host: authentik-proxy.authentik.svc.cluster.local` in auth_request sub-locations — without `X-Forwarded-Host`, the outpost cannot find the app and returns 400 → nginx 500.

## Architecture: how forward-auth works here

- **`vollminlab-forward-auth`** — a single `forward_domain` ProxyProvider covering all `*.vollminlab.com`. Serves every host that has no `forward_single` provider of its own.
- **Critical**: `external_host` for this provider must be `https://authentik.vollminlab.com`. This controls the OAuth2 callback URL (`https://authentik.vollminlab.com/outpost.goauthentik.io/callback`), which already has an ingress. Do NOT set it to the root domain (`vollminlab.com`) — that domain has no ingress and no TLS cert, so the OAuth2 callback would be unreachable.
- **Domain matching uses `cookie_domain`, not `external_host`**: The outpost matches `*.vollminlab.com` requests because `cookie_domain=vollminlab.com`. Changing `external_host` to `vollminlab.com` is wrong and breaks OAuth2 callbacks.
- Every host protected by Authentik nginx annotations **must have an Application entry** in Authentik, even if that Application has `provider_id=None`. Without it the outpost returns 400 → nginx converts to 500.
- Applications using native SSO (Grafana, Harbor, Headlamp, Jellyfin, MinIO, Portainer, Audiobookshelf) have dedicated OAuth2/OIDC providers (`provider_id != None`).
- Applications using forward-proxy auth only (Alertmanager, Bazarr, Homepage, Longhorn, Policy Reporter, Prometheus, Prowlarr, Radarr, SABnzbd, Shlink, Sonarr, Seerr, Tautulli) have `provider_id=None` — they rely on the domain-wide `vollminlab-forward-auth`.
- **A PolicyBinding on a `provider_id=None` Application restricts nothing.** The outpost's nginx check accepts any valid `vollminlab-forward-auth` session cookie, and that cookie covers the whole domain. Policies run only when a provider's own Application authorizes, which for the domain-wide provider is `vollminlab-forward-auth` itself. Verified 2026-10-10 against outpost source 2026.2.2 and with a no-group test account, which reached both FileBrowser and Foundry while both had a group binding.
- **To restrict a forward-auth app to a group, give it a `forward_single` provider** (tofu: `terraform/authentik/providers_proxy.tf`), set it as the Application's `protocol_provider`, add it to the outpost, and bind the group to the Application. The outpost prefers an exact host match over the cookie-domain match, so that host gets its own cookie and its own authorization. FileBrowser and Foundry work this way. Two more pieces are required:
  - The app's Ingress `auth-signin` must be `https://$http_host/outpost.goauthentik.io/start?rd=https://$http_host$escaped_request_uri`, on the app's own host.
  - `/outpost.goauthentik.io` on that host must route to the outpost, via a rule in `clusters/vollminlab-cluster/authentik/authentik-proxy/app/ingress.yaml`. Without it the callback loops: the outpost lets `/outpost.goauthentik.io` paths through the auth check without serving them, so nginx proxies the callback to the app. That loop is why this rule used to say never to use `forward_single`.

## Known limitation: skip_path_regex does not work for forward-auth

`ProxyProvider.skip_path_regex` only applies when the outpost is acting as an embedded proxy. It has no effect on the `auth/nginx` endpoint that nginx calls via `auth_request`. To bypass authentication for specific paths (e.g., WebSocket/socket.io long-poll), use a **path-split ingress**: create a second Ingress object for that path without any Authentik annotations. The more specific path takes precedence in nginx routing.

## Common operations

### Register a new service for forward-auth

Every time a new Ingress gets Authentik forward-auth annotations, create an Application:

```python
from authentik.core.models import Application

app, created = Application.objects.get_or_create(
    slug='<service-slug>',
    defaults=dict(
        name='<Display Name>',
        meta_launch_url='https://<service>.vollminlab.com',
        meta_description='<description>',
        open_in_new_tab=False,
    )
)
print(f'{"created" if created else "exists"}: {app.name} pk={app.pk}')
```

### List all proxy providers and their hosts

```python
from authentik.providers.proxy.models import ProxyProvider
for p in ProxyProvider.objects.all():
    print(p.name, p.external_host, p.forward_auth_mode)
```

### List all applications and their provider assignments

```python
from authentik.core.models import Application
for a in Application.objects.all().order_by('name'):
    print(f'{a.name!r:30s} slug={a.slug!r:25s} provider_id={a.provider_id}')
```

### List outpost assignments

```python
from authentik.outposts.models import Outpost
for o in Outpost.objects.all():
    print(o.name, [p.name for p in o.providers.all()])
```

### Update a service URL (e.g. after rename)

```python
from authentik.core.models import Application
app = Application.objects.get(slug='old-slug')
app.slug = 'new-slug'
app.meta_launch_url = 'https://new-name.vollminlab.com'
app.save()
print(f'Updated: {app.name} -> {app.meta_launch_url}')
```

### Assign a ProxyProvider to the outpost

```python
from authentik.outposts.models import Outpost
from authentik.providers.proxy.models import ProxyProvider

outpost = Outpost.objects.get(name='vollminlab-proxy')
provider = ProxyProvider.objects.get(name='<provider-name>')
outpost.providers.add(provider)
print(f'Added {provider.name} to {outpost.name}')
```

## Checklist: adding a new Authentik-protected service

When adding Authentik forward-auth annotations to a new Ingress:

1. Add the standard annotations to the Ingress manifest (see any existing ingress for the template)
2. **Run the akshell command above** to create an Application entry — do not skip this step
3. Verify: `kubectl logs -n ingress-nginx deployment/ingress-nginx-controller --tail=20 | grep "<hostname>"` — should show 200/302, not 400/500

The Application entry is required even for services that use the domain-wide `forward_domain` provider with `provider_id=None`.
