# FlareSolverr + Prowlarr Indexer Fix

## Problem

`prowlarr-config` tofu has been stuck in a failed apply loop since PR #684 merged three Cardigann indexers (EZTV, 1337x, YTS):

- **EZTV & 1337x**: Prowlarr validates indexers on creation by making a live HTTP request to the site. Both sites use Cloudflare bot protection, which blocks plain HTTP clients and requires a real browser to solve the challenge. Result: 400 Bad Request from the Prowlarr API.
- **YTS**: The devopsarr/prowlarr Terraform provider has a bug where it reports "inconsistent values for sensitive attribute" after creating a Cardigann indexer — even though the resource was created successfully. YTS (ID 16) exists in Prowlarr but is not in tofu state.

## Solution

1. Deploy FlareSolverr to the `mediastack` namespace — a headless Chromium proxy that solves Cloudflare challenges on behalf of Prowlarr.
2. Configure Prowlarr to route EZTV and 1337x requests through FlareSolverr via a tag-based proxy assignment in Terraform.
3. Import YTS (ID 16) into tofu state via an import block, bypassing the provider bug.

## Architecture

### FlareSolverr (Kubernetes)

- **Namespace**: `mediastack`
- **Kind**: raw `Deployment` + `Service` (no Helm chart exists)
- **Image**: `ghcr.io/flaresolverr/flaresolverr:v3.4.6` (digest-pinned)
- **Port**: 8191
- **Replicas**: 1
- **Storage**: none (stateless)
- **Ingress**: none — internal only, accessed via cluster DNS
- **Authentik**: none — no external exposure
- **Shlink slug**: none
- **Labels**: `app: flaresolverr`, `env: production`, `category: media`
- **Resources**:
  - requests: 100m CPU, 256Mi memory
  - limits: 1000m CPU, 1Gi memory (Chromium requires headroom)

Prowlarr reaches FlareSolverr at:
```
http://flaresolverr.mediastack.svc.cluster.local:8191
```

### Prowlarr Terraform (new `proxy.tf`)

```hcl
resource "prowlarr_tag" "flaresolverr" {
  label = "flaresolverr"
}

resource "prowlarr_indexer_proxy_flaresolverr" "main" {
  name            = "FlareSolverr"
  host            = "http://flaresolverr.mediastack.svc.cluster.local:8191"
  request_timeout = 60
  tags            = [prowlarr_tag.flaresolverr.id]
}
```

### Prowlarr Terraform (updates to `indexers.tf`)

Add `tags = [prowlarr_tag.flaresolverr.id]` to `prowlarr_indexer.eztv` and `prowlarr_indexer.the1337x`. This causes Prowlarr to route all requests for those indexers through the FlareSolverr proxy.

YTS does not need the tag — it has no Cloudflare protection.

### YTS Import (`imports.tf`)

```hcl
import {
  to = prowlarr_indexer.yts
  id = "16"
}
```

This brings the already-existing YTS indexer into tofu state without re-creating it, bypassing the provider bug entirely.

## Files

### Create

| File | Purpose |
|---|---|
| `clusters/vollminlab-cluster/mediastack/flaresolverr/app/deployment.yaml` | FlareSolverr Deployment |
| `clusters/vollminlab-cluster/mediastack/flaresolverr/app/service.yaml` | ClusterIP Service on port 8191 |
| `clusters/vollminlab-cluster/mediastack/flaresolverr/app/kustomization.yaml` | Lists deployment + service |
| `terraform/prowlarr/proxy.tf` | Tag + FlareSolverr proxy resources |

### Modify

| File | Change |
|---|---|
| `clusters/vollminlab-cluster/mediastack/kustomization.yaml` | Add `flaresolverr/app` resource |
| `terraform/prowlarr/indexers.tf` | Add `tags` to eztv and the1337x |
| `terraform/prowlarr/imports.tf` | Add YTS import block (id = "16") |

## Order of Operations

Flux applies K8s manifests and tofu Terraform in the same reconciliation pass. FlareSolverr will be scheduled before tofu runs its apply, but there is a small race window if the pod takes time to become Ready. The tofu-controller retries on failure, so a first-attempt race is acceptable — it will succeed once FlareSolverr is healthy.

## Maintenance Notes

- FlareSolverr must be kept reasonably current with Cloudflare's bot detection updates. Monitor for apply failures on EZTV/1337x as a signal that FlareSolverr needs a version bump.
- If EZTV or 1337x changes their domain, the Cardigann definition in Prowlarr updates automatically via Prowlarr's definition sync — no Terraform change needed.
