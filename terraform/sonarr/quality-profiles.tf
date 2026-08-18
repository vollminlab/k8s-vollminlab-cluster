# Quality profiles imported from Sonarr API
# Retrieved 2026-05-14 via kubectl exec sonarr /api/v3/qualityprofile
# Sonarr v4 quality sources: television, web, webRip, dvd, bluray, televisionRaw, blurayRaw, unknown
# Note: all 18 quality groups are included in every profile because Sonarr's API always returns
# the full quality list per profile (with per-quality allowed flags). Unlike Radarr, omitting
# non-allowed qualities causes drift on import.
#
# KNOWN DEFECT, not addressed here: live state has *every* quality flagged allowed on *every*
# profile (verified 2026-08-18 against /api/v3/qualityprofile), so the names SD / HD-720p /
# HD-1080p / Ultra-HD / HD - 720p/1080p currently describe nothing — all six behave identically
# and differ only by cutoff. Radarr's equivalent profiles are still correctly restricted.
# Restoring the per-profile quality lists means dropping the ignore_changes below and rewriting
# quality_groups, which reorders quality ranking for the whole TV library — its own PR.
#
# upgrade_allowed is true on every profile. With it false (the previous state) Sonarr treats the
# highest allowed quality as the cutoff, so any existing file "meets cutoff" and nothing is ever
# upgraded. The previous cutoffs compounded this: each was pinned to the *lowest* quality in its
# list (SDTV / HDTV-720p / HDTV-1080p / HDTV-2160p) under a comment saying "cutoff irrelevant",
# which is only true while upgrades are off.

