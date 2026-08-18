# Per-quality size ceilings (MB per minute of runtime).
# IDs and titles fetched 2026-08-18 via kubectl exec radarr /api/v3/qualitydefinition
#
# Only the tiers that were previously uncapped (max = null, i.e. unlimited) are
# managed here. HDTV/WEBDL/WEBRip-1080p already carry a 100 MB/min cap and are
# deliberately left alone.
#
# These ceilings are what bound library growth now that upgrade_allowed is true.
# A movie below its profile cutoff takes the best allowed release it sees, and
# "Any" — which 519 of 589 movies use — allows every tier up to Remux-2160p.
#
# Reference points, for a 110-minute film:
#   200 MB/min = 22 GB     300 MB/min = 33 GB     350 MB/min = 38 GB
#   250 MB/min = 27 GB     400 MB/min = 44 GB
#
# Every value is kept strictly under 400: Radarr's slider treats 400 as
# "Unlimited", so a literal 400 would defeat the point of setting a cap.

resource "radarr_quality_definition" "bluray_1080p" {
  id       = 22
  title    = "Bluray-1080p"
  min_size = 0
  max_size = 200 # ~22 GB; observed 1080p Blu-rays run 9-21 GB
}

resource "radarr_quality_definition" "remux_1080p" {
  id       = 23
  title    = "Remux-1080p"
  min_size = 0
  max_size = 250 # ~27 GB; observed 1080p remuxes run 32-39 GB, so most are excluded
}

resource "radarr_quality_definition" "hdtv_2160p" {
  id       = 24
  title    = "HDTV-2160p"
  min_size = 0
  max_size = 250 # ~27 GB
}

resource "radarr_quality_definition" "webdl_2160p" {
  id       = 25
  title    = "WEBDL-2160p"
  min_size = 0
  max_size = 250 # ~27 GB; observed 4K WEB-DLs run 20-26 GB, so these still qualify
}

resource "radarr_quality_definition" "webrip_2160p" {
  id       = 26
  title    = "WEBRip-2160p"
  min_size = 0
  max_size = 250 # ~27 GB
}

resource "radarr_quality_definition" "bluray_2160p" {
  id       = 27
  title    = "Bluray-2160p"
  min_size = 0
  max_size = 300 # ~33 GB; admits the x265 encodes, not the 54-63 GB ones
}

resource "radarr_quality_definition" "remux_2160p" {
  id       = 28
  title    = "Remux-2160p"
  min_size = 0
  max_size = 350 # ~38 GB; observed 4K remuxes run 50-90 GB, so in practice this excludes them
}

resource "radarr_quality_definition" "br_disk" {
  id       = 29
  title    = "BR-DISK"
  min_size = 0
  max_size = 300 # ~33 GB; full-disc rips, uncapped until now
}
