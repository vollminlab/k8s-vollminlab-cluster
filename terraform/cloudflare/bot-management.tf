# ---------------------------------------------------------------------------
# Cloudflare Bot Management — zone-wide bot posture
#
# Bot Fight Mode is disabled deliberately. Do not turn it back on.
#
# It cannot be scoped or bypassed. Bot Fight Mode issues its managed challenge
# from its own phase — no WAF custom-rule `skip`, IP Access Rule, or hostname
# exemption reaches it, and Cloudflare exposes no `products` token for it. The
# skip rule in security.tf runs in http_request_firewall_custom and never sees
# it, so authentik was challenged for months despite that rule firing correctly.
#
# Measured 2026-08-21 — same URL, two paths:
#
#   /api/v3/flows/executor/default-authentication-flow/
#     via Cloudflare : 403  text/html         (cf-mitigated: challenge)
#     via LAN        : 200  application/json
#
# firewallEventsAdaptive attributed it to `source: botFight`, action
# `managed_challenge`.
#
# A browser solves a managed challenge; a native app cannot. Swiftfin, the
# Jellyfin mobile clients and every other non-browser client took a hard 403 the
# moment they left the LAN. LAN clients masked it completely, because Pi-hole
# points them at the ingress VIP instead of through the tunnel — the same
# masking that hid the tunnel-origin 502 for 68 days (#1058).
#
# What still guards this zone:
#   - Every exposed service authenticates for itself (Authentik forward-auth,
#     native OIDC, or the app's own login).
#   - The Cloudflare Managed Free Ruleset stays on, and is landing blocks.
#   - AI training crawlers stay blocked (ai_training = "block").
#
# NOTE: the Cloudflare Terraform API token needs the **Bot Management Write**
# permission group (zone scope) for this resource. Without it every plan and
# apply fails with `10000 Authentication error`, because the token cannot even
# read the current bot config.
#
# Free plan — the sbfm_* attributes are Pro and above, and must stay unset.
# Attributes not listed here (content_bots_protection, crawler_protection,
# is_robots_txt_managed, cf_robots_variant, aisearch, ai_user, ai_agents) are
# already at their defaults and are deliberately left unmanaged. All are
# Optional+Computed, so leaving them unset keeps the live value without a diff.
#
# AI Crawl Control, 2026-11-23: Cloudflare split the single ai_bots_protection
# switch into per-category fields (ai_training, aisearch, ai_user, ai_agents)
# and migrated this zone. ai_bots_protection now reads back "disabled" whatever
# is written, so setting it fails every apply with "Provider produced
# inconsistent result". Do not set it again. The migration also mapped our old
# "block" to ai_training = "disallow", which is only a robots.txt request, so
# ai_training is pinned to "block" below to restore an actual block. The new
# fields need provider >= 5.27.
# ---------------------------------------------------------------------------

resource "cloudflare_bot_management" "vollminlab" {
  zone_id = var.cloudflare_zone_id

  fight_mode = false

  # Pinned to the values measured live on 2026-08-21 so this resource cannot
  # silently reset them.
  enable_js = true

  ai_training = "block"
}
