resource "sonarr_notification_emby" "jellyfin" {
  name = "Jellyfin"

  host    = "jellyfin.mediastack.svc.cluster.local"
  port    = 8096
  use_ssl = false
  api_key = var.jellyfin_api_key

  on_grab                            = false
  on_download                        = true
  on_upgrade                         = true
  on_rename                          = false
  on_series_delete                   = false
  on_episode_file_delete             = false
  on_episode_file_delete_for_upgrade = false
  on_health_issue                    = false
  on_application_update              = false

  include_health_warnings = false
  update_library          = true
}
