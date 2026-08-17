---
description: Create all Shlink short links via the REST API after initial deployment
user-invocable: true
---

# Post-Deploy Shlink Short Links

Create all `go.vollminlab.com/<slug>` short links via the Shlink REST API. Run this after Shlink is deployed and healthy.

## Prerequisites

1. Shlink backend is running and reachable at `https://go.vollminlab.com`
2. You have the initial API key (from the `shlink-credentials` Secret, key `initial-api-key`, materialized by ESO)
3. Retrieve the key: `op read "op://Homelab/shlink-credentials/initial-api-key"` (or `kubectl get secret shlink-credentials -n shlink -o jsonpath='{.data.initial-api-key}' | base64 -d`)

## Steps

Set the API key variable, then create all short links:

```bash
SHLINK_API_KEY="<your-api-key>"
SHLINK_BASE="https://go.vollminlab.com"

# Function to create one short link
create_link() {
  local slug="$1"
  local url="$2"
  echo "Creating go/$slug → $url"
  curl -s -X POST "$SHLINK_BASE/rest/v3/short-urls" \
    -H "X-Api-Key: $SHLINK_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"longUrl\": \"$url\", \"customSlug\": \"$slug\"}" | jq -r '.shortUrl // .detail'
}

# Kubernetes cluster apps
create_link homepage     https://homepage.vollminlab.com
create_link capacitor    https://capacitor.vollminlab.com
create_link longhorn     https://longhorn.vollminlab.com
create_link policyreporter https://policyreporter.vollminlab.com
create_link radarr       https://radarr.vollminlab.com
create_link sonarr       https://sonarr.vollminlab.com
create_link sabnzbd      https://sabnzbd.vollminlab.com
create_link prowlarr     https://prowlarr.vollminlab.com
create_link bazarr       https://bazarr.vollminlab.com
create_link overseerr    https://overseerr.vollminlab.com
create_link tautulli     https://tautulli.vollminlab.com
create_link portainer    https://portainer.vollminlab.com
create_link shlink       https://shlink.vollminlab.com

# Infrastructure services
create_link pihole       https://pihole.vollminlab.com
create_link npm          https://npm.vollminlab.com
create_link plex         https://plex.vollminlab.com
create_link truenas      https://truenas.vollminlab.com
create_link udm          https://udm.vollminlab.com
create_link vcenter      https://vcenter.vollminlab.com
create_link haproxy      https://haproxy.vollminlab.com

# DMZ / Gaming
create_link bluemap      https://bluemap.vollminlab.com
```

## Verify

```bash
curl -s "$SHLINK_BASE/rest/v3/short-urls?itemsPerPage=50" \
  -H "X-Api-Key: $SHLINK_API_KEY" | jq '.data[] | {slug: .shortCode, url: .longUrl}'
```

## Notes

- If a slug already exists, Shlink returns 409 — safe to re-run, duplicates are skipped
- The canonical list of slugs is in `docs/cluster-reference.md` under "Short links inventory"
- To add a new slug, add it to the inventory table in cluster-reference.md and run the API call above
