resource "authentik_property_mapping_provider_scope" "groups" {
  name       = "OpenID  'groups'"
  scope_name = "groups"
  expression = "return list(request.user.ak_groups.values_list(\"name\", flat=True))"
}

resource "authentik_property_mapping_provider_scope" "minio_policy" {
  name        = "MinIO Policy"
  description = "Mapping for Minio Admins"
  scope_name  = "minio"
  expression  = <<-EOT
    if ak_is_group_member(request.user, name="MinIO Admins"):
        return {"policy": "consoleAdmin"}
    return {"policy": "readwrite"}
  EOT
}

resource "authentik_property_mapping_provider_scope" "minio_policy_claim" {
  name       = "MinIO Policy Claim"
  scope_name = "profile"
  expression = <<-EOT
    if ak_is_group_member(request.user, name="MinIO Admins"):
        return {"policy": "consoleAdmin"}
    return {"policy": "readwrite"}
  EOT
}

resource "authentik_property_mapping_provider_scope" "audiobookshelf_policy" {
  name       = "Audiobookshelf Policy"
  scope_name = "audiobookshelf"
  expression = <<-EOT
    if ak_is_group_member(request.user, name="Audiobookshelf Admins"):
      return {"groups": ["admin"]}
    if ak_is_group_member(request.user, name="Audiobookshelf Users"):
      return {"groups": ["user"]}
  EOT
}

resource "authentik_property_mapping_provider_scope" "audiobookshelf_policy_claim" {
  name       = "Audiobookshelf Policy Claim"
  scope_name = "profile"
  expression = <<-EOT
    if ak_is_group_member(request.user, name="Audiobookshelf Admins"):
        return {"groups": ["admin"]}
    if ak_is_group_member(request.user, name="Audiobookshelf Users"):
        return {"groups": ["user"]}
    return {"groups": ["user"]}
  EOT
}

# Headlamp forwards the user's OIDC id_token to the kube-apiserver as a Bearer
# token, and the apiserver runs with --oidc-username-claim=email. Kubernetes
# applies a special rule to that one claim (verified against the v1.34 source,
# staging/src/k8s.io/apiserver/plugin/pkg/authenticator/token/oidc/oidc.go):
# when the username comes from `email`, an `email_verified` claim that is
# explicitly false is rejected with `oidc: email not verified`. A *missing*
# email_verified is accepted; only an explicit false fails.
#
# authentik 2026.8.0 (deployed 2026-08-19 03:33) changed its built-in email
# scope mapping to return `"email_verified": False` — it does not actually run
# an email-verification flow, so the old hardcoded True was an overstatement.
# That flipped every Headlamp login to a 401 at the apiserver while authentik
# itself reported a successful sign-in.
#
# The built-in mapping is blueprint-managed
# (managed = "goauthentik.io/providers/oauth2/scope-email"), so editing it in
# place is reverted on the next authentik restart or upgrade, and it is shared
# by every other provider. This custom mapping is attached to the Headlamp
# provider *instead of* the built-in one, restoring exactly the pre-2026.8.0
# behaviour for that provider alone.
#
# On asserting True: authentik performs no email verification, so this is the
# cluster vouching that the address on its own admin account is genuine, which
# is the same assertion authentik itself shipped as the default until 2026.8.0.
# It is scoped to Headlamp and to k8s API authentication. Omitting the claim
# entirely would also satisfy k8s today, but asserting it stays correct if the
# structured AuthenticationConfiguration path ever requires the claim present.
resource "authentik_property_mapping_provider_scope" "headlamp_email" {
  name        = "Headlamp OpenID 'email'"
  description = "email scope asserting email_verified, required by kube-apiserver --oidc-username-claim=email"
  scope_name  = "email"
  expression  = <<-EOT
    return {
        "email": request.user.email,
        "email_verified": True,
    }
  EOT
}
