# ---------------------------------------------------------------------------
# Tunnel token outputs
#
# The workspace CR writes these to a K8s Secret via writeOutputsToSecret.
# Seal that Secret with kubeseal to produce the SealedSecrets used by each
# cloudflared deployment. Delete the plain Secret after sealing.
# ---------------------------------------------------------------------------

output "authentik_tunnel_token" {
  value     = cloudflare_zero_trust_tunnel_cloudflared.authentik.tunnel_token
  sensitive = true
}

output "audiobookshelf_tunnel_token" {
  value     = cloudflare_zero_trust_tunnel_cloudflared.audiobookshelf.tunnel_token
  sensitive = true
}

output "jellyfin_tunnel_token" {
  value     = cloudflare_zero_trust_tunnel_cloudflared.jellyfin.tunnel_token
  sensitive = true
}

output "nginx_tunnel_token" {
  value     = cloudflare_zero_trust_tunnel_cloudflared.nginx.tunnel_token
  sensitive = true
}
