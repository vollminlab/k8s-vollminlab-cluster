resource "authentik_application" "alertmanager" {
  name            = "Alertmanager"
  slug            = "alertmanager"
  meta_launch_url = "https://alertmanager.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "audiobookshelf" {
  name              = "Audiobookshelf"
  slug              = "audiobookshelf"
  protocol_provider = authentik_provider_oauth2.audiobookshelf.id
  meta_launch_url   = "https://audiobookshelf.vollminlab.com"
  open_in_new_tab   = false
}

resource "authentik_application" "bazarr" {
  name            = "Bazarr"
  slug            = "bazarr"
  meta_launch_url = "https://bazarr.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "filebrowser" {
  name            = "FileBrowser"
  slug            = "filebrowser"
  meta_launch_url = "https://filebrowser.vollminlab.com"
  meta_icon       = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/filebrowser.svg"
  open_in_new_tab = false
}

resource "authentik_policy_binding" "filebrowser_users" {
  target = authentik_application.filebrowser.uuid
  group  = authentik_group.filebrowser_users.id
  order  = 0
}

resource "authentik_application" "grafana" {
  name              = "Grafana"
  slug              = "grafana"
  protocol_provider = authentik_provider_oauth2.grafana.id
  meta_launch_url   = "https://grafana.vollminlab.com"
  open_in_new_tab   = false
}

resource "authentik_application" "haproxy" {
  name            = "HAProxy"
  slug            = "haproxy"
  meta_launch_url = "https://haproxy.vollminlab.com"
  meta_icon       = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/haproxy.svg"
  open_in_new_tab = false
}

resource "authentik_application" "haproxydmz" {
  name            = "HAProxy DMZ"
  slug            = "haproxydmz"
  meta_launch_url = "https://haproxydmz.vollminlab.com"
  meta_icon       = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/haproxy.svg"
  open_in_new_tab = false
}

resource "authentik_application" "harbor" {
  name              = "Harbor"
  slug              = "harbor"
  protocol_provider = authentik_provider_oauth2.harbor.id
  meta_launch_url   = "https://harbor.vollminlab.com"
  open_in_new_tab   = false
}

resource "authentik_application" "headlamp" {
  name              = "Headlamp"
  slug              = "headlamp"
  protocol_provider = authentik_provider_oauth2.headlamp.id
  meta_launch_url   = "https://headlamp.vollminlab.com"
  open_in_new_tab   = false
}

resource "authentik_application" "homepage" {
  name            = "Homepage"
  slug            = "homepage"
  meta_launch_url = "https://homepage.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "jellyfin" {
  name              = "Jellyfin"
  slug              = "jellyfin"
  protocol_provider = authentik_provider_oauth2.jellyfin.id
  meta_launch_url   = "https://jellyfin.vollminlab.com"
  open_in_new_tab   = false
}

resource "authentik_application" "jellystat" {
  name            = "Jellystat"
  slug            = "jellystat"
  meta_launch_url = "https://jellystat.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "longhorn" {
  name            = "Longhorn"
  slug            = "longhorn"
  meta_launch_url = "https://longhorn.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "minio" {
  name              = "MinIO"
  slug              = "minio"
  protocol_provider = authentik_provider_oauth2.minio.id
  meta_launch_url   = "https://minio.vollminlab.com"
  open_in_new_tab   = false
}

resource "authentik_application" "npm" {
  name            = "Nginx Proxy Manager"
  slug            = "npm"
  meta_launch_url = "https://npm.vollminlab.com"
  meta_icon       = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/nginx-proxy-manager.svg"
  open_in_new_tab = false
}

resource "authentik_application" "pihole" {
  name            = "Pi-hole"
  slug            = "pihole"
  meta_launch_url = "https://pihole.vollminlab.com"
  meta_icon       = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/pi-hole.svg"
  open_in_new_tab = false
}

resource "authentik_application" "policy_reporter" {
  name            = "Policy Reporter"
  slug            = "policy-reporter"
  meta_launch_url = "https://policyreporter.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "portainer" {
  name              = "Portainer"
  slug              = "portainer"
  protocol_provider = authentik_provider_oauth2.portainer.id
  meta_launch_url   = "https://portainer.vollminlab.com"
  open_in_new_tab   = false
}

resource "authentik_application" "prometheus" {
  name            = "Prometheus"
  slug            = "prometheus"
  meta_launch_url = "https://prometheus.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "prowlarr" {
  name            = "Prowlarr"
  slug            = "prowlarr"
  meta_launch_url = "https://prowlarr.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "radarr" {
  name            = "Radarr"
  slug            = "radarr"
  meta_launch_url = "https://radarr.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "readarr" {
  name            = "Readarr"
  slug            = "readarr"
  meta_launch_url = "https://readarr.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "sabnzbd" {
  name            = "SABnzbd"
  slug            = "sabnzbd"
  meta_launch_url = "https://sabnzbd.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "seerr" {
  name              = "Seerr"
  slug              = "seerr"
  protocol_provider = authentik_provider_oauth2.seerr.id
  meta_launch_url   = "https://seerr.vollminlab.com"
  open_in_new_tab   = false
}

resource "authentik_application" "shlink_web" {
  name            = "Shlink Web"
  slug            = "shlink-web"
  meta_launch_url = "https://shlink.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "sonarr" {
  name            = "Sonarr"
  slug            = "sonarr"
  meta_launch_url = "https://sonarr.vollminlab.com"
  open_in_new_tab = false
}

resource "authentik_application" "truenas" {
  name            = "TrueNAS"
  slug            = "truenas"
  meta_launch_url = "https://truenas.vollminlab.com"
  meta_icon       = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/truenas-scale.svg"
  open_in_new_tab = false
}

resource "authentik_application" "vollminlab_forward_auth" {
  name              = "Vollminlab Forward Auth"
  slug              = "vollminlab-forward-auth"
  protocol_provider = authentik_provider_proxy.vollminlab_forward_auth.id
  meta_launch_url   = "https://authentik.vollminlab.com"
  open_in_new_tab   = false
}
