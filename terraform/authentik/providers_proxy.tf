resource "authentik_provider_proxy" "vollminlab_forward_auth" {
  name               = "vollminlab-forward-auth"
  external_host      = "https://authentik.vollminlab.com"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  mode               = "forward_domain"
  cookie_domain      = "vollminlab.com"
  skip_path_regex    = "^/api/socket\\.io/"
}

# Per-host providers for apps restricted to a group. The domain-wide provider above
# cannot restrict anything: the outpost's nginx check accepts any valid session
# cookie, and policies run only when that provider's own application authorizes.
# A forward_single provider has a cookie for its host alone and is authorized
# against its own application, so that application's PolicyBinding applies.
# The outpost prefers an exact host match over the cookie-domain match.
# Each host needs /outpost.goauthentik.io routed to the outpost:
# clusters/vollminlab-cluster/authentik/authentik-proxy/app/ingress.yaml
resource "authentik_provider_proxy" "filebrowser" {
  name               = "filebrowser-forward-auth"
  external_host      = "https://filebrowser.vollminlab.com"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  mode               = "forward_single"
}

resource "authentik_provider_proxy" "foundry" {
  name               = "foundry-forward-auth"
  external_host      = "https://foundry.vollminlab.com"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  mode               = "forward_single"
}
