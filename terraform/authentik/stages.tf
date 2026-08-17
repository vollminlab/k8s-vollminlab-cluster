resource "authentik_stage_user_login" "default_authentication_login" {
  name               = "default-authentication-login"
  session_duration   = "days=30"
  remember_me_offset = "days=0"
  network_binding    = "no_binding"
  geoip_binding      = "no_binding"
}

# --- Recovery flow stages (new) -----------------------------------------------

# Reuses authentik's built-in password prompt fields rather than declaring its
# own, so the recovery form and the forced-reset form stay identical.
resource "authentik_stage_prompt" "recovery_password" {
  name = "recovery-password-prompt"
  fields = [
    local.prompt_field_password,
    local.prompt_field_password_rpt,
  ]
  validation_policies = [local.password_policy_id]
}

# never_create: a recovery link always carries an existing pending_user. This
# stage must never be able to bring a new account into existence.
resource "authentik_stage_user_write" "recovery_password" {
  name               = "recovery-password-write"
  user_creation_mode = "never_create"
  user_type          = "internal"
}

# --- Force-password-reset stages (existing, adopted via import) ---------------

# The static banner shown above the password fields. Unlike the two password
# fields this one is ours, not an authentik built-in, so it is managed here.
resource "authentik_stage_prompt_field" "reset_password_banner" {
  name          = "reset-password"
  field_key     = "password-reset-prompt"
  label         = "Reset Password"
  type          = "static"
  required      = false
  order         = 0
  placeholder   = "Please reset your password."
  initial_value = "Please reset your password."
}

resource "authentik_stage_prompt" "force_password_reset" {
  name = "force-password-reset-prompt"
  fields = [
    authentik_stage_prompt_field.reset_password_banner.id,
    local.prompt_field_password,
    local.prompt_field_password_rpt,
  ]
  validation_policies = [local.password_policy_id]
}

resource "authentik_stage_user_write" "force_password_reset" {
  name               = "force-password-reset-user-write"
  user_creation_mode = "never_create"
  user_type          = "internal"
}
