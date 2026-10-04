# Ebooks only. The /audiobooks root folder moved to the readarr-audio instance
# (terraform/readarr-audio) on 2026-10-04, and this instance no longer mounts it.
#
# Monitor "none": nothing is downloaded unless it is explicitly added or searched
# for. With "all", every author's whole bibliography became wanted (1,046 books)
# and RSS sync grabbed them continuously.
resource "readarr_root_folder" "books" {
  path                            = "/books"
  name                            = "Books"
  default_metadata_profile_id     = 1
  default_quality_profile_id      = 1
  default_monitor_option          = "none"
  default_monitor_new_item_option = "none"
  is_calibre_library              = false
  output_profile                  = "default"
}
