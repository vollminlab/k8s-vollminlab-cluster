resource "harbor_config_auth" "oidc" {
  auth_mode          = "oidc_auth"
  oidc_name          = "Authentik"
  oidc_endpoint      = "https://authentik.vollminlab.com/application/o/harbor/"
  oidc_client_id     = "61knXoFusnE1LOVJLSSRZkLtnLFak5NylhhOxDBx" # gitleaks:allow
  oidc_client_secret = var.harbor_oidc_client_secret
  # Harbor reports the username claim it actually onboarded with
  # (preferred_username). Declaring it here matches live state; omitting it
  # let the provider default (name) plan a perpetual oidc_user_claim -> null.
  oidc_user_claim    = "preferred_username"
  oidc_scope         = "openid,profile,email,groups"
  oidc_groups_claim  = "groups"
  oidc_admin_group   = "Harbor Admins"
  oidc_auto_onboard  = true
  oidc_verify_cert   = true
  primary_auth_mode  = true
}
