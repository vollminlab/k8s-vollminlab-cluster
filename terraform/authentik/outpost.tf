resource "authentik_outpost" "vollminlab_proxy" {
  name = "vollminlab-proxy"
  type = "proxy"

  protocol_providers = [
    authentik_provider_proxy.vollminlab_forward_auth.id,
    authentik_provider_proxy.filebrowser.id,
    authentik_provider_proxy.foundry.id,
  ]

  config = jsonencode({
    authentik_host                   = "https://authentik.vollminlab.com/"
    authentik_host_insecure          = false
    authentik_host_browser           = ""
    log_level                        = "info"
    object_naming_template           = "ak-outpost-%(name)s"
    refresh_interval                 = "minutes=5"
    container_image                  = null
    docker_network                   = null
    docker_map_ports                 = true
    docker_labels                    = null
    kubernetes_replicas              = 1
    kubernetes_namespace             = "authentik"
    kubernetes_ingress_annotations   = {}
    kubernetes_ingress_secret_name   = "authentik-outpost-tls"
    kubernetes_ingress_class_name    = null
    kubernetes_ingress_path_type     = null
    kubernetes_httproute_annotations = {}
    kubernetes_httproute_parent_refs = []
    kubernetes_service_type          = "ClusterIP"
    kubernetes_disabled_components   = []
    kubernetes_image_pull_secrets    = []
    kubernetes_json_patches          = null
  })
}
