# Prowlarr Terraform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring existing Prowlarr configuration (2 Newznab indexers + Radarr/Sonarr app sync connections) under tofu-controller management without losing or overwriting current state.

**Architecture:** Mirror the radarr/sonarr Terraform module pattern — a `terraform/prowlarr/` module with import blocks for all existing resources, wired into the cluster via a `Terraform` CR in `clusters/vollminlab-cluster/tofu/prowlarr-config/app/`. Credentials come from a single `prowlarr-tf-credentials` SealedSecret via `varsFrom`. Sensitive indexer fields (API keys) use `lifecycle { ignore_changes = [fields] }` to prevent perpetual drift from Prowlarr's masked `********` API responses.

**Tech Stack:** devopsarr/prowlarr Terraform provider v3.2.1, tofu-controller, SealedSecrets, 1Password CLI

---

## Current state (snapshot as of 2026-05-15)

| Resource | Type | ID |
|----------|------|----|
| NZBGeek | `prowlarr_indexer` (Newznab) | 1 |
| NzbPlanet | `prowlarr_indexer` (Newznab) | 2 |
| Radarr app sync | `prowlarr_application_radarr` | 1 |
| Sonarr app sync | `prowlarr_application_sonarr` | 2 |

## 1Password items for credentials

| Variable | 1Password item ID | Field |
|----------|-------------------|-------|
| `prowlarr_api_key` | `ob3pfz7obr53nmbvoub5smyyom` (Prowlarr) | `credential` |
| `nzbgeek_api_key` | `5nq2o3nqapt3deshar6fd3znxm` (NZBGeek) | check `password` field; if that's the web password, look in `notesPlain` for the API key |
| `nzbplanet_api_key` | `h67tj4sytpcoktfde547bq5ure` (NZBPlanet) | `credential` |
| `radarr_api_key` | Radarr 1Password item | same key already in `radarr-tf-credentials` |
| `sonarr_api_key` | Sonarr 1Password item | same key already in `sonarr-tf-credentials` |

## File map

| Action | Path |
|--------|------|
| Create | `terraform/prowlarr/versions.tf` |
| Create | `terraform/prowlarr/providers.tf` |
| Create | `terraform/prowlarr/variables.tf` |
| Create | `terraform/prowlarr/indexers.tf` |
| Create | `terraform/prowlarr/applications.tf` |
| Create | `terraform/prowlarr/imports.tf` |
| Create | `clusters/vollminlab-cluster/tofu/prowlarr-config/app/kustomization.yaml` |
| Create | `clusters/vollminlab-cluster/tofu/prowlarr-config/app/terraform-cr.yaml` |
| Create | `clusters/vollminlab-cluster/tofu/prowlarr-config/app/prowlarr-tf-credentials-sealedsecret.yaml` |
| Modify | `clusters/vollminlab-cluster/tofu/kustomization.yaml` (add `prowlarr-config/app`) |

No new Flux Kustomization CR or HelmRepository needed — the existing `tofu` Flux Kustomization already picks up everything in `clusters/vollminlab-cluster/tofu/`.

---

### Task 1: Scaffold the Terraform module

**Files:**
- Create: `terraform/prowlarr/versions.tf`
- Create: `terraform/prowlarr/providers.tf`
- Create: `terraform/prowlarr/variables.tf`

- [ ] **Step 1: Create `versions.tf`**

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    prowlarr = {
      source  = "devopsarr/prowlarr"
      version = "~> 3.2"
    }
  }
}
```

- [ ] **Step 2: Create `providers.tf`**

```hcl
provider "prowlarr" {
  url     = "http://prowlarr.mediastack.svc.cluster.local:9696"
  api_key = var.prowlarr_api_key
}
```

- [ ] **Step 3: Create `variables.tf`**

```hcl
variable "prowlarr_api_key" {
  description = "Prowlarr API key for provider authentication"
  type        = string
  sensitive   = true
}

variable "nzbgeek_api_key" {
  description = "NZBGeek Newznab API key"
  type        = string
  sensitive   = true
}

variable "nzbplanet_api_key" {
  description = "NzbPlanet Newznab API key"
  type        = string
  sensitive   = true
}

variable "radarr_api_key" {
  description = "Radarr API key for application sync connection"
  type        = string
  sensitive   = true
}