# Profile: Any (id=1) — 82 of 88 series. Every quality is allowed live, up to Bluray-2160p Remux.
resource "sonarr_quality_profile" "any" {
  name            = "Any"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p — stop upgrading once a 1080p Blu-ray is in place
  lifecycle { ignore_changes = [quality_groups] }

  quality_groups = [
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
    {
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      id   = 1000
      name = "WEB 480p"
      qualities = [
        { id = 12, name = "WEBRip-480p", source = "webRip", resolution = 480 },
        { id = 8, name = "WEBDL-480p", source = "web", resolution = 480 },
      ]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      id   = 1001
      name = "WEB 720p"
      qualities = [
        { id = 14, name = "WEBRip-720p", source = "webRip", resolution = 720 },
        { id = 5, name = "WEBDL-720p", source = "web", resolution = 720 },
      ]
    },
    {
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
    },
    {
      id   = 1002
      name = "WEB 1080p"
      qualities = [
        { id = 15, name = "WEBRip-1080p", source = "webRip", resolution = 1080 },
        { id = 3, name = "WEBDL-1080p", source = "web", resolution = 1080 },
      ]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      id   = 1003
      name = "WEB 2160p"
      qualities = [
        { id = 17, name = "WEBRip-2160p", source = "webRip", resolution = 2160 },
        { id = 18, name = "WEBDL-2160p", source = "web", resolution = 2160 },
      ]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
  ]
}

# Profile: SD (id=2) — unused (0 series).
resource "sonarr_quality_profile" "sd" {
  name            = "SD"
  upgrade_allowed = true
  cutoff          = 22 # Bluray-576p
  lifecycle { ignore_changes = [quality_groups] }

  quality_groups = [
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
    {
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      id   = 1000
      name = "WEB 480p"
      qualities = [
        { id = 12, name = "WEBRip-480p", source = "webRip", resolution = 480 },
        { id = 8, name = "WEBDL-480p", source = "web", resolution = 480 },
      ]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      id   = 1001
      name = "WEB 720p"
      qualities = [
        { id = 14, name = "WEBRip-720p", source = "webRip", resolution = 720 },
        { id = 5, name = "WEBDL-720p", source = "web", resolution = 720 },
      ]
    },
    {
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
    },
    {
      id   = 1002
      name = "WEB 1080p"
      qualities = [
        { id = 15, name = "WEBRip-1080p", source = "webRip", resolution = 1080 },
        { id = 3, name = "WEBDL-1080p", source = "web", resolution = 1080 },
      ]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      id   = 1003
      name = "WEB 2160p"
      qualities = [
        { id = 17, name = "WEBRip-2160p", source = "webRip", resolution = 2160 },
        { id = 18, name = "WEBDL-2160p", source = "web", resolution = 2160 },
      ]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
  ]
}

# Profile: HD-720p (id=3) — unused (0 series).
resource "sonarr_quality_profile" "hd_720p" {
  name            = "HD-720p"
  upgrade_allowed = true
  cutoff          = 6 # Bluray-720p
  lifecycle { ignore_changes = [quality_groups] }

  quality_groups = [
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
    {
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      id   = 1000
      name = "WEB 480p"
      qualities = [
        { id = 12, name = "WEBRip-480p", source = "webRip", resolution = 480 },
        { id = 8, name = "WEBDL-480p", source = "web", resolution = 480 },
      ]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      id   = 1001
      name = "WEB 720p"
      qualities = [
        { id = 14, name = "WEBRip-720p", source = "webRip", resolution = 720 },
        { id = 5, name = "WEBDL-720p", source = "web", resolution = 720 },
      ]
    },
    {
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
    },
    {
      id   = 1002
      name = "WEB 1080p"
      qualities = [
        { id = 15, name = "WEBRip-1080p", source = "webRip", resolution = 1080 },
        { id = 3, name = "WEBDL-1080p", source = "web", resolution = 1080 },
      ]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      id   = 1003
      name = "WEB 2160p"
      qualities = [
        { id = 17, name = "WEBRip-2160p", source = "webRip", resolution = 2160 },
        { id = 18, name = "WEBDL-2160p", source = "web", resolution = 2160 },
      ]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
  ]
}

# Profile: HD-1080p (id=4) — unused (0 series).
resource "sonarr_quality_profile" "hd_1080p" {
  name            = "HD-1080p"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p — stops short of Bluray-1080p Remux
  lifecycle { ignore_changes = [quality_groups] }

  quality_groups = [
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
    {
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      id   = 1000
      name = "WEB 480p"
      qualities = [
        { id = 12, name = "WEBRip-480p", source = "webRip", resolution = 480 },
        { id = 8, name = "WEBDL-480p", source = "web", resolution = 480 },
      ]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      id   = 1001
      name = "WEB 720p"
      qualities = [
        { id = 14, name = "WEBRip-720p", source = "webRip", resolution = 720 },
        { id = 5, name = "WEBDL-720p", source = "web", resolution = 720 },
      ]
    },
    {
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
    },
    {
      id   = 1002
      name = "WEB 1080p"
      qualities = [
        { id = 15, name = "WEBRip-1080p", source = "webRip", resolution = 1080 },
        { id = 3, name = "WEBDL-1080p", source = "web", resolution = 1080 },
      ]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      id   = 1003
      name = "WEB 2160p"
      qualities = [
        { id = 17, name = "WEBRip-2160p", source = "webRip", resolution = 2160 },
        { id = 18, name = "WEBDL-2160p", source = "web", resolution = 2160 },
      ]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
  ]
}

# Profile: Ultra-HD (id=5) — unused (0 series).
resource "sonarr_quality_profile" "ultra_hd" {
  name            = "Ultra-HD"
  upgrade_allowed = true
  cutoff          = 19 # Bluray-2160p — stops short of Bluray-2160p Remux
  lifecycle { ignore_changes = [quality_groups] }

  quality_groups = [
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
    {
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      id   = 1000
      name = "WEB 480p"
      qualities = [
        { id = 12, name = "WEBRip-480p", source = "webRip", resolution = 480 },
        { id = 8, name = "WEBDL-480p", source = "web", resolution = 480 },
      ]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      id   = 1001
      name = "WEB 720p"
      qualities = [
        { id = 14, name = "WEBRip-720p", source = "webRip", resolution = 720 },
        { id = 5, name = "WEBDL-720p", source = "web", resolution = 720 },
      ]
    },
    {
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
    },
    {
      id   = 1002
      name = "WEB 1080p"
      qualities = [
        { id = 15, name = "WEBRip-1080p", source = "webRip", resolution = 1080 },
        { id = 3, name = "WEBDL-1080p", source = "web", resolution = 1080 },
      ]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      id   = 1003
      name = "WEB 2160p"
      qualities = [
        { id = 17, name = "WEBRip-2160p", source = "webRip", resolution = 2160 },
        { id = 18, name = "WEBDL-2160p", source = "web", resolution = 2160 },
      ]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
  ]
}

# Profile: HD - 720p/1080p (id=6) — 6 series.
resource "sonarr_quality_profile" "hd_720p_1080p" {
  name            = "HD - 720p/1080p"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p — was 4 (HDTV-720p), which capped this profile at 720p
  lifecycle { ignore_changes = [quality_groups] }

  quality_groups = [
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
    {
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      id   = 1000
      name = "WEB 480p"
      qualities = [
        { id = 12, name = "WEBRip-480p", source = "webRip", resolution = 480 },
        { id = 8, name = "WEBDL-480p", source = "web", resolution = 480 },
      ]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      id   = 1001
      name = "WEB 720p"
      qualities = [
        { id = 14, name = "WEBRip-720p", source = "webRip", resolution = 720 },
        { id = 5, name = "WEBDL-720p", source = "web", resolution = 720 },
      ]
    },
    {
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
    },
    {
      id   = 1002
      name = "WEB 1080p"
      qualities = [
        { id = 15, name = "WEBRip-1080p", source = "webRip", resolution = 1080 },
        { id = 3, name = "WEBDL-1080p", source = "web", resolution = 1080 },
      ]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      id   = 1003
      name = "WEB 2160p"
      qualities = [
        { id = 17, name = "WEBRip-2160p", source = "webRip", resolution = 2160 },
        { id = 18, name = "WEBDL-2160p", source = "web", resolution = 2160 },
      ]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
  ]
}
