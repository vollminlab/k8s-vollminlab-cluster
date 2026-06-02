# Phase 5d IaC Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four tofu-controller Terraform modules (Cloudflare, Radarr, Sonarr, Backblaze B2) to bring the remaining manually-configured services under GitOps IaC, following the exact pattern established by `harbor-config`, `minio-config`, `grafana-config`, and `authentik-config`.

**Architecture:** Each module is two parts: a `terraform/<module>/` directory in this repo (the Terraform code) and a `clusters/vollminlab-cluster/tofu/<module>-config/app/` directory (Terraform CR + SealedSecret). The tofu-controller reads the Terraform CR, fetches the code via the `flux-system` GitRepository, and reconciles against the MinIO state backend. Adding a new module requires only adding one entry to `clusters/vollminlab-cluster/tofu/kustomization.yaml` — the `tofu-kustomization.yaml` Flux CR already watches this directory.

**Tech Stack:** OpenTofu (tofu-controller), Terraform HCL, SealedSecrets (kubeseal), Kustomize, Flux CD, providers: cloudflare/cloudflare v5, devopsarr/radarr, devopsarr/sonarr, Backblaze/b2

---

## File Map

### New files to create

```
terraform/b2/
  versions.tf
  providers.tf
  variables.tf
  main.tf
  imports.tf

clusters/vollminlab-cluster/tofu/b2-config/app/
  terraform-cr.yaml
  kustomization.yaml
  b2-tf-credentials-sealedsecret.yaml   (sealed output — never commit plaintext)

terraform/radarr/
  versions.tf
  providers.tf
  variables.tf
  download-clients.tf
  quality-profiles.tf
  imports.tf

clusters/vollminlab-cluster/tofu/radarr-config/app/
  terraform-cr.yaml
  kustomization.yaml
  radarr-tf-credentials-sealedsecret.yaml

terraform/sonarr/
  versions.tf
  providers.tf
  variables.tf
  download-clients.tf
  quality-profiles.tf
  imports.tf

clusters/vollminlab-cluster/tofu/sonarr-config/app/
  terraform-cr.yaml
  kustomization.yaml
  sonarr-tf-credentials-sealedsecret.yaml

terraform/cloudflare/
  versions.tf
  providers.tf
  variables.tf
  tunnels.tf
  dns.tf
  imports.tf

clusters/vollminlab-cluster/tofu/cloudflare-config/app/
  terraform-cr.yaml
  kustomization.yaml
  cloudflare-tf-credentials-sealedsecret.yaml
```

### Files to modify

```
clusters/vollminlab-cluster/tofu/kustomization.yaml   (add 4 module entries)
```

---

## Task 1: Pre-flight — 1Password, branch, provider version check

**Files:** none

- [ ] **Step 1: Sign into 1Password**

```bash
eval $(op signin)
```

Expected: no error. If `op: command not found`, install with `brew install 1password-cli` or per distro docs.

- [ ] **Step 2: Create branch from fresh main**

```bash
git checkout main && git pull
git checkout -b feat/phase5d-iac-modules
```

Expected: switched to new branch `feat/phase5d-iac-modules`, starting from latest main.

- [ ] **Step 3: Verify latest provider versions at registry.terraform.io**

```bash
# cloudflare v5 (must be v5 — v4 resource names are incompatible)
curl -s "https://registry.terraform.io/v1/providers/cloudflare/cloudflare/versions" | \
  jq -r '[.versions[].version | select(startswith("5."))] | last'

# devopsarr/radarr
curl -s "https://registry.terraform.io/v1/providers/devopsarr/radarr/versions" | \
  jq -r '.versions[-1].version'

# devopsarr/sonarr
curl -s "https://registry.terraform.io/v1/providers/devopsarr/sonarr/versions" | \
  jq -r '.versions[-1].version'

# Backblaze/b2
curl -s "https://registry.terraform.io/v1/providers/Backblaze/b2/versions" | \
  jq -r '.versions[-1].version'
```

Record the exact MAJOR.MINOR version for each (e.g., `5.6`, `2.1`, `3.4`, `0.8`). Use `~> X.Y` as the version constraint in `versions.tf` for each module. Do NOT write the terraform files until you have confirmed the actual latest versions.

---

## Task 2: B2 module — Terraform code

**Files:** Create `terraform/b2/versions.tf`, `providers.tf`, `variables.tf`, `main.tf`, `imports.tf`

This module imports the existing Velero B2 bucket and declares the scoped application key.

- [ ] **Step 1: Fetch existing B2 bucket ID for import**

```bash
# The bucket name is known: vollminlab-k8s-backups
# Fetch its B2 bucket ID via the B2 API (needed for the b2_application_key import)
B2_KEY_ID=$(op read "op://Homelab/Backblaze B2/application key id")
B2_KEY=$(op read "op://Homelab/Backblaze B2/application key")

curl -s -u "${B2_KEY_ID}:${B2_KEY}" \
  "https://api.backblazeb2.com/b2api/v3/b2_authorize_account" | jq '{accountId, apiUrl}'
```

