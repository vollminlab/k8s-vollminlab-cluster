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
}
