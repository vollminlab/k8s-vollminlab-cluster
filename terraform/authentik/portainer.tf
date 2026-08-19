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

# The Kubernetes environment Portainer manages. Portainer runs in-cluster and
# talks to the API server with the portainer-sa-clusteradmin ServiceAccount, so
# this is a local Kubernetes environment (type 5) — no agent, no credentials.
#
# This was the one piece of Portainer's configuration that lived only in
# portainer.db. When the database was lost on 2026-08-19 the OAuth settings
# above came back on the next reconcile and this did not, because nothing
# declared it. Registering it here closes that gap: a wiped database now
# rebuilds to a working Portainer without anyone clicking through the setup
# wizard.
resource "portainer_environment" "local" {
  name = "local"
  type = 5

  # Portainer rewrites an empty URL to this for type 5 regardless of what is
  # sent, so declaring the same value is what keeps the plan empty.
  environment_address = "https://kubernetes.default.svc"
  group_id            = 1

  # On update the provider mirrors environment_address into PublicURL whenever
  # public_ip is unset, then reads it straight back into public_ip — so leaving
  # it undeclared produces a diff on every 10-minute reconcile, forever. Same
  # failure mode as hide_internal_auth above; same remedy. PublicURL only
  # decorates published-port links, which a Kubernetes environment never uses.
  lifecycle {
    ignore_changes = [public_ip]
  }
}
