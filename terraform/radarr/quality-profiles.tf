# Quality profiles imported from Radarr API
# Retrieved 2026-05-14 via kubectl exec radarr /api/v3/qualityprofile
# language id=1 = English
#
# upgrade_allowed is true on every profile. With it false (the previous state)
# Radarr treats the *highest allowed quality* as the cutoff, so any existing file
# "meets cutoff" and no release is ever grabbed as a replacement — a 720p file
# stays 720p forever no matter how many searches you run.
#
# cutoff is the quality at which upgrading stops. Items *below* cutoff take the
# best allowed release they see, which can be 2160p on the profiles that allow it
# — per-quality size ceilings in quality-definitions.tf are what bound that.

# Profile: Any (id=1) — 519 of 589 movies. All qualities allowed, up to Remux-2160p.
resource "radarr_quality_profile" "any" {
  name            = "Any"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p — stop upgrading once a 1080p Blu-ray is in place
  lifecycle { ignore_changes = [quality_groups] }

  language = {
    id = 1
  }

  quality_groups = [
    {
      name      = "WORKPRINT"
      qualities = [{ id = 24, name = "WORKPRINT", source = "workprint", resolution = 0 }]
    },
    {
      name      = "CAM"
      qualities = [{ id = 25, name = "CAM", source = "cam", resolution = 0 }]
    },
    {
      name      = "TELESYNC"
      qualities = [{ id = 26, name = "TELESYNC", source = "telesync", resolution = 0 }]
    },
    {
      name      = "TELECINE"
      qualities = [{ id = 27, name = "TELECINE", source = "telecine", resolution = 0 }]
    },
    {
      name      = "REGIONAL"
      qualities = [{ id = 29, name = "REGIONAL", source = "dvd", resolution = 480 }]
    },
    {
      name      = "DVDSCR"
      qualities = [{ id = 28, name = "DVDSCR", source = "dvd", resolution = 480 }]
    },
    {
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "tv", resolution = 480 }]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 0 }]
    },
    {
      name      = "DVD-R"
      qualities = [{ id = 23, name = "DVD-R", source = "dvd", resolution = 480 }]
    },
    {
      id   = 1000
      name = "WEB 480p"
      qualities = [
        { id = 8, name = "WEBDL-480p", source = "webdl", resolution = 480 },
        { id = 12, name = "WEBRip-480p", source = "webrip", resolution = 480 },
      ]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 20, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 21, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "tv", resolution = 720 }]
    },
    {
      id   = 1001
      name = "WEB 720p"
      qualities = [
        { id = 5, name = "WEBDL-720p", source = "webdl", resolution = 720 },
        { id = 14, name = "WEBRip-720p", source = "webrip", resolution = 720 },
      ]
    },
    {
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "tv", resolution = 1080 }]
    },
    {
      id   = 1002
      name = "WEB 1080p"
      qualities = [
        { id = 3, name = "WEBDL-1080p", source = "webdl", resolution = 1080 },
        { id = 15, name = "WEBRip-1080p", source = "webrip", resolution = 1080 },
      ]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
    },
    {
      name      = "Remux-1080p"
      qualities = [{ id = 30, name = "Remux-1080p", source = "bluray", resolution = 1080 }]
    },
    {
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "tv", resolution = 2160 }]
    },
    {
      id   = 1003
      name = "WEB 2160p"
      qualities = [
        { id = 18, name = "WEBDL-2160p", source = "webdl", resolution = 2160 },
        { id = 17, name = "WEBRip-2160p", source = "webrip", resolution = 2160 },
      ]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
    },
    {
      name      = "Remux-2160p"
      qualities = [{ id = 31, name = "Remux-2160p", source = "bluray", resolution = 2160 }]
    },
  ]
}

# Profile: SD (id=2) — unused (0 movies). SD and WEB 480p / Bluray 480p/576p.
resource "radarr_quality_profile" "sd" {
  name            = "SD"
  upgrade_allowed = true
  cutoff          = 21 # Bluray-576p — top of this profile's own list
  lifecycle { ignore_changes = [quality_groups] }

  language = {
    id = 1
  }

  quality_groups = [
    {
      name      = "WORKPRINT"
      qualities = [{ id = 24, name = "WORKPRINT", source = "workprint", resolution = 0 }]
    },
    {
      name      = "CAM"
      qualities = [{ id = 25, name = "CAM", source = "cam", resolution = 0 }]
    },
    {
      name      = "TELESYNC"
      qualities = [{ id = 26, name = "TELESYNC", source = "telesync", resolution = 0 }]
    },
    {
      name      = "TELECINE"
      qualities = [{ id = 27, name = "TELECINE", source = "telecine", resolution = 0 }]
    },
    {
      name      = "REGIONAL"
      qualities = [{ id = 29, name = "REGIONAL", source = "dvd", resolution = 480 }]
    },
    {
      name      = "DVDSCR"
      qualities = [{ id = 28, name = "DVDSCR", source = "dvd", resolution = 480 }]
    },
    {
      name      = "SDTV"
      qualities = [{ id = 1, name = "SDTV", source = "tv", resolution = 480 }]
    },
    {
      name      = "DVD"
      qualities = [{ id = 2, name = "DVD", source = "dvd", resolution = 0 }]
    },
    {
      id   = 1000
      name = "WEB 480p"
      qualities = [
        { id = 8, name = "WEBDL-480p", source = "webdl", resolution = 480 },
        { id = 12, name = "WEBRip-480p", source = "webrip", resolution = 480 },
      ]
    },
    {
      name      = "Bluray-480p"
      qualities = [{ id = 20, name = "Bluray-480p", source = "bluray", resolution = 480 }]
    },
    {
      name      = "Bluray-576p"
      qualities = [{ id = 21, name = "Bluray-576p", source = "bluray", resolution = 576 }]
    },
  ]
}

