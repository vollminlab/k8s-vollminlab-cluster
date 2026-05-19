resource "authentik_group" "audiobookshelf_admins" {
  name  = "Audiobookshelf Admins"
  users = [authentik_user.vollmin.id]
}

resource "authentik_group" "audiobookshelf_users" {
  name  = "Audiobookshelf Users"
  users = toset([authentik_user.jvollmin.id, authentik_user.gkroner.id, authentik_user.chavelock.id, authentik_user.jkvedaras.id])
}

resource "authentik_group" "filebrowser_users" {
  name  = "FileBrowser Users"
  users = toset([authentik_user.vollmin.id, authentik_user.jvollmin.id, authentik_user.gkroner.id, authentik_user.jkvedaras.id])
}

resource "authentik_group" "grafana_admins" {
  name  = "Grafana Admins"
  users = [authentik_user.vollmin.id]
}

resource "authentik_group" "harbor_admins" {
  name  = "Harbor Admins"
  users = [authentik_user.vollmin.id]
}

resource "authentik_group" "headlamp_admins" {
  name  = "Headlamp Admins"
  users = [authentik_user.vollmin.id]
}

resource "authentik_group" "jellyfin_admins" {
  name  = "Jellyfin Admins"
  users = [authentik_user.vollmin.id]
}

resource "authentik_group" "jellyfin_users" {
  name  = "Jellyfin Users"
  users = toset([authentik_user.jvollmin.id, authentik_user.vollmin.id, authentik_user.gkroner.id, authentik_user.chavelock.id, authentik_user.jkvedaras.id])
}

resource "authentik_group" "minio_admins" {
  name  = "MinIO Admins"
  users = [authentik_user.vollmin.id]
}

resource "authentik_group" "portainer_admins" {
  name  = "Portainer Admins"
  users = [authentik_user.vollmin.id]
}
