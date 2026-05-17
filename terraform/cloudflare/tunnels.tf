# ---------------------------------------------------------------------------
# Cloudflare Zero Trust Tunnels
#
# All tunnels are TF-created and fully config-managed. The random_bytes
# secret is generated once at creation and stored in TF state. The
# tunnel_token output (derived from account_id + tunnel_id + secret) is
# written to a K8s Secret via writeOutputsToSecret on the workspace CR,
# then sealed into a SealedSecret for the cloudflared deployment.
#
# lifecycle.prevent_destroy guards against accidental tunnel deletion that
# would invalidate the sealed token and require manual re-sealing.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tunnel secrets (generated once, stable for the lifetime of each tunnel)
# ---------------------------------------------------------------------------

resource "random_bytes" "authentik_tunnel_secret" {
  length = 32
}

resource "random_bytes" "audiobookshelf_tunnel_secret" {
  length = 32
}

resource "random_bytes" "jellyfin_tunnel_secret" {
  length = 32
}

resource "random_bytes" "nginx_tunnel_secret" {
  length = 32
}

# ---------------------------------------------------------------------------
# Tunnels
# ---------------------------------------------------------------------------

resource "cloudflare_zero_trust_tunnel_cloudflared" "authentik" {
  account_id = var.cloudflare_account_id
  name       = "vollminlab-Authentik"
  secret     = random_bytes.authentik_tunnel_secret.base64
  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "audiobookshelf" {
  account_id = var.cloudflare_account_id
  name       = "vollminlab-Audiobookshelf"
  secret     = random_bytes.audiobookshelf_tunnel_secret.base64
  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "jellyfin" {
  account_id = var.cloudflare_account_id
  name       = "vollminlab-Jellyfin"
  secret     = random_bytes.jellyfin_tunnel_secret.base64
  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "nginx" {
  account_id = var.cloudflare_account_id
  name       = "vollminlab-ClusterNginx"
  secret     = random_bytes.nginx_tunnel_secret.base64
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
    ingress_rule = [
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
    ingress_rule = [
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
    ingress_rule = [
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
    ingress_rule = [
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
