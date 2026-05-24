resource "tailscale_dns_split_nameservers" "vollminlab" {
  domain      = "vollminlab.com"
  nameservers = ["192.168.100.2"]
}
