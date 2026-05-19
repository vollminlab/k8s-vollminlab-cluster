resource "radarr_download_client_qbittorrent" "qbittorrent" {
  name                       = "qBittorrent"
  enable                     = true
  priority                   = 25
  host                       = "qbittorrent"
  port                       = 8080
  use_ssl                    = false
  movie_category             = "movies"
  remove_completed_downloads = true
  remove_failed_downloads    = true
}

resource "radarr_download_client_sabnzbd" "sabnzbd" {
  name                       = "SABnzbd"
  enable                     = true
  priority                   = 1
  host                       = "sabnzbd"
  port                       = 10097
  api_key                    = var.sabnzbd_api_key
  use_ssl                    = false
  movie_category             = "movies"
  recent_movie_priority      = -100
  older_movie_priority       = -100
  remove_completed_downloads = true
  remove_failed_downloads    = true
}
