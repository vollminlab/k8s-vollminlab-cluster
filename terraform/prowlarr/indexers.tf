# Prowlarr indexers: Newznab usenet (NZBgeek, NzbPlanet) imported 2026-05-15;
# Cardigann torrent (1337x, EZTV, YTS) created 2026-05-19.
# Newznab indexers use ignore_changes on fields: Prowlarr masks sensitive API keys on read-back.
# Cardigann indexers: specify only non-null, non-info-type field values (provider omits these).
# 1337x additionally uses ignore_changes: provider bug causes hash mismatch on definition-specific
# Cardigann fields (downloadlink, sort, type, disablesort) in sensitive-containing sets.

resource "prowlarr_indexer" "eztv" {
  name            = "EZTV"
  enable          = true
  priority        = 25
  protocol        = "torrent"
  implementation  = "Cardigann"
  config_contract = "CardigannSettings"
  app_profile_id  = 1
  tags            = [prowlarr_tag.flaresolverr.id]

  fields = [
    { name = "definitionFile", text_value = "eztv" },
    { name = "baseSettings.limitsUnit", number_value = 0 },
    { name = "torrentBaseSettings.preferMagnetUrl", bool_value = false },
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
  tags            = [prowlarr_tag.flaresolverr.id]

  # ignore_changes on fields: the devopsarr/prowlarr provider has a bug where
  # definition-specific Cardigann fields (downloadlink, sort, type, etc.) cause
  # a post-apply hash mismatch on sensitive-containing sets. The indexer is
  # correctly configured in Prowlarr; we suppress drift detection to avoid
  # the destroy-recreate loop.
  lifecycle { ignore_changes = [fields] }

  fields = [
    { name = "definitionFile", text_value = "1337x" },
    { name = "baseSettings.limitsUnit", number_value = 0 },
    { name = "torrentBaseSettings.preferMagnetUrl", bool_value = false },
    { name = "downloadlink", number_value = 0 },
    { name = "downloadlink2", number_value = 1 },
    { name = "disablesort", bool_value = false },
    { name = "sort", number_value = 2 },
    { name = "type", number_value = 1 },
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

  fields = [
    { name = "definitionFile", text_value = "yts" },
    { name = "apiurl", text_value = "movies-api.accel.li" },
    { name = "baseSettings.limitsUnit", number_value = 0 },
    { name = "torrentBaseSettings.preferMagnetUrl", bool_value = false },
  ]
}
