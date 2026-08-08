variable "radarr_api_key" {
  description = "Radarr API key for provider authentication"
  type        = string
  sensitive   = true
}

variable "sabnzbd_api_key" {
  description = "SABnzbd API key for download client configuration"
  type        = string
  sensitive   = true
}

variable "jellyfin_api_key" {
  description = "Jellyfin API key for the Emby/Jellyfin notification connection"
  type        = string
  sensitive   = true
}
