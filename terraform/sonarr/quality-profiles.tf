# Quality profiles imported from Sonarr API
# Retrieved 2026-05-14 via kubectl exec sonarr /api/v3/qualityprofile
# Sonarr v4 quality sources: television, web, webRip, dvd, bluray, televisionRaw, blurayRaw, unknown
# Each profile lists ONLY the qualities it allows, matching Radarr's convention. Sonarr's API
# returns the full quality list per profile with per-quality allowed flags; the provider takes
# the allowed subset and the app keeps the rest flagged not-allowed. An earlier note here
# claimed omitting non-allowed qualities causes drift on import — that was written during the
# 2026-05-14 import and is not what happens; Radarr has always been written this way.
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
# A single-quality group must NOT carry a group-level `name`. Radarr and Sonarr only
# store a name for real multi-quality groups (the WEB tiers, ids 1000-1003); for a group
# of one they return `name: null`, and the provider then fails the apply with
# "Provider produced inconsistent result after apply ... .name: was cty.StringVal(\"CAM\"),
# but now null". The old `ignore_changes = [quality_groups]` masked this.
#
# To verify after any change to this file, read the live order back and confirm index 0
# is the worst quality:
#   kubectl exec -n mediastack <pod> -c <app> -- sh -c \
#     "curl -s -H 'X-Api-Key: $KEY' 'http://localhost:<port>/api/v3/qualityprofile/1'" \
#     | python3 -c "import json,sys;print([i.get('quality',{}).get('name') for i in json.load(sys.stdin)['items']][:4])"
#
# 2160p is deliberately absent from every profile except Ultra-HD. TV is where uncapped
# growth hurts: 3,005 of 3,934 episode files sit below cutoff, and the per-quality size
# ceilings in quality-definitions.tf bound each file, not the total. At the 2160p ceilings
# that backlog tops out around 39 TB; capped at 1080p it is ~17 TB. Free space on the pool
# behind /tv is ~12.8 TiB and is shared with /movies, so 4K on the default profile is not
# something the pool can absorb. Ultra-HD keeps 4K because that is the profile's entire
# purpose; it currently has 0 series, so assigning one is a deliberate act.
#
# Until 2026-08-18 every profile allowed every quality, so five of the six had byte-identical
# allowed sets and three (Any, HD-1080p, HD - 720p/1080p) were indistinguishable in every
# respect. The names described nothing. They are now scoped to match their names, which is why
# renaming alone could never have fixed this — five profiles would have needed the same name.
#
# "Any" is the one genuine rename: #1095 removed 2160p from it for storage reasons, so it is no
# longer "any" and is now "Any - up to 1080p". Nothing references profiles by name except Seerr,
# which stores activeProfileId=6 alongside a display-only activeProfileName — and profile 6
# (HD - 720p/1080p) is deliberately NOT renamed, so Seerr is untouched.
#
# upgrade_allowed is true on every profile. With it false (the previous state) Sonarr treats the
# highest allowed quality as the cutoff, so any existing file "meets cutoff" and nothing is ever
# upgraded. The previous cutoffs compounded this: each was pinned to the *lowest* quality in its
# list (SDTV / HDTV-720p / HDTV-1080p / HDTV-2160p) under a comment saying "cutoff irrelevant",
# which is only true while upgrades are off.

# Profile: Any (id=1) — 82 of 88 series. Everything up to Bluray-1080p Remux; no 2160p.

# Profile: Any - up to 1080p — 82 of 88 series. Everything below 2160p, which #1095 removed for storage reasons — hence the rename: it is no longer "any".
resource "sonarr_quality_profile" "any" {
  name            = "Any - up to 1080p"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p

  quality_groups = [
    {
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
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
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
      qualities = [{ id = 10, name = "Raw-HD", source = "televisionRaw", resolution = 1080 }]
    },
    {
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
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
    {
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
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
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
    {
      qualities = [{ id = 0, name = "Unknown", source = "unknown", resolution = 0 }]
    },
  ]
}

# Profile: SD — unused (0 series). SD sources only.
resource "sonarr_quality_profile" "sd" {
  name            = "SD"
  upgrade_allowed = true
  cutoff          = 22 # Bluray-576p

  quality_groups = [
    {
      qualities = [{ id = 22, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      qualities = [{ id = 13, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
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
      qualities = [{ id = 1, name = "SDTV", source = "television", resolution = 480 }]
    },
  ]
}

# Profile: HD-720p — unused (0 series). 720p only.
resource "sonarr_quality_profile" "hd_720p" {
  name            = "HD-720p"
  upgrade_allowed = true
  cutoff          = 6 # Bluray-720p

  quality_groups = [
    {
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
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
  ]
}

# Profile: HD-1080p — unused (0 series). 1080p only.
resource "sonarr_quality_profile" "hd_1080p" {
  name            = "HD-1080p"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p

  quality_groups = [
    {
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
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
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
  ]
}

# Profile: Ultra-HD — unused (0 series). The only profile permitting 2160p.
resource "sonarr_quality_profile" "ultra_hd" {
  name            = "Ultra-HD"
  upgrade_allowed = true
  cutoff          = 19 # Bluray-2160p

  quality_groups = [
    {
      qualities = [{ id = 21, name = "Bluray-2160p Remux", source = "blurayRaw", resolution = 2160 }]
    },
    {
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
      qualities = [{ id = 16, name = "HDTV-2160p", source = "television", resolution = 2160 }]
    },
  ]
}

# Profile: HD - 720p/1080p — 6 series, and the profile Seerr requests into (activeProfileId=6). 720p and 1080p only.
resource "sonarr_quality_profile" "hd_720p_1080p" {
  name            = "HD - 720p/1080p"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p

  quality_groups = [
    {
      qualities = [{ id = 20, name = "Bluray-1080p Remux", source = "blurayRaw", resolution = 1080 }]
    },
    {
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
      qualities = [{ id = 9, name = "HDTV-1080p", source = "television", resolution = 1080 }]
    },
    {
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
      qualities = [{ id = 4, name = "HDTV-720p", source = "television", resolution = 720 }]
    },
  ]
}
