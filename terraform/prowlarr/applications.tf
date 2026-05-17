# Application sync connections imported from Prowlarr API
# Retrieved 2026-05-15 via kubectl exec prowlarr /api/v1/applications

resource "prowlarr_application_radarr" "radarr" {
  name            = "Radarr"
  sync_level      = "fullSync"
  prowlarr_url    = "http://prowlarr.mediastack.svc.cluster.local:9696"
  base_url        = "http://radarr.mediastack.svc.cluster.local:7878"
  api_key         = var.radarr_api_key
  sync_categories = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080, 2090]
}

resource "prowlarr_application_readarr" "readarr" {
  name            = "Readarr"
  sync_level      = "fullSync"
  prowlarr_url    = "http://prowlarr.mediastack.svc.cluster.local:9696"
  base_url        = "http://readarr.mediastack.svc.cluster.local:8787"
  api_key         = var.readarr_api_key
  sync_categories = [3030, 7000, 7010, 7020, 7030, 7040, 7050, 7060]
}

resource "prowlarr_application_sonarr" "sonarr" {
  name                  = "Sonarr"
  sync_level            = "fullSync"
  prowlarr_url          = "http://prowlarr.mediastack.svc.cluster.local:9696"
  base_url              = "http://sonarr.mediastack.svc.cluster.local:8989"
  api_key               = var.sonarr_api_key
  sync_categories       = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5090]
  anime_sync_categories = [5070]
}
