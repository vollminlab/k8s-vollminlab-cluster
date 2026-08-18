# Quality profiles imported from Sonarr API
# Retrieved 2026-05-14 via kubectl exec sonarr /api/v3/qualityprofile
# Sonarr v4 quality sources: television, web, webRip, dvd, bluray, televisionRaw, blurayRaw, unknown
# Note: all 18 quality groups are included in every profile because Sonarr's API always returns
# the full quality list per profile (with per-quality allowed flags). Unlike Radarr, omitting
# non-allowed qualities causes drift on import.
#
# ORDERING IS DELIBERATELY BEST-FIRST. DO NOT "FIX" IT TO READ WORST-FIRST.
#
# Radarr and Sonarr rank quality by position in this list, index 0 = WORST. But the
# devopsarr provider writes the list REVERSED, so what you put here comes out backwards
# in the app. Listing best-first is what produces a correct worst-first ranking live.
#
# Measured 2026-08-18 on two profiles independently: the tf order was worst-first, and
# the live API read back the exact reverse in both cases. The API itself round-trips
# order faithfully (verified by POSTing a scratch profile with a known order and reading
# it back), so the reversal is the provider's, not the API's.
#
# The cost of getting this backwards is not cosmetic. Before this change every profile
# was inverted live: WEBDL-720p outranked Bluray-1080p, so Radarr reported
# `qualityCutoffNotMet: false` for 720p files and rejected all 97 candidate releases with
# "Existing file meets cutoff" — only 1 of ~315 sub-1080p movies was eligible to upgrade.
# In the "Any" profile it also meant WORKPRINT ranked 29 and CAM 28 against Remux-2160p
# at 3: a cam rip beat a 4K remux.
#
# `lifecycle { ignore_changes = [quality_groups] }` used to sit on every profile here and
# is why the inversion survived from the 2026-05-14 import until now. Do not re-add it —
# it hides exactly this class of bug.
#
# To verify after any change to this file, read the live order back and confirm index 0
# is the worst quality:
#   kubectl exec -n mediastack <pod> -c <app> -- sh -c \
#     "curl -s -H 'X-Api-Key: $KEY' 'http://localhost:<port>/api/v3/qualityprofile/1'" \
#     | python3 -c "import json,sys;print([i.get('quality',{}).get('name') for i in json.load(sys.stdin)['items']][:4])"
#
# KNOWN DEFECT, still not addressed here: live state has *every* quality flagged allowed on
# *every* profile, so the names SD / HD-720p / HD-1080p / Ultra-HD / HD - 720p/1080p describe
# nothing — all six differ only by cutoff. Radarr's equivalents are correctly restricted.
# This PR fixes the *ranking* of those qualities, not *which* are allowed; narrowing them
# would drop 4K from the 6 series on HD - 720p/1080p, which is a product decision, not a bug
# fix, so it stays separate.
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

  quality_groups = [
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
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
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
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
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
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
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
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
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
  ]
}

# Profile: SD (id=2) — unused (0 series).
resource "sonarr_quality_profile" "sd" {
  name            = "SD"
  upgrade_allowed = true
  cutoff          = 22 # Bluray-576p

  quality_groups = [
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
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
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
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
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
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
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
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
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
  ]
}

# Profile: HD-720p (id=3) — unused (0 series).
resource "sonarr_quality_profile" "hd_720p" {
  name            = "HD-720p"
  upgrade_allowed = true
  cutoff          = 6 # Bluray-720p

  quality_groups = [
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
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
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
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
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
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
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
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
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
  ]
}

# Profile: HD-1080p (id=4) — unused (0 series).
resource "sonarr_quality_profile" "hd_1080p" {
  name            = "HD-1080p"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p — stops short of Bluray-1080p Remux

  quality_groups = [
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
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
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
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
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
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
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
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
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
  ]
}

# Profile: Ultra-HD (id=5) — unused (0 series).
resource "sonarr_quality_profile" "ultra_hd" {
  name            = "Ultra-HD"
  upgrade_allowed = true
  cutoff          = 19 # Bluray-2160p — stops short of Bluray-2160p Remux

  quality_groups = [
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
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
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
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
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
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
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
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
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
  ]
}

# Profile: HD - 720p/1080p (id=6) — 6 series.
resource "sonarr_quality_profile" "hd_720p_1080p" {
  name            = "HD - 720p/1080p"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p — was 4 (HDTV-720p), which capped this profile at 720p

  quality_groups = [
    {
      name      = "Bluray-2160p Remux"
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
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
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
    {
      name      = "Bluray-1080p Remux"
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
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
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
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
      name      = "Raw-HD"
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 480 }]
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
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      name      = "Unknown"
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
  ]
}
