# ---------------------------------------------------------------------------
# Cloudflare WAF Custom Rules — Security overrides
#
# Authentik handles all authentication itself, so Cloudflare's bot challenges
# must be bypassed for authentik.vollminlab.com. Without this, a fresh browser
# session (incognito, first visit) gets a managed challenge on the
# /api/v3/flows/executor/ fetch, which returns HTML instead of JSON and
# breaks the login flow with "Request failed and the interceptors did not
# return an alternative response".
# ---------------------------------------------------------------------------

resource "cloudflare_ruleset" "authentik_skip_challenges" {
  zone_id     = var.cloudflare_zone_id
  name        = "Authentik — skip bot challenges"
  description = "Bypass Cloudflare bot/security challenges for authentik.vollminlab.com so the login flow API calls are not intercepted"
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  rules = [
    {
      action = "skip"
      action_parameters = {
        phases   = ["http_ratelimit", "http_request_firewall_managed"]
        products = ["bic", "hot", "rateLimit", "securityLevel", "uaBlock", "waf", "zoneLockdown"]
      }
      expression  = "(http.host eq \"authentik.vollminlab.com\")"
      description = "Skip bot challenges for Authentik — IAM handles its own authentication"
      enabled     = true
    }
  ]
}
