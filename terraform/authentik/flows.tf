# Password-recovery flow.
#
# Exists so an admin can generate a one-time recovery link from the Authentik
# admin UI (Users -> a user -> Create recovery link) and hand it to the person
# out of band. They set their own password; no temporary password is ever
# transmitted.
#
# Deliberately NOT wired into the login page: the identification stage
# (default-authentication-identification) has recovery_flow = null, so no
# "Forgot password?" link is rendered. Self-service recovery would need an email
# stage, and this instance has no SMTP configured.
#
# authentication = require_unauthenticated matches how recovery links are used
# (opened by a logged-out user). Note the side effect: an admin who is already
# signed in cannot open the link to test it — use a private window.
resource "authentik_flow" "recovery" {
  name           = "recovery-flow"
  title          = "Reset your password"
  slug           = "recovery-flow"
  designation    = "recovery"
  authentication = "require_unauthenticated"
}

resource "authentik_flow_stage_binding" "recovery_prompt" {
  target = authentik_flow.recovery.uuid
  stage  = authentik_stage_prompt.recovery_password.id
  order  = 10
}

resource "authentik_flow_stage_binding" "recovery_write" {
  target = authentik_flow.recovery.uuid
  stage  = authentik_stage_user_write.recovery_password.id
  order  = 20
}

# --- Existing force-password-reset machinery on the login flow -----------------
#
# These two bindings already exist in the live instance and are adopted via
# import blocks; they are not new. Together with the policies in policies.tf they
# implement: set a temp password + user.attributes.reset_password = true, and the
# user is forced to change it at next login.
#
# evaluate_on_plan / re_evaluate_policies are pinned to the live values because
# both differ from the provider defaults, and both matter. The gating policies
# read request.context["pending_user"], which is not populated at flow-plan time
# — evaluating on plan would decide the stage's fate before the user has been
# identified. They must be evaluated when the stage is reached instead.
resource "authentik_flow_stage_binding" "force_password_reset_prompt" {
  target                  = data.authentik_flow.default_authentication.id
  stage                   = authentik_stage_prompt.force_password_reset.id
  order                   = 25
  evaluate_on_plan        = false
  re_evaluate_policies    = true
  policy_engine_mode      = "any"
  invalid_response_action = "retry"
}

resource "authentik_flow_stage_binding" "force_password_reset_write" {
  target                  = data.authentik_flow.default_authentication.id
  stage                   = authentik_stage_user_write.force_password_reset.id
  order                   = 26
  evaluate_on_plan        = false
  re_evaluate_policies    = true
  policy_engine_mode      = "any"
  invalid_response_action = "retry"
}
