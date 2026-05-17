# ---------------------------------------------------------------------------
# Import blocks for pre-existing Cloudflare resources
#
# Tunnel resources are TF-created from scratch — no tunnel imports needed.
# DNS records that existed before TF management are imported here once.
# After the first successful `tofu apply`, these blocks are inert but harmless.
#
# Format: "<zone_id>/<record_id>"
# Zone ID: 30033aeb9194c2b67af71e7d0869da02
# ---------------------------------------------------------------------------

# DDNS anchor and DDNS-relative CNAMEs (pre-existing records, imported once)

import {
  to = cloudflare_dns_record.dynamic
  id = "30033aeb9194c2b67af71e7d0869da02/9cb712524cbd23acaf91b48ed27b57bb"
}

import {
  to = cloudflare_dns_record.apex
  id = "30033aeb9194c2b67af71e7d0869da02/30efec053133ce79bdf5a37328b7ee9d"
}

import {
  to = cloudflare_dns_record.bluemap
  id = "30033aeb9194c2b67af71e7d0869da02/909e558da87da5410dff73bff2e0979e"
}

import {
  to = cloudflare_dns_record.mastersleague
  id = "30033aeb9194c2b67af71e7d0869da02/33b3fd2a51bf12a6375770e9e493dd45"
}

import {
  to = cloudflare_dns_record.minecraft
  id = "30033aeb9194c2b67af71e7d0869da02/7cbe0a9f3ada42cca2c189c748a1d01f"
}

import {
  to = cloudflare_dns_record.vpn
  id = "30033aeb9194c2b67af71e7d0869da02/124db74a65a26978edc60aeee07c7232"
}
