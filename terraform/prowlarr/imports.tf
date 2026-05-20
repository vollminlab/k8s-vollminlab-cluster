# Import blocks for existing Prowlarr resources
# IDs fetched 2026-05-15 via kubectl exec prowlarr /api/v1/{indexer,applications}

import {
  to = prowlarr_indexer.nzbgeek
  id = "1"
}

import {
  to = prowlarr_indexer.nzbplanet
  id = "2"
}

import {
  to = prowlarr_application_radarr.radarr
  id = "1"
}

import {
  to = prowlarr_application_sonarr.sonarr
  id = "2"
}

import {
  to = prowlarr_indexer.yts
  id = "16"
}
