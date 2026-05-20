# Prowlarr indexers: Newznab usenet (NZBgeek, NzbPlanet) imported 2026-05-15;
# Cardigann torrent (1337x, EZTV, YTS) created 2026-05-19.
# Newznab indexers use ignore_changes on fields to tolerate Prowlarr masking sensitive API keys.
# Cardigann indexers specify only non-null field values — the provider omits null-value fields
# from state, so including them in the plan causes a hash mismatch on post-CREATE read.

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

  fields = [
    { name = "definitionFile", text_value = "1337x" },
    { name = "baseSettings.limitsUnit", number_value = 0 },
    { name = "torrentBaseSettings.preferMagnetUrl", bool_value = false },
    { name = "info_uploader", text_value = "You can filter by Uploader by entering a Case Sensitive username, or leave empty to get all results.<br>Note: this is the username of the Uploader and not the Groupname that often show up at the end of 1337x titles, eg -GalaxyRG." },
    { name = "info_flaresolverr", text_value = "This site may use Cloudflare DDoS Protection, therefore Prowlarr requires <a href=\"https://wiki.servarr.com/prowlarr/faq#can-i-use-flaresolverr-indexers\" target=\"_blank\" rel=\"noreferrer\">FlareSolverr</a> to access it." },
    { name = "downloadlink", number_value = 0 },
    { name = "downloadlink2", number_value = 1 },
    { name = "info_download", text_value = "As the iTorrents .torrent download link on this site is known to fail from time to time, we suggest using the magnet link as a fallback. The BTCache and Torrage services are not supported because they require additional user interaction (a captcha for BTCache and a download button on Torrage.)" },
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
