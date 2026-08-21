# ---------------------------------------------------------------------------
# Cloudflare WAF Custom Rules — Security overrides
#
# Authentik handles all authentication itself, so Cloudflare's managed WAF,
# security level, browser integrity check and rate limiting are skipped for
# authentik.vollminlab.com. Those products do honour this rule — firewall
# events show it matching as `action: skip, source: firewallCustom`.
#
# This rule does NOT stop Bot Fight Mode, which was its original stated purpose
# (#648). Bot Fight Mode runs in its own phase, outside
# http_request_firewall_custom, and has no `products` token, so it is
# unreachable from here. The managed challenge on the /api/v3/flows/executor/
# fetch — HTML instead of JSON, surfacing in the browser as "Request failed and
# the interceptors did not return an alternative response" — kept firing with
# this rule active and enabled. It is fixed in bot-management.tf by turning Bot
# Fight Mode off outright.
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
