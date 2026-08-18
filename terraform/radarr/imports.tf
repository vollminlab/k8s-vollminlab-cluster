# Import blocks for existing Radarr resources
# IDs fetched 2026-05-14 via kubectl exec radarr /api/v3/{qualityprofile,downloadclient}

import {
  to = radarr_quality_profile.any
  id = "1"
}

import {
  to = radarr_quality_profile.sd
  id = "2"
}

import {
  to = radarr_quality_profile.hd_720p
  id = "3"
}

import {
  to = radarr_quality_profile.hd_1080p
  id = "4"
}

import {
  to = radarr_quality_profile.ultra_hd
  id = "5"
}

import {
  to = radarr_quality_profile.hd_720p_1080p
  id = "6"
}

import {
  to = radarr_download_client_sabnzbd.sabnzbd
  id = "1"
}

# ID fetched 2026-08-08 via kubectl exec radarr /api/v3/delayprofile
import {
  to = radarr_delay_profile.default
  id = "1"
}

# Quality definitions — built-in rows that can only be updated, never created,
# so each one needs an import block before tofu will manage it.
# IDs fetched 2026-08-18 via kubectl exec radarr /api/v3/qualitydefinition

import {
  to = radarr_quality_definition.bluray_1080p
  id = "22"
}

import {
  to = radarr_quality_definition.remux_1080p
  id = "23"
}

import {
  to = radarr_quality_definition.hdtv_2160p
  id = "24"
}

import {
  to = radarr_quality_definition.webdl_2160p
  id = "25"
}

import {
  to = radarr_quality_definition.webrip_2160p
  id = "26"
}

import {
  to = radarr_quality_definition.bluray_2160p
  id = "27"
}

import {
  to = radarr_quality_definition.remux_2160p
  id = "28"
}

import {
  to = radarr_quality_definition.br_disk
  id = "29"
}
