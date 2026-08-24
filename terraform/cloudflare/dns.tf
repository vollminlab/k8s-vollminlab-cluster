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

# Moves Java clients off the default port without anyone having to type one.
#
# A Java client given "minecraft.vollminlab.com" queries
# _minecraft._tcp.<host> for an SRV record first, and only falls back to
# A/AAAA plus port 25565 if there is none. So this record is read entirely on
# the client side before it opens a socket: it reroutes nothing, it just tells
# the client which WAN port to dial. The port translation itself stays on the
# router (57913 -> 192.168.160.4:25565).
#
# One record covers LAN and WAN both. Pi-hole holds no local override for
# minecraft.vollminlab.com (verified 2026-08-23: both 192.168.100.2 and .3
# return 108.5.213.103, the public address), so LAN clients resolve exactly as
# external ones do and hairpin through the router. If an override is ever added
# for this name, this record has to be mirrored into both Pi-holes with port
# 25565, or LAN players break while external ones keep working -- the same
# split-horizon shape as the AAAA leak that hid the tunnel outage for 68 days.
#
# target is dynamic.vollminlab.com, not minecraft.vollminlab.com: RFC 2782
# requires an SRV target to be a real hostname and minecraft.vollminlab.com is
# a CNAME. Pointing at the A record also means the SRV follows the WAN address
# whenever DDNS updates it.
#
# proxied stays false, as it must: Cloudflare's proxy carries HTTP/HTTPS only,
# not the Minecraft TCP protocol.
#
# Java Edition only. Bedrock ignores SRV, so a Bedrock client would need the
# port typed explicitly. Not a concern here -- the server is Paper.
resource "cloudflare_dns_record" "minecraft_srv" {
  zone_id  = var.cloudflare_zone_id
  name     = "_minecraft._tcp.minecraft.vollminlab.com"
  type     = "SRV"
  proxied  = false
  ttl      = 1
  priority = 0

  data = {
    port   = 57913
    target = "dynamic.vollminlab.com"
    weight = 5
  }
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

resource "cloudflare_dns_record" "foundry" {
  zone_id = var.cloudflare_zone_id
  name    = "foundry.vollminlab.com"
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
