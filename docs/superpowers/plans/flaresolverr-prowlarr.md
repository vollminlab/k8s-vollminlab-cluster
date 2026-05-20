# FlareSolverr + Prowlarr Indexer Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy FlareSolverr to unblock Cloudflare-protected Prowlarr indexers (EZTV, 1337x) and import the already-created YTS indexer into tofu state.

**Architecture:** FlareSolverr runs as a stateless Deployment in `mediastack`, reachable cluster-internally on port 8191. Prowlarr is configured via Terraform to route EZTV and 1337x requests through it using a tag-based proxy. YTS (already in Prowlarr as ID 16) is brought into tofu state via an import block.

**Tech Stack:** Kubernetes raw manifests (Kustomize), OpenTofu / tofu-controller, devopsarr/prowlarr Terraform provider v3.2

---

### Task 1: Create new branch

**Files:** none

- [ ] **Check out main and create branch**

```bash
git checkout main && git pull
git checkout -b feat/flaresolverr-prowlarr
```

Expected: switched to new branch `feat/flaresolverr-prowlarr`

---

### Task 2: Create FlareSolverr Deployment

**Files:**
- Create: `clusters/vollminlab-cluster/mediastack/flaresolverr/app/deployment.yaml`

- [ ] **Create the directory**

```bash
mkdir -p clusters/vollminlab-cluster/mediastack/flaresolverr/app
```

- [ ] **Write deployment.yaml**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: flaresolverr
  namespace: mediastack
  labels:
    app: flaresolverr
    env: production
    category: media
spec:
  replicas: 1
  selector:
    matchLabels:
      app: flaresolverr
  template:
    metadata:
      labels:
        app: flaresolverr
        app.kubernetes.io/name: flaresolverr
        env: production
        category: media
    spec:
      containers:
        - name: flaresolverr
          image: ghcr.io/flaresolverr/flaresolverr:v3.4.6@sha256:524715c5b5d045ff77ae409ffa1d6c0fcf9f23a2e5e957eb44da4f2fc53e6876
          ports:
            - containerPort: 8191
              name: http
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 1000m
              memory: 1Gi
```

---

### Task 3: Create FlareSolverr Service

**Files:**
- Create: `clusters/vollminlab-cluster/mediastack/flaresolverr/app/service.yaml`

- [ ] **Write service.yaml**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: flaresolverr
  namespace: mediastack
  labels:
    app: flaresolverr
    env: production
    category: media
spec:
  type: ClusterIP
  selector:
    app: flaresolverr
  ports:
    - name: http
      port: 8191
      targetPort: 8191
```

---

### Task 4: Create FlareSolverr app kustomization

**Files:**
- Create: `clusters/vollminlab-cluster/mediastack/flaresolverr/app/kustomization.yaml`

- [ ] **Write kustomization.yaml**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: flaresolverr-app
resources:
  - deployment.yaml
  - service.yaml
```

- [ ] **Commit K8s manifests**

```bash
git add clusters/vollminlab-cluster/mediastack/flaresolverr/
git commit -m "feat(flaresolverr): add Deployment and Service to mediastack"
```

---

### Task 5: Wire FlareSolverr into the mediastack namespace kustomization

**Files:**
- Modify: `clusters/vollminlab-cluster/mediastack/kustomization.yaml`

- [ ] **Add flaresolverr/app to the resources list**

Insert `- ./flaresolverr/app` alphabetically (between `./filebrowser/app` and `./jellyfin/app`):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: mediastack
resources:
  - namespace.yaml
  - arr-media-dashboard-configmap.yaml
  - ./secrets
  - ./audiobookshelf/app
  - ./bazarr/app
  - ./cloudflared-audiobookshelf/app
  - ./cloudflared-jellyfin/app
  - ./bazarr-exportarr/app
  - ./filebrowser/app
  - ./flaresolverr/app
  - ./seerr/app
  - ./jellyfin/app
  - ./jellystat-db/app
  - ./jellystat/app
  - ./prowlarr/app
  - ./qbittorrent/app
  - ./readarr/app
  - ./radarr/app
  - ./sabnzbd/app
  - ./sonarr/app
  - ./pvcs
```

- [ ] **Commit**

```bash
git add clusters/vollminlab-cluster/mediastack/kustomization.yaml
git commit -m "feat(flaresolverr): wire into mediastack namespace kustomization"
```

---

### Task 6: Add Prowlarr FlareSolverr proxy Terraform resources

**Files:**
- Create: `terraform/prowlarr/proxy.tf`

- [ ] **Write proxy.tf**

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

- [ ] **Commit**

