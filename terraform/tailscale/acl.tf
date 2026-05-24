resource "tailscale_acl" "main" {
  overwrite_existing_content = true
  acl = jsonencode({
    grants = [
      {
        src = ["*"]
        dst = ["*"]
        ip  = ["*"]
      }
    ]

    ssh = [
      {
        action = "check"
        src    = ["autogroup:member"]
        dst    = ["autogroup:self"]
        users  = ["autogroup:nonroot", "root"]
      }
    ]

    tagOwners = {
      "tag:k8s" = ["autogroup:admin"]
    }

    autoApprovers = {
      routes = {
        "192.168.152.0/24" = ["tag:k8s"]
        "192.168.151.0/24" = ["tag:k8s"]
        "192.168.100.0/24" = ["tag:k8s"]
      }
    }
  })
}
