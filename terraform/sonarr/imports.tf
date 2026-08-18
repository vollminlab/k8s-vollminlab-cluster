# Import blocks for existing Sonarr resources
# IDs fetched 2026-05-14 via kubectl exec sonarr /api/v3/{qualityprofile,downloadclient}

import {
  to = sonarr_quality_profile.any
  id = "1"
}

import {
  to = sonarr_quality_profile.sd
  id = "2"
}

import {
  to = sonarr_quality_profile.hd_720p
  id = "3"
}

import {
  to = sonarr_quality_profile.hd_1080p
  id = "4"
}

import {
  to = sonarr_quality_profile.ultra_hd
  id = "5"
}

import {
  to = sonarr_quality_profile.hd_720p_1080p
  id = "6"
}

import {
  to = sonarr_download_client_sabnzbd.sabnzbd
  id = "1"
}

# ID fetched 2026-08-08 via kubectl exec sonarr /api/v3/delayprofile
import {
  to = sonarr_delay_profile.default
  id = "1"
}

# Quality definitions — built-in rows that can only be updated, never created,
# so each one needs an import block before tofu will manage it.
# IDs fetched 2026-08-18 via kubectl exec sonarr /api/v3/qualitydefinition

import {
  to = sonarr_quality_definition.raw_hd
  id = "10"
}

import {
  to = sonarr_quality_definition.bluray_1080p_remux
  id = "17"
}

import {
  to = sonarr_quality_definition.webrip_2160p
  id = "19"
}

import {
  to = sonarr_quality_definition.webdl_2160p
  id = "20"
}

import {
  to = sonarr_quality_definition.bluray_2160p
  id = "21"
}

import {
  to = sonarr_quality_definition.bluray_2160p_remux
  id = "22"
}