```bash
git add terraform/prowlarr/proxy.tf
git commit -m "feat(prowlarr): add FlareSolverr indexer proxy and tag"
```

---

### Task 7: Tag EZTV and 1337x indexers to use the proxy

**Files:**
- Modify: `terraform/prowlarr/indexers.tf`

- [ ] **Add tags to prowlarr_indexer.eztv**

Replace the `eztv` resource block so it reads:

```hcl
resource "prowlarr_indexer" "eztv" {
  name            = "EZTV"
  enable          = true
  priority        = 25
  protocol        = "torrent"
  implementation  = "Cardigann"
  config_contract = "CardigannSettings"
  app_profile_id  = 1
  tags            = [prowlarr_tag.flaresolverr.id]

  lifecycle { ignore_changes = [fields] }

  fields = [
    { name = "definitionFile", text_value = "eztv" },
  ]
}
```

- [ ] **Add tags to prowlarr_indexer.the1337x**

Replace the `the1337x` resource block so it reads:

```hcl
resource "prowlarr_indexer" "the1337x" {
  name            = "1337x"
  enable          = true
  priority        = 25
  protocol        = "torrent"
  implementation  = "Cardigann"
  config_contract = "CardigannSettings"
  app_profile_id  = 1
  tags            = [prowlarr_tag.flaresolverr.id]

  lifecycle { ignore_changes = [fields] }

  fields = [
    { name = "definitionFile", text_value = "1337x" },
  ]
}
```

- [ ] **Commit**

```bash
git add terraform/prowlarr/indexers.tf
git commit -m "feat(prowlarr): tag EZTV and 1337x to use FlareSolverr proxy"
```

---

### Task 8: Import YTS into tofu state

**Files:**
- Modify: `terraform/prowlarr/imports.tf`

- [ ] **Add YTS import block**

Append to `terraform/prowlarr/imports.tf`:

```hcl
import {
  to = prowlarr_indexer.yts
  id = "16"
}
```

Full file after the change:

```hcl
# Import blocks for existing Prowlarr resources
# IDs fetched 2026-05-15 via kubectl exec prowlarr /api/v1/{indexer,applications}

import {
  to = prowlarr_indexer.nzbgeek
  id = "1"
}

import {
  to = prowlarr_indexer.nzbplanet
  id = "2"
}

import {
  to = prowlarr_application_radarr.radarr
  id = "1"
}

import {
  to = prowlarr_application_sonarr.sonarr
  id = "2"
}

import {
  to = prowlarr_indexer.yts
  id = "16"
}
```

- [ ] **Commit**

```bash
git add terraform/prowlarr/imports.tf
git commit -m "feat(prowlarr): import YTS indexer (id=16) into tofu state"
```

---

### Task 9: Push and open PR

- [ ] **Push branch**

```bash
git push -u origin feat/flaresolverr-prowlarr
```

- [ ] **Open PR**

```bash
gh pr create \
  --title "feat(prowlarr): deploy FlareSolverr and fix Cardigann indexers" \
  --body "$(cat <<'EOF'
## Summary

- Deploys FlareSolverr (v3.4.6) to mediastack as a headless Chromium proxy for Cloudflare-protected indexers
- Configures Prowlarr to route EZTV and 1337x through FlareSolverr via a tag-based proxy (Terraform)
- Imports YTS (ID 16, already created in Prowlarr) into tofu state to resolve the provider sensitive-field bug
- Unblocks prowlarr-config tofu which has been in a failed apply loop since PR #684
EOF
)"
```

---

### Task 10: Verify reconciliation

After the PR merges and Flux reconciles (allow ~10 minutes):

- [ ] **Check FlareSolverr pod is Running**

```bash
kubectl get pod -n mediastack -l app=flaresolverr
```

Expected: `1/1 Running`

- [ ] **Check prowlarr-config tofu is Applied**

```bash
kubectl get tf prowlarr-config -n tofu
```

Expected: `True   Applied successfully: main@sha1:<new-sha>`

- [ ] **Verify indexers exist in Prowlarr**

```bash
PROWLARR_KEY=$(kubectl get secret prowlarr-tf-credentials -n tofu -o jsonpath='{.data.prowlarr_api_key}' | base64 -d)
kubectl exec -n mediastack deploy/prowlarr -c prowlarr -- \
  curl -s "http://localhost:9696/api/v1/indexer" -H "X-Api-Key: $PROWLARR_KEY" \
  | python3 -c "import json,sys; [print(i['id'], i['name']) for i in json.load(sys.stdin)]"
```

Expected: output includes lines for YTS, EZTV, and 1337x alongside the existing NZBgeek and NzbPlanet.