# Profile: HD-720p (id=3) — unused (0 movies). 720p qualities only.
resource "radarr_quality_profile" "hd_720p" {
  name            = "HD-720p"
  upgrade_allowed = true
  cutoff          = 6 # Bluray-720p — top of this profile's own list
  lifecycle { ignore_changes = [quality_groups] }

  language = {
    id = 1
  }

  quality_groups = [
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "tv", resolution = 720 }]
    },
    {
      id   = 1001
      name = "WEB 720p"
      qualities = [
        { id = 5, name = "WEBDL-720p", source = "webdl", resolution = 720 },
        { id = 14, name = "WEBRip-720p", source = "webrip", resolution = 720 },
      ]
    },
    {
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
    },
  ]
}

# Profile: HD-1080p (id=4) — unused (0 movies). 1080p qualities only.
resource "radarr_quality_profile" "hd_1080p" {
  name            = "HD-1080p"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p — stops short of Remux-1080p (32-39 GB)
  lifecycle { ignore_changes = [quality_groups] }

  language = {
    id = 1
  }

  quality_groups = [
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "tv", resolution = 1080 }]
    },
    {
      id   = 1002
      name = "WEB 1080p"
      qualities = [
        { id = 3, name = "WEBDL-1080p", source = "webdl", resolution = 1080 },
        { id = 15, name = "WEBRip-1080p", source = "webrip", resolution = 1080 },
      ]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
    },
    {
      name      = "Remux-1080p"
      qualities = [{ id = 30, name = "Remux-1080p", source = "bluray", resolution = 1080 }]
    },
  ]
}

# Profile: Ultra-HD (id=5) — unused (0 movies). 4K qualities only.
resource "radarr_quality_profile" "ultra_hd" {
  name            = "Ultra-HD"
  upgrade_allowed = true
  cutoff          = 19 # Bluray-2160p — stops short of Remux-2160p (50-90 GB)
  lifecycle { ignore_changes = [quality_groups] }

  language = {
    id = 1
  }

  quality_groups = [
    {
      name      = "HDTV-2160p"
      qualities = [{ id = 16, name = "HDTV-2160p", source = "tv", resolution = 2160 }]
    },
    {
      id   = 1003
      name = "WEB 2160p"
      qualities = [
        { id = 18, name = "WEBDL-2160p", source = "webdl", resolution = 2160 },
        { id = 17, name = "WEBRip-2160p", source = "webrip", resolution = 2160 },
      ]
    },
    {
      name      = "Bluray-2160p"
      qualities = [{ id = 19, name = "Bluray-2160p", source = "bluray", resolution = 2160 }]
    },
    {
      name      = "Remux-2160p"
      qualities = [{ id = 31, name = "Remux-2160p", source = "bluray", resolution = 2160 }]
    },
  ]
}

# Profile: HD - 720p/1080p (id=6) — 70 movies. 720p and 1080p qualities.
resource "radarr_quality_profile" "hd_720p_1080p" {
  name            = "HD - 720p/1080p"
  upgrade_allowed = true
  cutoff          = 7 # Bluray-1080p — was 6 (Bluray-720p), which capped this profile at 720p
  lifecycle { ignore_changes = [quality_groups] }

  language = {
    id = 1
  }

  quality_groups = [
    {
      name      = "HDTV-720p"
      qualities = [{ id = 4, name = "HDTV-720p", source = "tv", resolution = 720 }]
    },
    {
      id   = 1001
      name = "WEB 720p"
      qualities = [
        { id = 5, name = "WEBDL-720p", source = "webdl", resolution = 720 },
        { id = 14, name = "WEBRip-720p", source = "webrip", resolution = 720 },
      ]
    },
    {
      name      = "Bluray-720p"
      qualities = [{ id = 6, name = "Bluray-720p", source = "bluray", resolution = 720 }]
    },
    {
      name      = "HDTV-1080p"
      qualities = [{ id = 9, name = "HDTV-1080p", source = "tv", resolution = 1080 }]
    },
    {
      id   = 1002
      name = "WEB 1080p"
      qualities = [
        { id = 3, name = "WEBDL-1080p", source = "webdl", resolution = 1080 },
        { id = 15, name = "WEBRip-1080p", source = "webrip", resolution = 1080 },
      ]
    },
    {
      name      = "Bluray-1080p"
      qualities = [{ id = 7, name = "Bluray-1080p", source = "bluray", resolution = 1080 }]
    },
    {
      name      = "Remux-1080p"
      qualities = [{ id = 30, name = "Remux-1080p", source = "bluray", resolution = 1080 }]
    },
  ]
}
