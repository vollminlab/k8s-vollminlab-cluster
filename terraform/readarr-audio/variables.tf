variable "readarr_audio_api_key" {
  description = "readarr-audio API key for provider authentication (1P \"Readarr Audio API Key\")"
  type        = string
  sensitive   = true
}

variable "sabnzbd_api_key" {
  description = "SABnzbd API key for download client configuration"
  type        = string
  sensitive   = true
}
