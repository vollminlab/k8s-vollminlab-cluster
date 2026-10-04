# Same clients as the ebook instance, but separate categories so audiobook and
# ebook downloads never land in each other's completed folders. SABnzbd's
# "audio" category already exists (completes to /downloads/audio).
resource "readarr_download_client_qbittorrent" "qbittorrent" {
  name                       = "qBittorrent"
  enable                     = true
  priority                   = 25
  host                       = "qbittorrent"
  port                       = 8080
  use_ssl                    = false
  book_category              = "audiobooks"
  remove_completed_downloads = true
  remove_failed_downloads    = true
}

resource "readarr_download_client_sabnzbd" "sabnzbd" {
  name                       = "SABnzbd"
  enable                     = true
  priority                   = 1
  host                       = "sabnzbd"
  port                       = 10097
  api_key                    = var.sabnzbd_api_key
  use_ssl                    = false
  book_category              = "audio"
  remove_completed_downloads = true
  remove_failed_downloads    = true
}