Note the `accountId` from the output — needed for API calls. Then:

```bash
ACCOUNT_ID=<accountId from above>
AUTH_TOKEN=<authorizationToken from above>
API_URL=<apiUrl from above>

curl -s -H "Authorization: ${AUTH_TOKEN}" \
  "${API_URL}/b2api/v3/b2_list_buckets?accountId=${ACCOUNT_ID}" | \
  jq '.buckets[] | select(.bucketName == "vollminlab-k8s-backups") | {bucketId, bucketName}'
```

Record the `bucketId` — used in `imports.tf`.

Also fetch the scoped Velero key ID:

```bash
curl -s -H "Authorization: ${AUTH_TOKEN}" \
  "${API_URL}/b2api/v3/b2_list_keys?accountId=${ACCOUNT_ID}" | \
  jq '.keys[] | select(.keyName == "velero-k8s") | {applicationKeyId, keyName}'
```

Record the `applicationKeyId` — used in `imports.tf`.

- [ ] **Step 2: Create `terraform/b2/versions.tf`**

Replace `~> X.Y` with the version confirmed in Task 1 Step 3.

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    b2 = {
      source  = "Backblaze/b2"
      version = "~> X.Y"
    }
  }
}
```

- [ ] **Step 3: Create `terraform/b2/providers.tf`**

```hcl
provider "b2" {
  application_key_id = var.b2_master_application_key_id
  application_key    = var.b2_master_application_key
}
```

- [ ] **Step 4: Create `terraform/b2/variables.tf`**

```hcl
variable "b2_master_application_key_id" {
  description = "Backblaze B2 master application key ID for provider authentication"
  type        = string
  sensitive   = true
}

