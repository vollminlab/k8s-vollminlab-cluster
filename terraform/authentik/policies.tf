# Forced password reset on next login.
#
# Existing live configuration, adopted via import blocks — this is not a new
# behaviour. The mechanism:
#
#   1. an admin sets a temporary password on the user and sets the custom
#      attribute  reset_password: true
#   2. at next login, reset-password-check gates the extra prompt stage into the
#      authentication flow (order 25), forcing a password change
#   3. reset-password-update gates the user-write stage (order 26) and clears the
#      attribute, so the prompt appears exactly once
#
# Both expressions return False when pending_user is absent, which is why the
# bindings in flows.tf must evaluate policies at stage time rather than at plan
# time — see the comment there.

resource "authentik_policy_expression" "reset_password_check" {
  name       = "reset-password-check"
  expression = <<-EOT
    pending_user = request.context.get("pending_user")
    if pending_user is None:
        return False
    return pending_user.attributes.get("reset_password", False) == True
  EOT
}

resource "authentik_policy_expression" "reset_password_update" {
  name       = "reset-password-update"
  expression = <<-EOT
    pending_user = request.context.get("pending_user")
    if pending_user is None:
        return False
    if not pending_user.attributes.get("reset_password", False):
        return False
    pending_user.attributes["reset_password"] = False
    return True
  EOT
}

resource "authentik_policy_binding" "reset_password_check" {
  target = authentik_flow_stage_binding.force_password_reset_prompt.id
  policy = authentik_policy_expression.reset_password_check.id
  order  = 0
}

resource "authentik_policy_binding" "reset_password_update" {
  target = authentik_flow_stage_binding.force_password_reset_write.id
  policy = authentik_policy_expression.reset_password_update.id
  order  = 0
}
