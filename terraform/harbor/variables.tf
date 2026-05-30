variable "harbor_admin_password" {
  description = "Harbor admin password for Terraform provider authentication"
  type        = string
  sensitive   = true
}

variable "harbor_oidc_client_secret" {
  description = "OAuth2 client secret for the Harbor application in Authentik"
  type        = string
  sensitive   = true
}

variable "harbor_gha_robot_secret" {
  description = "Password for the github-actions robot account (project-level, vollminlab)"
  type        = string
  sensitive   = true
}

variable "harbor_cluster_pull_secret" {
  description = "Password for the cluster-pull robot account (pull-only, vollminlab)"
  type        = string
  sensitive   = true
}

variable "harbor_dockerhub_user" {
  description = "Docker Hub username for the pull-through cache registry (authenticated proxy)"
  type        = string
  sensitive   = true
}

variable "harbor_dockerhub_token" {
  description = "Docker Hub read-only personal access token for the pull-through cache registry"
  type        = string
  sensitive   = true
}