variable "b2_master_application_key" {
  description = "Backblaze B2 master application key for provider authentication"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 5: Create `terraform/b2/main.tf`**

```hcl
resource "b2_bucket" "velero" {
  bucket_name = "vollminlab-k8s-backups"
  bucket_type = "allPrivate"
}

resource "b2_application_key" "velero" {
  key_name     = "velero-k8s"
  capabilities = ["deleteFiles", "listBuckets", "listFiles", "readFiles", "writeFiles"]
  bucket_id    = b2_bucket.velero.bucket_id
}
```

- [ ] **Step 6: Create `terraform/b2/imports.tf`**

Replace `<BUCKET_ID>` and `<APPLICATION_KEY_ID>` with values from Step 1.

```hcl
import {
  to = b2_bucket.velero
  id = "<BUCKET_ID>"
}

import {
  to = b2_application_key.velero
  id = "<APPLICATION_KEY_ID>"
}
```

---

## Task 3: B2 module — K8s manifests, seal, wire

**Files:** Create `clusters/vollminlab-cluster/tofu/b2-config/app/` files; modify `clusters/vollminlab-cluster/tofu/kustomization.yaml`

- [ ] **Step 1: Create `clusters/vollminlab-cluster/tofu/b2-config/app/terraform-cr.yaml`**

```yaml
---
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: b2-config
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 10m
  approvePlan: auto
  path: ./terraform/b2
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  backendConfig:
    customConfiguration: |
      backend "s3" {
        bucket                      = "terraform-state"
        key                         = "b2/terraform.tfstate"
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
      name: b2-tf-credentials
```

- [ ] **Step 2: Create `clusters/vollminlab-cluster/tofu/b2-config/app/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: b2-config
  labels:
    app: tofu-controller
    env: production
    category: core
resources:
  - b2-tf-credentials-sealedsecret.yaml
  - terraform-cr.yaml
```

- [ ] **Step 3: Seal the B2 credentials secret**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic b2-tf-credentials \
  -n tofu \
  --from-literal=b2_master_application_key_id="$(op read 'op://Homelab/Backblaze B2/application key id')" \
  --from-literal=b2_master_application_key="$(op read 'op://Homelab/Backblaze B2/application key')" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/b2-config/app/b2-tf-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 4: Add labels to the SealedSecret template**

The output of kubeseal lacks `spec.template.metadata.labels` — add them manually. Open `b2-tf-credentials-sealedsecret.yaml` and ensure it ends with:

```yaml
  template:
    metadata:
      name: b2-tf-credentials
      namespace: tofu
      labels:
        app: tofu-controller
        env: production
        category: core
```

Verify both `metadata.labels` and `spec.template.metadata.labels` are present — Kyverno requires labels in both locations.

- [ ] **Step 5: Add b2-config to `clusters/vollminlab-cluster/tofu/kustomization.yaml`**

Add `- b2-config/app` to the resources list (maintain alphabetical order):

```yaml
resources:
  - namespace.yaml
  - authentik-config/app
  - b2-config/app
  - grafana-config/app
  - harbor-config/app
  - minio-config/app
  - tofu-controller/app
```

- [ ] **Step 6: Validate YAML structure**

```bash
kubectl apply --dry-run=client -f \
  clusters/vollminlab-cluster/tofu/b2-config/app/terraform-cr.yaml
kubectl apply --dry-run=client -f \
  clusters/vollminlab-cluster/tofu/b2-config/app/b2-tf-credentials-sealedsecret.yaml
```

Expected: `configured (dry run)` for both. If you see `unknown field`, check the apiVersion matches `infra.contrib.fluxcd.io/v1alpha2` and `bitnami.com/v1alpha1`.

- [ ] **Step 7: Commit B2 module**

```bash
git add \
  terraform/b2/ \
  clusters/vollminlab-cluster/tofu/b2-config/ \
  clusters/vollminlab-cluster/tofu/kustomization.yaml
git commit -m "feat(tofu): add B2 IaC module for Velero bucket management"
```

---

## Task 4: Radarr module — fetch existing resource IDs

**Files:** none created yet

The devopsarr/radarr provider uses integer IDs for import. Fetch them before writing the Terraform code.

- [ ] **Step 1: Fetch Radarr API key from 1Password**

```bash
RADARR_API_KEY=$(op read "op://Homelab/Radarr/api key")
echo "Key fetched: ${#RADARR_API_KEY} chars"
```

- [ ] **Step 2: Fetch existing quality profile IDs and names**

```bash
curl -s "https://radarr.vollminlab.com/api/v3/qualityprofile" \
  -H "X-Api-Key: ${RADARR_API_KEY}" | \
  jq '.[] | {id, name}'
```

Record ALL profiles. Each becomes one `radarr_quality_profile` resource + one `import` block. The `id` field is used in the import block.

- [ ] **Step 3: Fetch existing download client IDs**

```bash
curl -s "https://radarr.vollminlab.com/api/v3/downloadclient" \
  -H "X-Api-Key: ${RADARR_API_KEY}" | \
  jq '.[] | {id, name, implementation}'
```

Record the SABnzbd client ID. The `id` is used in the import block.

- [ ] **Step 4: Fetch SABnzbd API key**

```bash
SABNZBD_API_KEY=$(op read "op://Homelab/SABnzbd/api key")
echo "Key fetched: ${#SABNZBD_API_KEY} chars"
```

If this item doesn't exist in 1Password, fetch the key from the SABnzbd UI (General → Security → API Key) and store it in 1Password Homelab vault as "SABnzbd" with field "api key" before proceeding.

- [ ] **Step 5: Fetch SABnzbd download client host and port from Radarr API**

```bash
curl -s "https://radarr.vollminlab.com/api/v3/downloadclient" \
  -H "X-Api-Key: ${RADARR_API_KEY}" | \
  jq '.[] | select(.implementation == "Sabnzbd") | {id, name, fields: (.fields | map({.name, .value}))}'
```

Record `host`, `port`, `apiPath` field values — needed for the `radarr_download_client_sabnzbd` resource.

---

## Task 5: Radarr module — Terraform code

**Files:** Create `terraform/radarr/versions.tf`, `providers.tf`, `variables.tf`, `download-clients.tf`, `quality-profiles.tf`, `imports.tf`

Replace `~> X.Y` with the devopsarr/radarr version confirmed in Task 1. Replace bracketed values with IDs from Task 4.

- [ ] **Step 1: Create `terraform/radarr/versions.tf`**

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    radarr = {
      source  = "devopsarr/radarr"
      version = "~> X.Y"
    }
  }
}
```

- [ ] **Step 2: Create `terraform/radarr/providers.tf`**

```hcl
provider "radarr" {
  url     = "http://radarr.mediastack.svc.cluster.local"
  api_key = var.radarr_api_key
}
```

- [ ] **Step 3: Create `terraform/radarr/variables.tf`**

```hcl
variable "radarr_api_key" {
  description = "Radarr API key for Terraform provider authentication"
  type        = string
  sensitive   = true
}

