resource "prowlarr_tag" "flaresolverr" {
  label = "flaresolverr"
}

resource "prowlarr_indexer_proxy_flaresolverr" "main" {
  name            = "FlareSolverr"
  host            = "http://flaresolverr.mediastack.svc.cluster.local:8191"
  request_timeout = 60
  tags            = [prowlarr_tag.flaresolverr.id]
}