variable "sonarr_api_key" {
  description = "Sonarr API key for application sync connection"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 4: Commit**

```bash
git add terraform/prowlarr/
git commit -m "feat(prowlarr-tf): scaffold terraform module with provider and variables"
```

---

### Task 2: Add indexer resources

**Files:**
- Create: `terraform/prowlarr/indexers.tf`

Both indexers are Newznab protocol. `lifecycle { ignore_changes = [fields] }` prevents perpetual drift because Prowlarr returns `********` for sensitive field values on API reads — without this, Terraform would detect a diff every plan and re-apply the API keys unnecessarily.

- [ ] **Step 1: Create `indexers.tf`**

```hcl
# Indexers imported from Prowlarr API
# Retrieved 2026-05-15 via kubectl exec prowlarr /api/v1/indexer
# Both are Newznab usenet indexers; sensitive fields use ignore_changes
# to prevent perpetual drift from Prowlarr's masked API responses.

resource "prowlarr_indexer" "nzbgeek" {
  name           = "NZBgeek"
  enable         = true
  priority       = 25
  protocol       = "usenet"
  implementation = "Newznab"
  config_contract = "NewznabSettings"
  app_profile_id = 1

  lifecycle { ignore_changes = [fields] }

  fields = [
    { name = "baseUrl",  text_value      = "https://api.nzbgeek.info" },
    { name = "apiPath",  text_value      = "/api" },
    { name = "apiKey",   sensitive_value = var.nzbgeek_api_key },
  ]
}

resource "prowlarr_indexer" "nzbplanet" {
  name           = "NzbPlanet"
  enable         = true
  priority       = 25
  protocol       = "usenet"
  implementation = "Newznab"
  config_contract = "NewznabSettings"
  app_profile_id = 1

  lifecycle { ignore_changes = [fields] }

  fields = [
    { name = "baseUrl",  text_value      = "https://api.nzbplanet.net" },
    { name = "apiPath",  text_value      = "/api" },
    { name = "apiKey",   sensitive_value = var.nzbplanet_api_key },
  ]
}
```

- [ ] **Step 2: Commit**

```bash
git add terraform/prowlarr/indexers.tf
git commit -m "feat(prowlarr-tf): add NZBGeek and NzbPlanet indexer resources"
```

---

### Task 3: Add application sync resources

**Files:**
- Create: `terraform/prowlarr/applications.tf`

- [ ] **Step 1: Create `applications.tf`**

```hcl
# Application sync connections imported from Prowlarr API
# Retrieved 2026-05-15 via kubectl exec prowlarr /api/v1/applications

resource "prowlarr_application_radarr" "radarr" {
  name         = "Radarr"
  sync_level   = "fullSync"
  prowlarr_url = "http://prowlarr.mediastack.svc.cluster.local:9696"
  base_url     = "http://radarr.mediastack.svc.cluster.local:7878"
  api_key      = var.radarr_api_key
  sync_categories = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080, 2090]
}

resource "prowlarr_application_sonarr" "sonarr" {
  name         = "Sonarr"
  sync_level   = "fullSync"
  prowlarr_url = "http://prowlarr.mediastack.svc.cluster.local:9696"
  base_url     = "http://sonarr.mediastack.svc.cluster.local:8989"
  api_key      = var.sonarr_api_key
  sync_categories       = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5090]
  anime_sync_categories = [5070]
}
```

**Note:** `syncAnimeStandardFormatSearch: true` was observed in the Prowlarr API but may not be a supported field in the Terraform resource. If the plan fails with an unknown attribute error, add `lifecycle { ignore_changes = all }` to `prowlarr_application_sonarr.sonarr` temporarily and re-run. Check the provider v3.2.1 docs for the correct field name if needed.

- [ ] **Step 2: Commit**

```bash
git add terraform/prowlarr/applications.tf
git commit -m "feat(prowlarr-tf): add Radarr and Sonarr application sync resources"
```

---

### Task 4: Add import blocks

**Files:**
- Create: `terraform/prowlarr/imports.tf`

- [ ] **Step 1: Create `imports.tf`**

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
```

- [ ] **Step 2: Commit**

```bash
git add terraform/prowlarr/imports.tf
git commit -m "feat(prowlarr-tf): add import blocks for existing prowlarr resources"
```

---

### Task 5: Create cluster Terraform CR and kustomization

**Files:**
- Create: `clusters/vollminlab-cluster/tofu/prowlarr-config/app/terraform-cr.yaml`
- Create: `clusters/vollminlab-cluster/tofu/prowlarr-config/app/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/tofu/kustomization.yaml`

- [ ] **Step 1: Create `terraform-cr.yaml`**

```yaml
---
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: prowlarr-config
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 10m
  approvePlan: auto
  path: ./terraform/prowlarr
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  backendConfig:
    customConfiguration: |
      backend "s3" {
        bucket                      = "terraform-state"
        key                         = "prowlarr/terraform.tfstate"
        region                      = "us-east-1"
        endpoint                    = "http://minio.minio.svc.cluster.local:9000"
        force_path_style            = true
        skip_credentials_validation = true
        skip_metadata_api_check     = true
        skip_region_validation      = true
      }
  backendConfigsFrom:
    - kind: Secret
      name: tofu-minio-credentials
  varsFrom:
    - kind: Secret
      name: prowlarr-tf-credentials
```

- [ ] **Step 2: Create `kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: prowlarr-config
  labels:
    app: tofu-controller
    env: production
    category: core
resources:
  - terraform-cr.yaml
  - prowlarr-tf-credentials-sealedsecret.yaml
```

- [ ] **Step 3: Add `prowlarr-config/app` to `clusters/vollminlab-cluster/tofu/kustomization.yaml`**

Add `- prowlarr-config/app` to the `resources` list in alphabetical order (between `minio-config/app` and `radarr-config/app`):

```yaml
resources:
  - namespace.yaml
  - authentik-config/app
  - b2-config/app
  - cloudflare-config/app
  - grafana-config/app
  - harbor-config/app
  - minio-config/app
  - prowlarr-config/app   # ← add this line
  - radarr-config/app
  - sonarr-config/app
  - tofu-controller/app
```

- [ ] **Step 4: Commit**

```bash
git add clusters/vollminlab-cluster/tofu/
git commit -m "feat(prowlarr-tf): add Terraform CR and kustomization for prowlarr-config"
```

---

### Task 6: Seal credentials and open PR

**Files:**
- Create: `clusters/vollminlab-cluster/tofu/prowlarr-config/app/prowlarr-tf-credentials-sealedsecret.yaml`

The sealed secret must contain all 5 variables. Look up each API key from 1Password, then seal in one pipeline. Never write the plain secret to disk.

- [ ] **Step 1: Fetch API keys from 1Password and seal**

```bash
# Authenticate if needed
eval $(op signin)

PROWLARR_KEY=$(op item get ob3pfz7obr53nmbvoub5smyyom --fields credential)
NZBGEEK_KEY=$(op item get 5nq2o3nqapt3deshar6fd3znxm --fields password)
# If NZBGEEK_KEY looks like a web password rather than an API key,
# try: op item get 5nq2o3nqapt3deshar6fd3znxm --fields notesPlain
NZBPLANET_KEY=$(op item get h67tj4sytpcoktfde547bq5ure --fields credential)
RADARR_KEY=$(op item get <radarr-1password-item-id> --fields credential)
SONARR_KEY=$(op item get <sonarr-1password-item-id> --fields credential)

kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic prowlarr-tf-credentials -n tofu \
  --from-literal=prowlarr_api_key="$PROWLARR_KEY" \
  --from-literal=nzbgeek_api_key="$NZBGEEK_KEY" \
  --from-literal=nzbplanet_api_key="$NZBPLANET_KEY" \
  --from-literal=radarr_api_key="$RADARR_KEY" \
  --from-literal=sonarr_api_key="$SONARR_KEY" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/prowlarr-config/app/prowlarr-tf-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 2: Verify sealed secret metadata**

```bash
head -10 clusters/vollminlab-cluster/tofu/prowlarr-config/app/prowlarr-tf-credentials-sealedsecret.yaml
# Must show: name: prowlarr-tf-credentials, namespace: tofu
```

- [ ] **Step 3: Commit and push**

```bash
git add clusters/vollminlab-cluster/tofu/prowlarr-config/app/prowlarr-tf-credentials-sealedsecret.yaml
git commit -m "feat(prowlarr-tf): seal prowlarr credentials from 1Password"
git push -u origin <branch-name>
```

- [ ] **Step 4: Open PR and merge**

Open PR against `main`. After merge, Flux picks up the new `prowlarr-config/app` directory and the tofu-controller starts the Terraform run.

---

### Task 7: Verify

- [ ] **Step 1: Watch the terraform run**

```bash
kubectl get terraform prowlarr-config -n tofu -w
# Expected progression: Initializing → Terraform Planning → Plan generated → Applying → Applied successfully
# Ready should go True within 2-3 reconcile cycles (~10 min)
```

- [ ] **Step 2: Confirm all terraforms green**

```bash
kubectl get terraforms -A
# prowlarr-config should show: True   Applied successfully
```

- [ ] **Step 3: Verify no config drift in Prowlarr**

Check that the two indexers and two app connections still show the same settings they had before:

```bash
PROWLARR_POD=$(kubectl get pods -n mediastack -l app=prowlarr -o jsonpath='{.items[0].metadata.name}')
PROWLARR_KEY=$(kubectl exec -n mediastack $PROWLARR_POD -c prowlarr -- cat /config/config.xml | grep -oP '(?<=<ApiKey>)[^<]+')
kubectl exec -n mediastack $PROWLARR_POD -c prowlarr -- curl -s "http://localhost:9696/api/v1/indexer" -H "X-Api-Key: $PROWLARR_KEY" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for item in data:
    print(f'id={item[\"id\"]} name={item[\"name\"]!r} enable={item.get(\"enable\")}')
"
# Expected: id=1 name='NZBgeek' enable=True, id=2 name='NzbPlanet' enable=True
```
