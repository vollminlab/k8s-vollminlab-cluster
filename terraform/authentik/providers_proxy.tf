resource "authentik_provider_proxy" "vollminlab_forward_auth" {
  name               = "vollminlab-forward-auth"
  external_host      = "https://authentik.vollminlab.com"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  mode               = "forward_domain"
  cookie_domain      = "vollminlab.com"
  skip_path_regex    = "^/api/socket\\.io/"
  # Default is minutes=10. After that the outpost answers with a 302 to sign-in, which a
  # page load follows but an in-page API fetch cannot (cross-origin), so app API calls
  # failed ten minutes after page load. 8h matches the OAuth providers; the trade-off is
  # that a revoked user keeps access to forward-auth apps for up to 8h.
  access_token_validity = "hours=8"
}

# Per-host providers for apps restricted to a group. The domain-wide provider above
# cannot restrict anything: the outpost's nginx check accepts any valid session
# cookie, and policies run only when that provider's own application authorizes.
# A forward_single provider has a cookie for its host alone and is authorized
# against its own application, so that application's PolicyBinding applies.
# The outpost prefers an exact host match over the cookie-domain match.
# Each host needs /outpost.goauthentik.io routed to the outpost:
# clusters/vollminlab-cluster/authentik/authentik-proxy/app/ingress.yaml
#
# cookie_domain MUST be "vollminlab.com" on every provider in this outpost. The outpost's
# filesystem session backend is ONE store shared by all its providers, and each provider
# writes its cookie_domain into that store's options on load; the last one loaded wins.
# An empty value here once made every session cookie host-only, which broke the
# domain-wide callback on authentik.vollminlab.com and looped every forward-auth login.
# Each provider still has its own cookie name, so isolation does not depend on the domain.
resource "authentik_provider_proxy" "filebrowser" {
  name               = "filebrowser-forward-auth"
  external_host      = "https://filebrowser.vollminlab.com"
  cookie_domain      = "vollminlab.com"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  mode               = "forward_single"
}

resource "authentik_provider_proxy" "foundry" {
  name               = "foundry-forward-auth"
  external_host      = "https://foundry.vollminlab.com"
  cookie_domain      = "vollminlab.com"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  mode               = "forward_single"
}
