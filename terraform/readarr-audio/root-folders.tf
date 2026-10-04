# Bookshelf seeds a fresh database with the "eBook" and "Spoken" quality profiles
# and the "Standard" metadata profile; look them up by name rather than assuming ids.
data "readarr_quality_profile" "spoken" {
  name = "Spoken"
}

data "readarr_metadata_profile" "standard" {
  name = "Standard"
}

# The Audiobookshelf library. Nothing is monitored by default: this instance
# downloads only what is explicitly added or searched for.
resource "readarr_root_folder" "audiobooks" {
  path                            = "/audiobooks"
  name                            = "Audiobooks"
  default_metadata_profile_id     = data.readarr_metadata_profile.standard.id
  default_quality_profile_id      = data.readarr_quality_profile.spoken.id
  default_monitor_option          = "none"
  default_monitor_new_item_option = "none"
  is_calibre_library              = false
  output_profile                  = "default"
}
