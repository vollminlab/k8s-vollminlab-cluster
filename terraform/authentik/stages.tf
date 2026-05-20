resource "authentik_stage_user_login" "default_authentication_login" {
  name               = "default-authentication-login"
  session_duration   = "days=30"
  remember_me_offset = "days=0"
  network_binding    = "no_binding"
  geoip_binding      = "no_binding"
}