variable "sabnzbd_api_key" {
  description = "SABnzbd API key for download client configuration"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 4: Create `terraform/radarr/download-clients.tf`**

Replace `<HOST>`, `<PORT>`, `<API_PATH>` with values from Task 4 Step 5. The `url_base` is typically `/sabnzbd` or empty string — check the `apiPath` field value.

```hcl
resource "radarr_download_client_sabnzbd" "sabnzbd" {
  name     = "SABnzbd"
  enable   = true
  priority = 1
  host     = "<HOST>"
  port     = <PORT>
  api_key  = var.sabnzbd_api_key
}
```

- [ ] **Step 5: Create `terraform/radarr/quality-profiles.tf`**

Write one resource block per profile from Task 4 Step 2. Below is the pattern — repeat for each profile. The `quality_profile_format` and `items` blocks must match what is already configured in Radarr exactly (so the import reconciles cleanly). 

Fetch the full profile definition for each profile ID to get the items:

```bash
# Example for profile ID 1
curl -s "https://radarr.vollminlab.com/api/v3/qualityprofile/1" \
  -H "X-Api-Key: ${RADARR_API_KEY}" | jq '{id, name, cutoff, items: [.items[] | {quality: .quality.name, allowed: .allowed}]}'
```

Then write the resource. Example pattern (fill in real values):

```hcl
resource "radarr_quality_profile" "any" {
  name     = "Any"
  cutoff   = 1
  upgrade_allowed = true
  
  quality_groups = []
  
  qualities = [
    {
      enabled = true
      id      = 1
      name    = "WEBDL-1080p"
    },
    # ... repeat for each quality in .items
  ]
}
```

> **Note on quality profile schema:** The exact HCL schema varies by devopsarr/radarr provider version. After fetching the provider version in Task 1, check the provider docs at `registry.terraform.io/providers/devopsarr/radarr/latest/docs/resources/quality_profile` for the exact field names before writing these blocks.

- [ ] **Step 6: Create `terraform/radarr/imports.tf`**

Replace IDs with values from Task 4. One `import` block per quality profile plus one for the download client.

```hcl
import {
  to = radarr_download_client_sabnzbd.sabnzbd
  id = "<SABNZBD_CLIENT_ID>"
}

# Repeat for each quality profile
import {
  to = radarr_quality_profile.<snake_case_name>
  id = "<PROFILE_ID>"
}
```

---

## Task 6: Radarr module — K8s manifests, seal, wire

**Files:** Create `clusters/vollminlab-cluster/tofu/radarr-config/app/` files; modify `clusters/vollminlab-cluster/tofu/kustomization.yaml`

- [ ] **Step 1: Create `clusters/vollminlab-cluster/tofu/radarr-config/app/terraform-cr.yaml`**

```yaml
---
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: radarr-config
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 10m
  approvePlan: auto
  path: ./terraform/radarr
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  backendConfig:
    customConfiguration: |
      backend "s3" {
        bucket                      = "terraform-state"
        key                         = "radarr/terraform.tfstate"
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
      name: radarr-tf-credentials
```

- [ ] **Step 2: Create `clusters/vollminlab-cluster/tofu/radarr-config/app/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: radarr-config
  labels:
    app: tofu-controller
    env: production
    category: core
resources:
  - radarr-tf-credentials-sealedsecret.yaml
  - terraform-cr.yaml
```

- [ ] **Step 3: Seal the Radarr credentials**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic radarr-tf-credentials \
  -n tofu \
  --from-literal=radarr_api_key="$(op read 'op://Homelab/Radarr/api key')" \
  --from-literal=sabnzbd_api_key="$(op read 'op://Homelab/SABnzbd/api key')" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/radarr-config/app/radarr-tf-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 4: Add labels to SealedSecret template**

Edit `radarr-tf-credentials-sealedsecret.yaml` — ensure `spec.template.metadata.labels` is present:

```yaml
  template:
    metadata:
      name: radarr-tf-credentials
      namespace: tofu
      labels:
        app: tofu-controller
        env: production
        category: core
```

- [ ] **Step 5: Add radarr-config to `clusters/vollminlab-cluster/tofu/kustomization.yaml`**

```yaml
resources:
  - namespace.yaml
  - authentik-config/app
  - b2-config/app
  - grafana-config/app
  - harbor-config/app
  - minio-config/app
  - radarr-config/app
  - tofu-controller/app
```

- [ ] **Step 6: Validate YAML**

```bash
kubectl apply --dry-run=client -f \
  clusters/vollminlab-cluster/tofu/radarr-config/app/terraform-cr.yaml
kubectl apply --dry-run=client -f \
  clusters/vollminlab-cluster/tofu/radarr-config/app/radarr-tf-credentials-sealedsecret.yaml
```

Expected: `configured (dry run)` for both.

- [ ] **Step 7: Commit Radarr module**

```bash
git add \
  terraform/radarr/ \
  clusters/vollminlab-cluster/tofu/radarr-config/ \
  clusters/vollminlab-cluster/tofu/kustomization.yaml
git commit -m "feat(tofu): add Radarr IaC module for quality profiles and download clients"
```

---

## Task 7: Sonarr module — fetch existing resource IDs

**Files:** none created yet

Same pattern as Task 4 but for Sonarr. The Sonarr API is identical in shape.

- [ ] **Step 1: Fetch Sonarr API key**

```bash
SONARR_API_KEY=$(op read "op://Homelab/Sonarr/api key")
echo "Key fetched: ${#SONARR_API_KEY} chars"
```

- [ ] **Step 2: Fetch existing quality profile IDs**

```bash
curl -s "https://sonarr.vollminlab.com/api/v3/qualityprofile" \
  -H "X-Api-Key: ${SONARR_API_KEY}" | \
  jq '.[] | {id, name}'
```

Record all profiles.

- [ ] **Step 3: Fetch existing download client**

```bash
curl -s "https://sonarr.vollminlab.com/api/v3/downloadclient" \
  -H "X-Api-Key: ${SONARR_API_KEY}" | \
  jq '.[] | select(.implementation == "Sabnzbd") | {id, name, fields: (.fields | map({.name, .value}))}'
```

Record the client ID, host, port, and apiPath.

---

## Task 8: Sonarr module — Terraform code

**Files:** Create `terraform/sonarr/versions.tf`, `providers.tf`, `variables.tf`, `download-clients.tf`, `quality-profiles.tf`, `imports.tf`

Identical structure to Radarr. Replace `radarr` with `sonarr` throughout.

- [ ] **Step 1: Create `terraform/sonarr/versions.tf`**

Replace `~> X.Y` with the devopsarr/sonarr version from Task 1 Step 3.

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    sonarr = {
      source  = "devopsarr/sonarr"
      version = "~> X.Y"
    }
  }
}
```

- [ ] **Step 2: Create `terraform/sonarr/providers.tf`**

```hcl
provider "sonarr" {
  url     = "http://sonarr.mediastack.svc.cluster.local"
  api_key = var.sonarr_api_key
}
```

- [ ] **Step 3: Create `terraform/sonarr/variables.tf`**

```hcl
variable "sonarr_api_key" {
  description = "Sonarr API key for Terraform provider authentication"
  type        = string
  sensitive   = true
}

