# Prowlarr indexers: Newznab usenet (NZBgeek, NzbPlanet) imported 2026-05-15;
# Cardigann torrent (1337x, EZTV, YTS) created 2026-05-19.
# Sensitive fields use ignore_changes to prevent drift from Prowlarr's masked API responses.

resource "prowlarr_indexer" "eztv" {
  name            = "EZTV"
  enable          = true
  priority        = 25
  protocol        = "torrent"
  implementation  = "Cardigann"
  config_contract = "CardigannSettings"
  app_profile_id  = 1

  lifecycle { ignore_changes = [fields] }

  fields = [
    { name = "definitionFile", text_value = "eztv" },
  ]
}

resource "prowlarr_indexer" "nzbgeek" {
  name            = "NZBgeek"
  enable          = true
  priority        = 25
  protocol        = "usenet"
  implementation  = "Newznab"
  config_contract = "NewznabSettings"
  app_profile_id  = 1

  lifecycle { ignore_changes = [fields] }

  fields = [
    { name = "baseUrl", text_value = "https://api.nzbgeek.info" },
    { name = "apiPath", text_value = "/api" },
    { name = "apiKey", sensitive_value = var.nzbgeek_api_key },
  ]
}

resource "prowlarr_indexer" "nzbplanet" {
  name            = "NzbPlanet"
  enable          = true
  priority        = 25
  protocol        = "usenet"
  implementation  = "Newznab"
  config_contract = "NewznabSettings"
  app_profile_id  = 1

  lifecycle { ignore_changes = [fields] }

  fields = [
    { name = "baseUrl", text_value = "https://api.nzbplanet.net" },
    { name = "apiPath", text_value = "/api" },
    { name = "apiKey", sensitive_value = var.nzbplanet_api_key },
  ]
}

resource "prowlarr_indexer" "the1337x" {
  name            = "1337x"
  enable          = true
  priority        = 25
  protocol        = "torrent"
  implementation  = "Cardigann"
  config_contract = "CardigannSettings"
  app_profile_id  = 1

  lifecycle { ignore_changes = [fields] }

  fields = [
    { name = "definitionFile", text_value = "1337x" },
  ]
}

resource "prowlarr_indexer" "yts" {
  name            = "YTS"
  enable          = true
  priority        = 25
  protocol        = "torrent"
  implementation  = "Cardigann"
  config_contract = "CardigannSettings"
  app_profile_id  = 1

  lifecycle { ignore_changes = [fields] }

  fields = [
    { name = "definitionFile", text_value = "yts" },
  ]
}
