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
