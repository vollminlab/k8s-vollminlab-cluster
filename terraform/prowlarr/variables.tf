variable "prowlarr_api_key" {
  description = "Prowlarr API key for provider authentication"
  type        = string
  sensitive   = true
}

variable "nzbgeek_api_key" {
  description = "NZBGeek Newznab API key"
  type        = string
  sensitive   = true
}

variable "nzbplanet_api_key" {
  description = "NzbPlanet Newznab API key"
  type        = string
  sensitive   = true
}

variable "radarr_api_key" {
  description = "Radarr API key for application sync connection"
  type        = string
  sensitive   = true
}

variable "readarr_api_key" {
  description = "Readarr API key for application sync connection"
  type        = string
  sensitive   = true
}

variable "sonarr_api_key" {
  description = "Sonarr API key for application sync connection"
  type        = string
  sensitive   = true
}