variable "sabnzbd_api_key" {
  description = "SABnzbd API key for download client configuration"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 4: Create `terraform/sonarr/download-clients.tf`**

Replace `<HOST>`, `<PORT>` with values from Task 7 Step 3.

```hcl
resource "sonarr_download_client_sabnzbd" "sabnzbd" {
  name     = "SABnzbd"
  enable   = true
  priority = 1
  host     = "<HOST>"
  port     = <PORT>
  api_key  = var.sabnzbd_api_key
}
```

- [ ] **Step 5: Create `terraform/sonarr/quality-profiles.tf`**

Fetch full definition for each profile ID from Task 7 Step 2:

```bash
for ID in <id1> <id2> <id3>; do
  echo "--- Profile $ID ---"
  curl -s "https://sonarr.vollminlab.com/api/v3/qualityprofile/$ID" \
    -H "X-Api-Key: ${SONARR_API_KEY}" | jq '{id, name, cutoff, items: [.items[] | {quality: .quality.name, allowed: .allowed}]}'
done
```

Write one `sonarr_quality_profile` resource per profile. Check provider docs at `registry.terraform.io/providers/devopsarr/sonarr/latest/docs/resources/quality_profile` for the exact HCL schema.

- [ ] **Step 6: Create `terraform/sonarr/imports.tf`**

```hcl
import {
  to = sonarr_download_client_sabnzbd.sabnzbd
  id = "<SABNZBD_CLIENT_ID>"
}

# Repeat for each quality profile
import {
  to = sonarr_quality_profile.<snake_case_name>
  id = "<PROFILE_ID>"
}
```

---

## Task 9: Sonarr module — K8s manifests, seal, wire

**Files:** Create `clusters/vollminlab-cluster/tofu/sonarr-config/app/` files; modify `clusters/vollminlab-cluster/tofu/kustomization.yaml`

- [ ] **Step 1: Create `clusters/vollminlab-cluster/tofu/sonarr-config/app/terraform-cr.yaml`**

```yaml
---
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: sonarr-config
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 10m
  approvePlan: auto
  path: ./terraform/sonarr
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  backendConfig:
    customConfiguration: |
      backend "s3" {
        bucket                      = "terraform-state"
        key                         = "sonarr/terraform.tfstate"
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
      name: sonarr-tf-credentials
```

- [ ] **Step 2: Create `clusters/vollminlab-cluster/tofu/sonarr-config/app/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: sonarr-config
  labels:
    app: tofu-controller
    env: production
    category: core
resources:
  - sonarr-tf-credentials-sealedsecret.yaml
  - terraform-cr.yaml
```

- [ ] **Step 3: Seal Sonarr credentials**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic sonarr-tf-credentials \
  -n tofu \
  --from-literal=sonarr_api_key="$(op read 'op://Homelab/Sonarr/api key')" \
  --from-literal=sabnzbd_api_key="$(op read 'op://Homelab/SABnzbd/api key')" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/sonarr-config/app/sonarr-tf-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 4: Add labels to SealedSecret template**

```yaml
  template:
    metadata:
      name: sonarr-tf-credentials
      namespace: tofu
      labels:
        app: tofu-controller
        env: production
        category: core
```

- [ ] **Step 5: Add sonarr-config to `clusters/vollminlab-cluster/tofu/kustomization.yaml`**

```yaml
resources:
  - namespace.yaml
  - authentik-config/app
  - b2-config/app
  - grafana-config/app
  - harbor-config/app
  - minio-config/app
  - radarr-config/app
  - sonarr-config/app
  - tofu-controller/app
```

- [ ] **Step 6: Validate YAML**

```bash
kubectl apply --dry-run=client -f \
  clusters/vollminlab-cluster/tofu/sonarr-config/app/terraform-cr.yaml
kubectl apply --dry-run=client -f \
  clusters/vollminlab-cluster/tofu/sonarr-config/app/sonarr-tf-credentials-sealedsecret.yaml
```

- [ ] **Step 7: Commit Sonarr module**

```bash
git add \
  terraform/sonarr/ \
  clusters/vollminlab-cluster/tofu/sonarr-config/ \
  clusters/vollminlab-cluster/tofu/kustomization.yaml
git commit -m "feat(tofu): add Sonarr IaC module for quality profiles and download clients"
```

---

## Task 10: Cloudflare module — fetch tunnel IDs and zone info

**Files:** none created yet

The cluster has 4 Cloudflare tunnels. The Cloudflare Terraform module manages their ingress configs and DNS CNAME records. Tunnel tokens are already in SealedSecrets in the cluster — do NOT re-manage them here.

- [ ] **Step 1: Fetch Cloudflare API token and account ID from 1Password**

```bash
CF_TOKEN=$(op read "op://Homelab/Cloudflare API Token/credential")
CF_ACCOUNT_ID=$(op read "op://Homelab/Cloudflare/account id")
```

If these items do not exist in 1Password, fetch from the Cloudflare dashboard:
- API Token: `dash.cloudflare.com` → Profile → API Tokens → the token scoped for this cluster  
- Account ID: `dash.cloudflare.com` → right sidebar on the main page

Store them in 1Password (Homelab vault) before proceeding:
```bash
# API token
op item create --vault Homelab --category "API Credential" \
  --title "Cloudflare API Token" credential="<paste token>"

# Account ID (create as login if no better category)
op item create --vault Homelab --category "Login" \
  --title "Cloudflare" username="svollmin@..." "account id"="<id>"
```

- [ ] **Step 2: List all existing tunnels**

```bash
curl -s -X GET \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/cfd_tunnel" \
  -H "Authorization: Bearer ${CF_TOKEN}" \
  -H "Content-Type: application/json" | \
  jq '.result[] | {id, name, status}'
```

Expected: 4 tunnels — `authentik`, `audiobookshelf` (or similar), `jellyfin`, and one more.
Record each tunnel's `id` (UUID format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`).

- [ ] **Step 3: Fetch zone ID for vollminlab.com**

```bash
curl -s -X GET \
  "https://api.cloudflare.com/client/v4/zones?name=vollminlab.com" \
  -H "Authorization: Bearer ${CF_TOKEN}" | \
  jq '.result[0] | {id, name}'
```

Record the zone `id`.

- [ ] **Step 4: Fetch existing DNS CNAME records for tunnel hostnames**

```bash
ZONE_ID=<zone id from above>
curl -s -X GET \
  "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records?type=CNAME" \
  -H "Authorization: Bearer ${CF_TOKEN}" | \
  jq '.result[] | {id, name, content} | select(.content | contains("cfargotunnel.com"))'
```

Record the `id` (CNAME record ID) for each tunnel-backed hostname — needed for `imports.tf`.

- [ ] **Step 5: Fetch existing tunnel configs**

```bash
# Run for each tunnel UUID recorded in Step 2
TUNNEL_UUID=<tunnel-uuid>
curl -s -X GET \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${TUNNEL_UUID}/configurations" \
  -H "Authorization: Bearer ${CF_TOKEN}" | \
  jq '.result.config.ingress'
```

Record the ingress rules for each tunnel — needed to write the `tunnels.tf` config blocks accurately.

---

## Task 11: Cloudflare module — Terraform code

**Files:** Create `terraform/cloudflare/versions.tf`, `providers.tf`, `variables.tf`, `tunnels.tf`, `dns.tf`, `imports.tf`

Replace `~> X.Y` with the cloudflare v5 version from Task 1 Step 3. Use v5 resource names (breaking change from v4: `cloudflare_tunnel` → `cloudflare_zero_trust_tunnel_cloudflared`; `cloudflare_record` → `cloudflare_dns_record`).

- [ ] **Step 1: Create `terraform/cloudflare/versions.tf`**

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> X.Y"
    }
  }
}
```

- [ ] **Step 2: Create `terraform/cloudflare/providers.tf`**

```hcl
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
```

- [ ] **Step 3: Create `terraform/cloudflare/variables.tf`**

```hcl
variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zero Trust:Edit and DNS:Edit permissions"
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID"
  type        = string
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for vollminlab.com"
  type        = string
}
```

- [ ] **Step 4: Create `terraform/cloudflare/tunnels.tf`**

Write one `cloudflare_zero_trust_tunnel_cloudflared` resource and one `cloudflare_zero_trust_tunnel_cloudflared_config` resource per tunnel. The ingress rules come from Task 10 Step 5.

```hcl
resource "cloudflare_zero_trust_tunnel_cloudflared" "authentik" {
  account_id = var.cloudflare_account_id
  name       = "authentik"
  secret     = null  # managed externally via SealedSecret TUNNEL_TOKEN — do not set here
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "authentik" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.authentik.id

  config {
    # Fill in ingress rules from Task 10 Step 5 output
    ingress_rule {
      hostname = "authentik.vollminlab.com"
      service  = "https://ingress-nginx-controller.ingress-nginx.svc.cluster.local:443"
      origin_request {
        no_tls_verify = true
      }
    }
    ingress_rule {
      service = "http_status:404"
    }
  }
}

