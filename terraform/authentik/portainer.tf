resource "portainer_settings" "main" {
  authentication_method = 3

  oauth_settings {
    client_id               = authentik_provider_oauth2.portainer.client_id
    client_secret           = var.portainer_client_secret
    authorization_uri       = "https://authentik.vollminlab.com/application/o/authorize/"
    access_token_uri        = "https://authentik.vollminlab.com/application/o/token/"
    resource_uri            = "https://authentik.vollminlab.com/application/o/userinfo/"
    redirect_uri            = "https://portainer.vollminlab.com"
    user_identifier         = "preferred_username"
    scopes                  = "openid profile email groups"
    sso                     = true
    hide_internal_auth      = true
    oauth_auto_create_users = true
    default_team_id         = 0
  }

  # Portainer's API does not persist hide_internal_auth (it reads back false),
  # so every reconcile re-sent the value and drifted again 10 minutes later.
  # That futile re-apply loop was the source of the authentik-config drift
  # churn (and CNPG WAL bloat). Keep the declared intent but stop the loop.
  lifecycle {
    ignore_changes = [oauth_settings[0].hide_internal_auth]
  }
}
