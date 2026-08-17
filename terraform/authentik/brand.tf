# The default brand. Adopted via import purely so flow_recovery can be set —
# that field is what makes "Create recovery link" work in the admin UI.
#
# READ BEFORE EDITING: every flow_* and branding_* field below is spelled out on
# purpose. The provider sends the whole object on update, so a field omitted here
# is written back as null/default. Dropping flow_authentication would leave the
# instance with no login flow. Values match the live object as of 2026-08-13;
# the branding_* ones are authentik's own defaults, restated rather than inferred.
resource "authentik_brand" "default" {
  domain  = "authentik-default"
  default = true

  flow_authentication = data.authentik_flow.default_authentication.id
  flow_invalidation   = data.authentik_flow.default_invalidation.id
  flow_user_settings  = data.authentik_flow.default_user_settings.id
  flow_recovery       = authentik_flow.recovery.uuid

  branding_title                   = "authentik"
  branding_logo                    = "/static/dist/assets/icons/icon_left_brand.svg"
  branding_favicon                 = "/static/dist/assets/icons/icon.png"
  branding_default_flow_background = "/static/dist/assets/images/flow_background.jpg"
}
