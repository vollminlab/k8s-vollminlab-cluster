# Default delay profile (tags = [] is the built-in, non-deletable profile).
# Torrent delay raised to 60 min so usenet (already delay-free) wins the race
# on releases available via both protocols.
resource "sonarr_delay_profile" "default" {
  tags = []

  preferred_protocol                  = "usenet"
  enable_usenet                       = true
  enable_torrent                      = true
  usenet_delay                        = 0
  torrent_delay                       = 60
  bypass_if_highest_quality           = true
  bypass_if_above_custom_format_score = false
  minimum_custom_format_score         = 0
  order                               = 2147483647
}
