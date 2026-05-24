data "tailscale_device" "connector" {
  hostname = "vollminlab-cluster.tail8b1511.ts.net"
}

resource "tailscale_device_subnet_routes" "connector" {
  device_id = data.tailscale_device.connector.id
  routes = [
    "192.168.152.0/24",
    "192.168.151.0/24",
    "192.168.100.0/24",
  ]
}
