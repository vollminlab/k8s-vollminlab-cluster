# ---------------------------------------------------------------------------
# DNS records for vollminlab.com
#
# Managed entirely by Terraform — do NOT create or modify records in the
# Cloudflare dashboard. See docs/cloudflare-management.md for the full
# ownership matrix and instructions for adding new external services.
#
# Exceptions (NOT managed here):
#   _acme-challenge TXT records — created/deleted automatically by cert-manager
#   plex.vollminlab.com         — deleted; stale record removed from CF dashboard
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Dynamic DNS anchor — WAN IP updated by DDNS client, not Terraform
# lifecycle.ignore_changes on content prevents Terraform from overwriting the
# current IP on every plan. The record itself is TF-owned (type, proxied, etc).
# ---------------------------------------------------------------------------

resource "cloudflare_dns_record" "dynamic" {
  zone_id = var.cloudflare_zone_id
  name    = "dynamic.vollminlab.com"
  type    = "A"
  content = "71.187.111.78"
  proxied = false
  ttl     = 60
  lifecycle {
    ignore_changes = [content]
  }
}

# ---------------------------------------------------------------------------
# DDNS-relative CNAMEs (follow dynamic.vollminlab.com → WAN IP)
# ---------------------------------------------------------------------------

resource "cloudflare_dns_record" "apex" {
  zone_id = var.cloudflare_zone_id
  name    = "vollminlab.com"
  type    = "CNAME"
  content = "dynamic.vollminlab.com"
  proxied = false
  ttl     = 1
}

resource "cloudflare_dns_record" "bluemap" {
  zone_id = var.cloudflare_zone_id
  name    = "bluemap.vollminlab.com"
  type    = "CNAME"
  content = "dynamic.vollminlab.com"
  proxied = false
  ttl     = 1
}

resource "cloudflare_dns_record" "mastersleague" {
  zone_id = var.cloudflare_zone_id
  name    = "mastersleague.vollminlab.com"
  type    = "CNAME"
  content = "dynamic.vollminlab.com"
  proxied = false
  ttl     = 1
}

resource "cloudflare_dns_record" "minecraft" {
  zone_id = var.cloudflare_zone_id
  name    = "minecraft.vollminlab.com"
  type    = "CNAME"
  content = "dynamic.vollminlab.com"
  proxied = false
  ttl     = 1
}

resource "cloudflare_dns_record" "vpn" {
  zone_id = var.cloudflare_zone_id
  name    = "vpn.vollminlab.com"
  type    = "CNAME"
  content = "vollminlab.com"
  proxied = false
  ttl     = 1
}

# ---------------------------------------------------------------------------
# Cloudflare Tunnel CNAMEs (proxied — all tunnel traffic goes through CF)
# ---------------------------------------------------------------------------

resource "cloudflare_dns_record" "authentik" {
  zone_id = var.cloudflare_zone_id
  name    = "authentik.vollminlab.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.authentik.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

resource "cloudflare_dns_record" "audiobookshelf" {
  zone_id = var.cloudflare_zone_id
  name    = "audiobookshelf.vollminlab.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.audiobookshelf.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

resource "cloudflare_dns_record" "filebrowser" {
  zone_id = var.cloudflare_zone_id
  name    = "filebrowser.vollminlab.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.nginx.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

resource "cloudflare_dns_record" "jellyfin" {
  zone_id = var.cloudflare_zone_id
  name    = "jellyfin.vollminlab.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.jellyfin.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}
