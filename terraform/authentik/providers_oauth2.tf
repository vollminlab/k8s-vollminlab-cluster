locals {
  common_property_mappings = [
    data.authentik_property_mapping_provider_scope.email.id,
    data.authentik_property_mapping_provider_scope.openid.id,
    data.authentik_property_mapping_provider_scope.profile.id,
  ]
}

resource "authentik_provider_oauth2" "grafana" {
  name               = "Grafana"
  client_id          = "rArLch2402M3G4HWq4eqmyt0B2EThCIyX5M6CHFG" # gitleaks:allow
  client_secret      = var.grafana_client_secret
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  signing_key        = data.authentik_certificate_key_pair.self_signed.id
  sub_mode           = "hashed_user_id"
  logout_uri         = "https://grafana.vollminlab.com/login"

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      url           = "https://grafana.vollminlab.com/login/generic_oauth"
    }
  ]

  property_mappings = concat(local.common_property_mappings, [
    authentik_property_mapping_provider_scope.groups.id,
  ])
}

resource "authentik_provider_oauth2" "headlamp" {
  name               = "Headlamp"
  client_id          = "cUhVNsmF0sJ3RvXKAxlfBXTndNHXhV7HbyPjjQYm" # gitleaks:allow
  client_secret      = var.headlamp_client_secret
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  signing_key        = data.authentik_certificate_key_pair.self_signed.id
  sub_mode           = "hashed_user_id"
  logout_uri         = "https://headlamp.vollminlab.com"

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      url           = "https://headlamp.vollminlab.com/oidc-callback"
    }
  ]

  property_mappings = concat(local.common_property_mappings, [
    data.authentik_property_mapping_provider_scope.offline_access.id,
    authentik_property_mapping_provider_scope.groups.id,
  ])
}

resource "authentik_provider_oauth2" "minio" {
  name               = "MinIO"
  client_id          = "GKq5oNsz9lgsa1kIOCM7uTa4qIBVe6SUsfVjeFCN" # gitleaks:allow
  client_secret      = var.minio_client_secret
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  signing_key        = data.authentik_certificate_key_pair.self_signed.id
  sub_mode           = "hashed_user_id"
  logout_uri         = "https://minio.vollminlab.com"

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      url           = "https://minio.vollminlab.com/oauth_callback"
    }
  ]

  property_mappings = concat(local.common_property_mappings, [
    authentik_property_mapping_provider_scope.minio_policy_claim.id,
  ])
}

resource "authentik_provider_oauth2" "jellyfin" {
  name               = "Jellyfin"
  client_id          = "OTxNz2JtIupVY33uMgUm6qw68r3hRaYiCzbDfa53" # gitleaks:allow
  client_secret      = var.jellyfin_client_secret
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  signing_key        = data.authentik_certificate_key_pair.self_signed.id
  sub_mode           = "hashed_user_id"
  logout_uri         = "https://jellyfin.vollminlab.com"

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      url           = "https://jellyfin.vollminlab.com/sso/OID/redirect/authentik"
    }
  ]

  property_mappings = concat(local.common_property_mappings, [
    authentik_property_mapping_provider_scope.groups.id,
  ])
}

resource "authentik_provider_oauth2" "harbor" {
  name               = "Harbor"
  client_id          = "61knXoFusnE1LOVJLSSRZkLtnLFak5NylhhOxDBx" # gitleaks:allow
  client_secret      = var.harbor_client_secret
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  signing_key        = data.authentik_certificate_key_pair.self_signed.id
  sub_mode           = "hashed_user_id"
  logout_uri         = "https://harbor.vollminlab.com"

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      url           = "https://harbor.vollminlab.com/c/oidc/callback"
    }
  ]

  property_mappings = concat(local.common_property_mappings, [
    authentik_property_mapping_provider_scope.groups.id,
  ])
}

resource "authentik_provider_oauth2" "portainer" {
  name               = "Portainer"
  client_id          = "f7hkfRgncvwxtWo1BwLl86FQo8i3GEJo7dTJhpSi" # gitleaks:allow
  client_secret      = var.portainer_client_secret
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  signing_key        = data.authentik_certificate_key_pair.self_signed.id
  sub_mode           = "hashed_user_id"
  logout_uri         = "https://portainer.vollminlab.com"

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      url           = "https://portainer.vollminlab.com"
    }
  ]

  property_mappings = concat(local.common_property_mappings, [
    authentik_property_mapping_provider_scope.groups.id,
  ])
}

resource "authentik_provider_oauth2" "seerr" {
  name               = "Seerr"
  client_id          = "EF795DE125C3E104B7ABAE521BA14E14AADB448C" # gitleaks:allow
  client_secret      = var.seerr_client_secret
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  signing_key        = data.authentik_certificate_key_pair.self_signed.id
  sub_mode           = "hashed_user_id"

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      url           = "https://seerr.vollminlab.com/login"
    },
    {
      matching_mode = "strict"
      url           = "https://seerr.vollminlab.com/profile/settings/linked-accounts"
    },
  ]

  property_mappings = local.common_property_mappings
}

resource "authentik_provider_oauth2" "audiobookshelf" {
  name               = "Audiobookshelf"
  client_id          = "8FBzOT0SL5Kz1brCSW25Uuyr71TvQYvvfsBA9f7I" # gitleaks:allow
  client_secret      = var.audiobookshelf_client_secret
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_provider_invalidation.id
  signing_key        = data.authentik_certificate_key_pair.self_signed.id
  sub_mode           = "hashed_user_id"
  logout_uri         = "https://audiobookshelf.vollminlab.com"

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      url           = "https://audiobookshelf.vollminlab.com/auth/openid/callback"
    },
    {
      matching_mode = "strict"
      url           = "https://audiobookshelf.vollminlab.com/auth/openid/mobile-redirect"
    },
    {
      matching_mode = "strict"
      url           = "https://audiobookshelf.vollminlab.com/audiobookshelf/auth/openid/callback"
    },
    {
      matching_mode = "strict"
      url           = "https://audiobookshelf.vollminlab.com/audiobookshelf/auth/openid/mobile-redirect"
    },
  ]

  property_mappings = concat(local.common_property_mappings, [
    authentik_property_mapping_provider_scope.groups.id,
    authentik_property_mapping_provider_scope.audiobookshelf_policy_claim.id,
  ])
}
