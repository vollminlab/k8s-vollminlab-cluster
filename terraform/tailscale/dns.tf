resource "tailscale_dns_split_nameservers" "vollminlab" {
  domain      = "vollminlab.com"
  nameservers = ["192.168.100.4"]
}

resource "tailscale_dns_split_nameservers" "vollm_in" {
  domain      = "vollm.in"
  nameservers = ["192.168.100.4"]
}
