# Jellyfin Runbook

## Access architecture

`jellyfin.vollminlab.com` resolves two ways (split-horizon DNS):

- **Internally (Pi-hole):** → `192.168.152.244` (nginx ingress VIP). LAN clients stream
  directly over the home network — full speed, never touches the internet.
- **Externally (public DNS):** → proxied Cloudflare CNAME → the `vollminlab-Jellyfin`
  Zero Trust tunnel (`cloudflared-jellyfin` deployment in `mediastack`) →
  `jellyfin.mediastack.svc.cluster.local:8096`.

Jellyfin's own `EnableRemoteAccess` is irrelevant to the tunnel path: requests arrive
from the in-cluster cloudflared pod IP, which Jellyfin treats as local.

## Symptom: media never starts playing ("stops at 0 ms")

Client can browse, select a title, and press play, but playback never begins. Jellyfin
logs show repeated `MediaInfoHelper: User policy for <user>` and
`Playback stopped ... Stopped at 0 ms`, but **no `/Videos/.../stream` request** ever
reaches the server.

### Root cause (seen 2026-06-01)

Cloudflare issues a **managed bot challenge** ("Just a moment…", header
`cf-mitigated: challenge`, HTTP 403) on requests to `jellyfin.vollminlab.com`. Native
players (Apple TV / tvOS, mobile apps) cannot solve a JavaScript challenge, so the
stream request returns the HTML challenge page instead of video → playback fails.
Small requests (metadata, artwork) often slip through, so browsing still works — which
masks the problem.

This is the same class of failure that affected `authentik.vollminlab.com` (PR #648).

### Diagnosis

```bash
JF=$(kubectl get pod -n mediastack -l app.kubernetes.io/name=jellyfin -o name | head -1 | cut -d/ -f2)

# 1. Is Jellyfin itself healthy? Stream in-cluster (expect HTTP 206, high MB/s).
#    Authenticate with the "Jellyfin" 1Password item, then range-GET /Videos/<id>/stream
#    against http://jellyfin.mediastack.svc.cluster.local:8096 from a pod in mediastack.

# 2. Did stream requests reach the tunnel? (empty output = blocked at the edge)
kubectl logs -n mediastack deploy/cloudflared-jellyfin --since=1h | grep '/Videos/'

# 3. Reproduce the challenge from outside:
#    curl -s -D - https://jellyfin.vollminlab.com/System/Info/Public | grep -i cf-mitigated
#    cf-mitigated: challenge  →  Cloudflare is challenging clients.
```

If in-cluster streaming works but the tunnel path is challenged, the fix is on the
Cloudflare side, not Jellyfin.

### Fix

- **External clients:** add a skip rule for `jellyfin.vollminlab.com` to the
  `http_request_firewall_custom` ruleset in `terraform/cloudflare/security.tf`
  (only one ruleset is allowed per zone — add a rule, don't create a new ruleset).
  tofu-controller (`cloudflare-config` Terraform CR) auto-applies on merge.
- **Home Apple TV / LAN clients:** point the device's DNS at Pi-hole so
  `jellyfin.vollminlab.com` resolves to `192.168.152.244` and streams over the LAN,
  bypassing Cloudflare entirely (Apple TV → Settings → Network → Wi-Fi → Configure DNS
  → Manual → Pi-hole IP). This is the preferred path for large remux files.

### Note on large remuxes over Cloudflare

The library contains ~20 GB 1080p remuxes. Even with the challenge skipped, Cloudflare's
free plan discourages sustained large-file/video proxying (TOS 2.8) and has previously
terminated direct-play streams (`stream canceled by remote with error code 0`). For
reliable remote streaming, prefer transcoded playback or a non-Cloudflare transport
(e.g. Tailscale). Local playback over the LAN ingress is unaffected.
