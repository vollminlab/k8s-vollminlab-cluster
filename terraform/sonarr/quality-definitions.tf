# Per-quality size ceilings (MB per minute of runtime).
# IDs and titles fetched 2026-08-18 via kubectl exec sonarr /api/v3/qualitydefinition
#
# Only the tiers that were previously uncapped (max = null, i.e. unlimited) are
# managed here. The 1080p tiers already carry sane caps (HDTV 125, WEBRip/WEBDL 130,
# Bluray 155) and HDTV-2160p is at Sonarr's own 199.9 default — all left untouched.
#
# TV is where uncapped growth hurts most: 3,934 episode files at 1.37 GB average
# today, and every Sonarr profile allows every quality up to Bluray-2160p Remux
# (see the KNOWN DEFECT note in quality-profiles.tf). These ceilings are the only
# thing bounding that now that upgrade_allowed is true.
#
# Reference points, for a 45-minute episode:
#   200 MB/min = 9 GB      300 MB/min = 13.5 GB
#   250 MB/min = 11 GB     350 MB/min = 15.75 GB
#
# min_size and preferred_size are carried over from live state unchanged; only
# max_size is being introduced.

resource "sonarr_quality_definition" "raw_hd" {
  id             = 10
  title          = "Raw-HD"
  min_size       = 4
  max_size       = 200
  preferred_size = 95
}

resource "sonarr_quality_definition" "bluray_1080p_remux" {
  id             = 17
  title          = "Bluray-1080p Remux"
  min_size       = 35
  max_size       = 200
  preferred_size = 95
}

resource "sonarr_quality_definition" "webrip_2160p" {
  id             = 19
  title          = "WEBRip-2160p"
  min_size       = 35
  max_size       = 250
  preferred_size = 95
}

resource "sonarr_quality_definition" "webdl_2160p" {
  id             = 20
  title          = "WEBDL-2160p"
  min_size       = 35
  max_size       = 250
  preferred_size = 95
}

resource "sonarr_quality_definition" "bluray_2160p" {
  id             = 21
  title          = "Bluray-2160p"
  min_size       = 35
  max_size       = 300
  preferred_size = 95
}

resource "sonarr_quality_definition" "bluray_2160p_remux" {
  id             = 22
  title          = "Bluray-2160p Remux"
  min_size       = 35
  max_size       = 350
  preferred_size = 95
}
