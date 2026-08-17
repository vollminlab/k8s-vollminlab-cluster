locals {
  # Authentik built-in objects referenced by the password stages below.
  #
  # All three are pinned by UUID rather than looked up, for two reasons:
  #
  #   1. The provider pinned in versions.tf (goauthentik/authentik 2026.2.1) has
  #      no data source for either kind. authentik_stage_prompt_field exists as a
  #      data source only on the provider's main branch, and password policies
  #      have never had one (authentik_policy_expression covers expression
  #      policies only, and this is a PasswordPolicy).
  #   2. All three are shared with authentik's own default-password-change flow.
  #      Managing them as resources here would mean an edit aimed at our stages
  #      silently rewrote a built-in flow.
  #
  # Captured from the live instance 2026-08-13. If these ever drift, re-read them
  # with: ak shell -c "from authentik.stages.prompt.models import Prompt; ..."
  #
  # The gitleaks:allow markers are required, not cosmetic. These are public
  # object IDs, but the generic-api-key rule fires on any high-entropy value on a
  # line whose name contains "password". The same UUIDs already appear throughout
  # imports.tf without tripping it, because `id = "..."` carries no trigger word.
  password_policy_id        = "878c9441-9a33-418c-b205-9d3f30101154" # default-password-change-password-policy gitleaks:allow
  prompt_field_password     = "b27678a9-cc84-43b7-ac17-5de7e809e597" # default-password-change-field-password gitleaks:allow
  prompt_field_password_rpt = "887b0965-339c-4870-adae-3ed9ffb68135" # default-password-change-field-password-repeat gitleaks:allow
}
