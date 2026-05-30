resource "harbor_project" "library" {
  name   = "library"
  public = true

  lifecycle {
    prevent_destroy = true
  }
}

resource "harbor_project" "vollminlab" {
  name   = "vollminlab"
  public = false

  lifecycle {
    prevent_destroy = true
  }
}

# Docker Hub pull-through cache project. The registry_id argument turns this into
# a proxy cache: docker.io images pulled as harbor.vollminlab.com/dockerhub-proxy/...
# are fetched through Harbor and cached. public=true so the containerd registry
# mirror on every node can pull without per-namespace credentials.
resource "harbor_project" "dockerhub_proxy" {
  name        = "dockerhub-proxy"
  public      = true
  registry_id = harbor_registry.dockerhub.registry_id
}
