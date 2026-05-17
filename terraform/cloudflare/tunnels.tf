# ---------------------------------------------------------------------------
# Cloudflare Zero Trust Tunnels
#
# All tunnels are TF-created and fully config-managed. The provider generates
# the tunnel secret automatically; tunnel_token is a computed output written
# to a K8s Secret via writeOutputsToSecret on the workspace CR, then sealed
# into a SealedSecret for each cloudflared deployment.
#
# lifecycle.prevent_destroy guards against accidental tunnel deletion that
# would invalidate the sealed token and require manual re-sealing.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tunnels
# ---------------------------------------------------------------------------

resource "cloudflare_zero_trust_tunnel_cloudflared" "authentik" {
  account_id = var.cloudflare_account_id
  name       = "vollminlab-Authentik"
  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "audiobookshelf" {
  account_id = var.cloudflare_account_id
  name       = "vollminlab-Audiobookshelf"
  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "jellyfin" {
  account_id = var.cloudflare_account_id
  name       = "vollminlab-Jellyfin"
  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "nginx" {
  account_id = var.cloudflare_account_id
  name       = "vollminlab-ClusterNginx"
  lifecycle {
    prevent_destroy = true
  }
}

# ---------------------------------------------------------------------------
# Tunnel ingress configurations (fully TF-managed, no lifecycle workarounds)
# ---------------------------------------------------------------------------

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "authentik" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.authentik.id

  config = {
    ingress = [
      {
        hostname = "authentik.vollminlab.com"
        service  = "http://authentik-server.authentik.svc.cluster.local:80"
      },
      {
        service = "http_status:404"
      },
    ]
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "audiobookshelf" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.audiobookshelf.id

  config = {
    ingress = [
      {
        hostname = "audiobookshelf.vollminlab.com"
        service  = "http://audiobookshelf.mediastack.svc.cluster.local:10223"
      },
      {
        service = "http_status:404"
      },
    ]
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "jellyfin" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.jellyfin.id

  config = {
    ingress = [
      {
        hostname = "jellyfin.vollminlab.com"
        service  = "http://jellyfin.mediastack.svc.cluster.local:8096"
      },
      {
        service = "http_status:404"
      },
    ]
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "nginx" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.nginx.id

  config = {
    ingress = [
      {
        hostname = "filebrowser.vollminlab.com"
        service  = "http://ingress-nginx-controller.ingress-nginx.svc.cluster.local:80"
      },
      {
        service = "http_status:404"
      },
    ]
  }
}
