terraform {
  required_version = ">= 1.6.0"

  required_providers {
    readarr = {
      source  = "devopsarr/readarr"
      version = "~> 2.1"
    }
  }
}
