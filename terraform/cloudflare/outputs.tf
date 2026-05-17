# ---------------------------------------------------------------------------
# Tunnel ID outputs
#
# The workspace CR writes these to a K8s Secret via writeOutputsToSecret.
# Use the IDs to fetch the actual tunnel tokens via the CF API post-apply:
#
#   curl -H "Authorization: Bearer $CF_TOKEN" \
#     "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/token"
#
# The response `result.token` value is what gets sealed into each cloudflared
# deployment's SealedSecret.
# ---------------------------------------------------------------------------

output "authentik_tunnel_id" {
  value = cloudflare_zero_trust_tunnel_cloudflared.authentik.id
}

output "audiobookshelf_tunnel_id" {
  value = cloudflare_zero_trust_tunnel_cloudflared.audiobookshelf.id
}

output "jellyfin_tunnel_id" {
  value = cloudflare_zero_trust_tunnel_cloudflared.jellyfin.id
}

output "nginx_tunnel_id" {
  value = cloudflare_zero_trust_tunnel_cloudflared.nginx.id
}
