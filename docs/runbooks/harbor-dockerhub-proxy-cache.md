# Harbor Docker Hub Pull-Through Cache Runbook

## What this is

All `docker.io` image pulls in the cluster are routed through a Harbor
**proxy-cache project** (`dockerhub-proxy`). Images are fetched *through* Harbor
on first pull and cached; subsequent pulls are served from Harbor in-cluster.
This eliminates the Docker Hub anonymous rate-limit (`429` →
`ImagePullBackOff`) storms that hit on mass reschedules — the whole cluster
shares one egress IP against Docker Hub's ~100 anonymous pulls / 6h limit.

The redirect is **transparent**: image references stay `docker.io/...`.
containerd on every node resolves them via Harbor through a registry mirror.
Renovate continues to track upstream versions normally.

## Architecture

Two halves, in two repos:

1. **Harbor side** (this repo, `terraform/harbor/`):
   - `harbor_registry "dockerhub"` — upstream endpoint `https://hub.docker.com`,
     authenticated with a **read-only Docker Hub PAT** (raises the upstream
     limit above the anonymous ceiling).
   - `harbor_project "dockerhub_proxy"` — `public = true`,
     `registry_id = harbor_registry.dockerhub.registry_id` (the `registry_id`
     argument is what makes a project a proxy cache).
   - Applied via the `harbor-config` tofu-controller workspace. Credentials live
     in the `harbor-tf-credentials` SealedSecret (`tofu` namespace):
     `harbor_dockerhub_user` + `harbor_dockerhub_token`.
   - PAT stored in 1Password: **`vollminlab-harbor-proxy-cache`** (Homelab vault,
     read-only, username `vollmin`).

2. **Node side** (`ansible-playbooks` repo,
   `playbooks/containerd-dockerhub-mirror.yml`):
   - containerd `config_path = "/etc/containerd/certs.d"` (CRI registry block).
   - `/etc/containerd/certs.d/docker.io/hosts.toml`:
     ```toml
     server = "https://registry-1.docker.io"

     [host."https://harbor.vollminlab.com/v2/dockerhub-proxy"]
       capabilities = ["pull", "resolve"]
       override_path = true
     ```
   - `server` is the **fallback** — if Harbor is unavailable, containerd pulls
     directly from Docker Hub. No single point of failure.
   - Harbor's TLS is a publicly-trusted Let's Encrypt wildcard, so no CA
     injection or `skip_verify` is needed on the nodes.

## How a pull flows

1. A pod references `docker.io/foo/bar:tag` (or `redis:7-alpine`, etc.).
2. containerd matches `docker.io` in `certs.d` → requests the manifest from
   `https://harbor.vollminlab.com/v2/dockerhub-proxy/...`.
3. Harbor checks its cache; on a miss it pulls from Docker Hub (authenticated),
   caches the layers, and serves them.
4. Library images map to `dockerhub-proxy/library/<name>`; org images to
   `dockerhub-proxy/<org>/<name>`.

## Verifying it works

```bash
# Harbor admin password (unsealed in-cluster — no 1Password needed)
ADMIN_PW=$(kubectl get secret harbor-tf-credentials -n tofu \
  -o jsonpath='{.data.harbor_admin_password}' | base64 -d)

# 1. Registry exists and is healthy (healthy => the PAT authenticated OK)
curl -sk -u "admin:${ADMIN_PW}" https://harbor.vollminlab.com/api/v2.0/registries

# 2. Proxy project is linked to the registry (registry_id != 0)
curl -sk -u "admin:${ADMIN_PW}" \
  https://harbor.vollminlab.com/api/v2.0/projects/dockerhub-proxy

# 3. Repos cached so far
curl -sk -u "admin:${ADMIN_PW}" \
  "https://harbor.vollminlab.com/api/v2.0/projects/dockerhub-proxy/repositories?page_size=50"

# 4. End-to-end: a fresh uncached pull on a node, via a throwaway pod
kubectl run proxy-test -n monitoring --restart=Never \
  --image=docker.io/library/busybox:1.36 \
  --overrides='{"metadata":{"labels":{"app":"proxy-test","env":"production","category":"observability"}},"spec":{"containers":[{"name":"proxy-test","image":"docker.io/library/busybox:1.36","command":["true"],"resources":{"requests":{"cpu":"10m","memory":"16Mi"},"limits":{"cpu":"50m","memory":"32Mi"}}}]}}'
kubectl describe pod proxy-test -n monitoring | grep -iE "Pulling|Pulled|Failed|BackOff"
kubectl delete pod proxy-test -n monitoring
```

**Note:** Harbor's repository list is eventually consistent — a freshly pulled
repo can take several seconds to appear in the `/repositories` API. Pause and
re-query before concluding a pull bypassed the proxy.

## Adding the proxy to a new node

New or rebuilt nodes must run the mirror playbook **before** taking production
workloads, or they fall back to direct (rate-limited) Docker Hub pulls:

```bash
# From ansible01, in ~/ansible-playbooks/
ansible-playbook playbooks/containerd-dockerhub-mirror.yml --ask-vault-pass
```

The playbook is idempotent and `serial: 1` with per-node `crictl pull` + node
Ready gates.

## Rotating the Docker Hub PAT

1. Generate a new **read-only** PAT in Docker Hub (Account settings → Personal
   access tokens). Save it to 1Password item `vollminlab-harbor-proxy-cache`.
2. Re-seal it into the workspace secret (preserving the other keys):
   ```bash
   kubectl create secret generic harbor-tf-credentials -n tofu \
     --from-literal=harbor_dockerhub_user='vollmin' \
     --from-literal=harbor_dockerhub_token='<new-pat>' \
     --dry-run=client -o yaml \
   | kubeseal --controller-namespace sealed-secrets \
       --controller-name sealed-secrets-controller --format yaml \
       --merge-into clusters/vollminlab-cluster/tofu/harbor-config/app/harbor-tf-credentials-sealedsecret.yaml
   ```
3. PR → merge. The `harbor-config` workspace re-applies the registry with the
   new credential. Cached images are unaffected.

## Troubleshooting

**Pulls still hitting Docker Hub / 429s persist**
- Confirm the node ran the playbook: `grep config_path /etc/containerd/config.toml`
  should show `/etc/containerd/certs.d` in the CRI registry block, and
  `/etc/containerd/certs.d/docker.io/hosts.toml` must exist.
- Restart containerd if config was changed out-of-band: `systemctl restart containerd`.

**`ImagePullBackOff` only when Harbor is down**
- Expected to degrade gracefully via the `registry-1.docker.io` fallback, but a
  Harbor outage during a mass reschedule reintroduces the anonymous limit. Check
  Harbor health first: `kubectl get pods -n harbor`.

**Registry shows `unhealthy` in Harbor**
- The Docker Hub PAT is invalid or expired. Rotate it (above).

**New image org not caching**
- The proxy caches any path automatically on first pull. If a specific repo
  404s through the proxy, verify it exists on Docker Hub at that exact path
  (library images need the `library/` prefix, which containerd adds
  automatically for bare names like `redis`).

## Related

- Design: `docs/superpowers/specs/harbor-dockerhub-proxy-cache-design.md`
- Plan: `docs/superpowers/plans/harbor-dockerhub-proxy-cache.md`
- Harbor LoadBalancer migration: roadmap 3.6
- Harbor robot account / tofu workspace notes: `docs/runbooks/` Harbor entries
