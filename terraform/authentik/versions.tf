terraform {
  required_version = ">= 1.6.0"

  required_providers {
    authentik = {
      source  = "goauthentik/authentik"
      version = "~> 2026.5.0"
    }
    portainer = {
      source  = "portainer/portainer"
      version = "~> 1.29"
    }
  }
}
