resource "harbor_registry" "dockerhub" {
  provider_name = "docker-hub"
  name          = "dockerhub"
  endpoint_url  = "https://hub.docker.com"

  # Authenticated pull-through (read-only Docker Hub PAT) — raises the upstream
  # rate limit above the shared-IP anonymous ceiling that caused the recurring
  # 429 ImagePullBackOff storms (2026-05-30 node maintenance).
  access_id     = var.harbor_dockerhub_user
  access_secret = var.harbor_dockerhub_token
}
