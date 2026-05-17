resource "authentik_user" "vollmin" {
  username  = "vollmin"
  name      = "Scott Vollmin"
  email     = "scottvollmin@gmail.com"
  is_active = true

  lifecycle {
    # password: users set their own via Authentik's reset flow, Terraform never touches it
    # groups: managed from the authentik_group side; ignore here to avoid conflict
    ignore_changes = [password, groups]
  }
}

resource "authentik_user" "jvollmin" {
  username  = "jvollmin"
  name      = "Justin Vollmin"
  email     = "vollmi91@gmail.com"
  is_active = true

  lifecycle {
    ignore_changes = [password, groups]
  }
}

resource "authentik_user" "gkroner" {
  username  = "gkroner"
  name      = "Garrett Kroner"
  email     = "gkroner@gmail.com"
  is_active = true

  lifecycle {
    ignore_changes = [password, groups]
  }
}

resource "authentik_user" "jkvedaras" {
  username  = "jkvedaras"
  name      = "jkvedaras"
  email     = "jokvedaras@gmail.com"
  is_active = true

  lifecycle {
    ignore_changes = [password, groups]
  }
}

resource "authentik_user" "chavelock" {
  username  = "chavelock"
  name      = "chavelock"
  email     = "havelock17@gmail.com"
  is_active = true

  lifecycle {
    ignore_changes = [password, groups]
  }
}
