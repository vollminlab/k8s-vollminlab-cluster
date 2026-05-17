resource "readarr_root_folder" "audiobooks" {
  path                            = "/audiobooks"
  name                            = "Audiobooks"
  default_metadata_profile_id     = 1
  default_quality_profile_id      = 1
  default_monitor_option          = "all"
  default_monitor_new_item_option = "all"
  is_calibre_library              = false
  output_profile                  = "default"
}

resource "readarr_root_folder" "books" {
  path                            = "/books"
  name                            = "Books"
  default_metadata_profile_id     = 1
  default_quality_profile_id      = 1
  default_monitor_option          = "all"
  default_monitor_new_item_option = "all"
  is_calibre_library              = false
  output_profile                  = "default"
}