# Repeat the above pattern for each tunnel:
# cloudflare_zero_trust_tunnel_cloudflared.audiobookshelf
# cloudflare_zero_trust_tunnel_cloudflared_config.audiobookshelf
# cloudflare_zero_trust_tunnel_cloudflared.jellyfin
# cloudflare_zero_trust_tunnel_cloudflared_config.jellyfin
# (and the 4th tunnel if present)
```

> **Important:** Do NOT set `secret` on the tunnel resources when importing existing tunnels. The tunnel token is already in the cluster SealedSecrets and is irrelevant to the config resource. Setting `secret` would cause Terraform to rotate the token.

- [ ] **Step 5: Create `terraform/cloudflare/dns.tf`**

One `cloudflare_dns_record` per tunnel-backed hostname. The CNAME value is `<tunnel-uuid>.cfargotunnel.com`. Use the UUIDs from Task 10 Step 2.

```hcl
resource "cloudflare_dns_record" "authentik" {
  zone_id = var.cloudflare_zone_id
  name    = "authentik"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.authentik.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

# Repeat for each tunnel-backed hostname
# cloudflare_dns_record.audiobookshelf
# cloudflare_dns_record.jellyfin
# etc.
```

- [ ] **Step 6: Create `terraform/cloudflare/imports.tf`**

Replace all `<UUID>`, `<CNAME_RECORD_ID>` with values from Task 10.

```hcl
# Tunnels — import by tunnel UUID
import {
  to = cloudflare_zero_trust_tunnel_cloudflared.authentik
  id = "<CF_ACCOUNT_ID>/<AUTHENTIK_TUNNEL_UUID>"
}

import {
  to = cloudflare_zero_trust_tunnel_cloudflared.audiobookshelf
  id = "<CF_ACCOUNT_ID>/<AUDIOBOOKSHELF_TUNNEL_UUID>"
}

import {
  to = cloudflare_zero_trust_tunnel_cloudflared.jellyfin
  id = "<CF_ACCOUNT_ID>/<JELLYFIN_TUNNEL_UUID>"
}

# Add 4th tunnel if present

# Tunnel configs — import by account_id/tunnel_uuid (same ID as the tunnel)
import {
  to = cloudflare_zero_trust_tunnel_cloudflared_config.authentik
  id = "<CF_ACCOUNT_ID>/<AUTHENTIK_TUNNEL_UUID>"
}

import {
  to = cloudflare_zero_trust_tunnel_cloudflared_config.audiobookshelf
  id = "<CF_ACCOUNT_ID>/<AUDIOBOOKSHELF_TUNNEL_UUID>"
}

import {
  to = cloudflare_zero_trust_tunnel_cloudflared_config.jellyfin
  id = "<CF_ACCOUNT_ID>/<JELLYFIN_TUNNEL_UUID>"
}

# DNS CNAME records — import by zone_id/record_id
import {
  to = cloudflare_dns_record.authentik
  id = "<ZONE_ID>/<AUTHENTIK_CNAME_RECORD_ID>"
}

import {
  to = cloudflare_dns_record.audiobookshelf
  id = "<ZONE_ID>/<AUDIOBOOKSHELF_CNAME_RECORD_ID>"
}

import {
  to = cloudflare_dns_record.jellyfin
  id = "<ZONE_ID>/<JELLYFIN_CNAME_RECORD_ID>"
}
```

---

## Task 12: Cloudflare module — K8s manifests, seal, wire

**Files:** Create `clusters/vollminlab-cluster/tofu/cloudflare-config/app/` files; modify `clusters/vollminlab-cluster/tofu/kustomization.yaml`

- [ ] **Step 1: Create `clusters/vollminlab-cluster/tofu/cloudflare-config/app/terraform-cr.yaml`**

```yaml
---
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: cloudflare-config
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 10m
  approvePlan: auto
  path: ./terraform/cloudflare
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  backendConfig:
    customConfiguration: |
      backend "s3" {
        bucket                      = "terraform-state"
        key                         = "cloudflare/terraform.tfstate"
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
      name: cloudflare-tf-credentials
```

- [ ] **Step 2: Create `clusters/vollminlab-cluster/tofu/cloudflare-config/app/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: cloudflare-config
  labels:
    app: tofu-controller
    env: production
    category: core
resources:
  - cloudflare-tf-credentials-sealedsecret.yaml
  - terraform-cr.yaml
```

- [ ] **Step 3: Seal the Cloudflare credentials**

Note: `cloudflare_account_id` and `cloudflare_zone_id` are passed as TF variables via the secret so they're not hardcoded in the repo. Use the values from Task 10.

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic cloudflare-tf-credentials \
  -n tofu \
  --from-literal=cloudflare_api_token="$(op read 'op://Homelab/Cloudflare API Token/credential')" \
  --from-literal=cloudflare_account_id="$(op read 'op://Homelab/Cloudflare/account id')" \
  --from-literal=cloudflare_zone_id="<zone_id from Task 10 Step 3>" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/cloudflare-config/app/cloudflare-tf-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 4: Add labels to SealedSecret template**

```yaml
  template:
    metadata:
      name: cloudflare-tf-credentials
      namespace: tofu
      labels:
        app: tofu-controller
        env: production
        category: core
```

- [ ] **Step 5: Add cloudflare-config to `clusters/vollminlab-cluster/tofu/kustomization.yaml`**

```yaml
resources:
  - namespace.yaml
  - authentik-config/app
  - b2-config/app
  - cloudflare-config/app
  - grafana-config/app
  - harbor-config/app
  - minio-config/app
  - radarr-config/app
  - sonarr-config/app
  - tofu-controller/app
```

- [ ] **Step 6: Validate YAML**

```bash
kubectl apply --dry-run=client -f \
  clusters/vollminlab-cluster/tofu/cloudflare-config/app/terraform-cr.yaml
kubectl apply --dry-run=client -f \
  clusters/vollminlab-cluster/tofu/cloudflare-config/app/cloudflare-tf-credentials-sealedsecret.yaml
```

- [ ] **Step 7: Commit Cloudflare module**

```bash
git add \
  terraform/cloudflare/ \
  clusters/vollminlab-cluster/tofu/cloudflare-config/ \
  clusters/vollminlab-cluster/tofu/kustomization.yaml
git commit -m "feat(tofu): add Cloudflare IaC module for tunnels and DNS records"
```

---

## Task 13: Open PR and verify Flux reconciliation

- [ ] **Step 1: Push branch and open PR**

```bash
git push -u origin feat/phase5d-iac-modules
gh pr create \
  --title "feat(tofu): Phase 5d — add Cloudflare, Radarr, Sonarr, B2 IaC modules" \
  --body "$(cat <<'EOF'
## Summary
- Adds four tofu-controller Terraform modules: B2 (Velero bucket), Radarr (quality profiles + download client), Sonarr (quality profiles + download client), Cloudflare (tunnels + DNS)
- Each module follows the established pattern: terraform CR + SealedSecret in tofu ns, state in MinIO terraform-state bucket
- No new Flux Kustomization CRs needed — all modules wired via tofu/kustomization.yaml

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: After PR merges, watch Flux reconcile**

```bash
# Watch for all 4 new Terraform CRs to appear and reconcile
watch flux get all -n tofu
```

Expected: `b2-config`, `radarr-config`, `sonarr-config`, `cloudflare-config` all show `Applied` within ~10 minutes.

If a module shows `TFExecPlanFailed` or `TFExecApplyFailed`:

```bash
kubectl describe terraform <module-name> -n tofu | grep -A 20 "Status:"
kubectl logs -n tofu deployment/tofu-controller | grep "<module-name>" | tail -20
```

Common failure modes:
- `import block references unknown resource` — the import ID format is wrong; check provider docs for the exact import ID format
- `API token insufficient permissions` — the Cloudflare token is missing a required permission scope
- `provider plugin not found` — versions.tf has a typo in the source address

---

## Appendix: Flux wiring check

The existing `tofu-kustomization.yaml` Flux CR already watches `./clusters/vollminlab-cluster/tofu` and the `flux-kustomizations/kustomization.yaml` already lists it. No new entries are needed in either index file. Only `tofu/kustomization.yaml` requires updates (done in each module task above).

Verify before opening PR:

```bash
# Confirm tofu/kustomization.yaml lists all 4 new modules
grep -E "b2|radarr|sonarr|cloudflare" clusters/vollminlab-cluster/tofu/kustomization.yaml

# Confirm no new entries added to flux-kustomizations (should not have changed)
git diff main -- clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml
```

Expected: first command shows 4 lines, second command shows no diff.
