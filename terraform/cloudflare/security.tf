# ---------------------------------------------------------------------------
# Cloudflare WAF Custom Rules — Security overrides
#
# Some services behind the tunnel are accessed by non-browser API clients that
# cannot solve a Cloudflare managed/JS challenge. When Cloudflare intercepts
# their requests it returns the "Just a moment..." HTML challenge page
# (cf-mitigated: challenge) instead of the expected response, which breaks the
# client. These hosts must have Cloudflare's bot/security challenges skipped.
#
# - authentik.vollminlab.com: a fresh browser session (incognito, first visit)
#   gets a managed challenge on the /api/v3/flows/executor/ fetch, which returns
#   HTML instead of JSON and breaks the login flow with "Request failed and the
#   interceptors did not return an alternative response". Authentik handles all
#   authentication itself, so the challenge is redundant.
# - jellyfin.vollminlab.com: native clients (Apple TV / tvOS, mobile apps)
#   cannot solve JS challenges. Without this skip, /Videos/.../stream requests
#   get a 403 challenge page and playback never starts (stops at 0 ms).
#   Jellyfin has its own authentication. See docs/runbooks/jellyfin.md.
#
# NOTE: http_request_firewall_custom is an entrypoint ruleset — only ONE may
# exist per zone, so every host skip must be a rule within this single ruleset.
# ---------------------------------------------------------------------------

resource "cloudflare_ruleset" "authentik_skip_challenges" {
  zone_id     = var.cloudflare_zone_id
  name        = "Skip bot challenges for API/native clients"
  description = "Bypass Cloudflare bot/security challenges for hosts whose clients cannot solve a managed challenge (Authentik login API, Jellyfin native players)"
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
    },
    {
      action = "skip"
      action_parameters = {
        phases   = ["http_ratelimit", "http_request_firewall_managed"]
        products = ["bic", "hot", "rateLimit", "securityLevel", "uaBlock", "waf", "zoneLockdown"]
      }
      expression  = "(http.host eq \"jellyfin.vollminlab.com\")"
      description = "Skip bot challenges for Jellyfin — native players (Apple TV/mobile) cannot solve JS challenges; Jellyfin handles its own authentication"
      enabled     = true
    }
  ]
}
