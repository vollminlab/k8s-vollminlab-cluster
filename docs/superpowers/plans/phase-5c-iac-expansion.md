# Phase 5c IaC Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend GitOps IaC to MinIO (buckets/users/policies), Harbor (OIDC/projects), and Grafana (SSO/notifications), and add `terraform fmt --check` + `tofu validate` CI gates for the `terraform/**` path.

**Architecture:** Each new provider gets its own `terraform/<module>/` directory mirroring the existing `terraform/authentik/` pattern. Each module gets a Terraform CR in `clusters/vollminlab-cluster/tofu/<module>-config/app/`, SealedSecrets for provider credentials as `TF_VAR_*` keys, and state stored in MinIO `terraform-state` bucket at `<module>/terraform.tfstate`. The CI job runs on all module directories so format and validity gates apply to every future module automatically. Harbor OIDC config and Grafana OAuth config currently live in Helm values — they must be migrated out in follow-up PRs after Terraform takes ownership to avoid split-brain.

**Tech Stack:** OpenTofu (tofu-controller), `aminueza/minio` provider ~>3.1, `goharbor/harbor` provider ~>3.10, `grafana/grafana` provider ~>3.7, SealedSecrets (kubeseal), Flux CD, 1Password CLI (`op`)

---

## Audit baseline (already verified — do not re-audit)

**MinIO buckets (4):** `cnpg-backups`, `loki`, `terraform-state`, `velero`

**MinIO users + policy:**
| user | policy | policy type |
|---|---|---|
| `cnpg-svc` | `cnpg-policy` | custom |
| `homepage-monitor` | `consoleAdmin` | built-in |
| `tofu-svc` | `tofu-state-policy` | custom |
| `velero-svc` | `velero-policy` | custom |

**Harbor projects:** `library` (project_id=1, public), `vollminlab` (project_id=4, public). No robot accounts.

**Harbor OIDC (current — in `extraEnvVars`):** auth_mode=oidc_auth, client_id in Authentik OAuth2 provider (see code blocks below)

**Grafana SSO (current — in `grafana.ini`):** generic_oauth enabled, client_id in Authentik OAuth2 provider (see code blocks below). No Grafana contact points yet (alerting is Alertmanager-only).

**S3 backend:** All modules reuse the existing `tofu-minio-credentials` Secret (deployed by `terraform-authentik/app` kustomization into the `tofu` namespace). This secret contains `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` for the `tofu-svc` MinIO user.

---

## PR 1 — Terraform CI Validation Job

### Task 1: Add `validate-terraform` job to CI

**Files:**
- Modify: `.github/workflows/ci.yaml`

- [ ] **Step 1: Add the new job**

Open `.github/workflows/ci.yaml`. After the `policy-validation` job (around line 1171) and before `notify-success`, insert this new job:

```yaml
  validate-terraform:
    name: Validate Terraform Modules
    runs-on: vollminlab
    needs: [validate-changes]
    if: needs.validate-changes.result == 'success'
    timeout-minutes: 10
    steps:
      - name: Checkout code
        uses: actions/checkout@v6
        with:
          fetch-depth: 0

      - name: Check for terraform changes
        id: tf-changes
        run: |
          set -euo pipefail
          if [[ "${{ github.event_name }}" == "pull_request" ]]; then
            BASE_SHA="${{ github.event.pull_request.base.sha }}"
          else
            BASE_SHA="${{ github.event.before }}"
            [[ -z "$BASE_SHA" || "$BASE_SHA" == "0000000000000000000000000000000000000000" ]] && BASE_SHA="HEAD~1"
          fi
          CHANGED=$(git diff --name-only "$BASE_SHA" HEAD | grep '^terraform/' || echo "")
          if [[ -z "$CHANGED" ]]; then
            echo "No terraform changes detected; skipping validation"
            echo "changed=false" >> $GITHUB_OUTPUT
          else
            echo "Terraform changes detected:"
            echo "$CHANGED"
            echo "changed=true" >> $GITHUB_OUTPUT
          fi

      - name: Install OpenTofu
        if: steps.tf-changes.outputs.changed == 'true'
        run: |
          set -euo pipefail
          if command -v tofu >/dev/null 2>&1; then
            echo "✅ tofu already installed: $(tofu version | head -1)"
          else
            curl -fsSL https://get.opentofu.org/install-opentofu.sh | sudo env METHOD=standalone sh
            tofu version
          fi

      - name: Check terraform fmt
        if: steps.tf-changes.outputs.changed == 'true'
        run: |
          set -euo pipefail
          FAILED=false
          for dir in terraform/*/; do
            [[ -d "$dir" ]] || continue
            echo "Checking format: $dir"
            if ! tofu fmt -check "$dir"; then
              echo "❌ $dir has formatting issues — run: tofu fmt $dir"
              FAILED=true
            else
              echo "✅ $dir is correctly formatted"
            fi
          done
          [[ "$FAILED" == "true" ]] && exit 1 || echo "✅ All modules formatted correctly"

      - name: Validate terraform modules
        if: steps.tf-changes.outputs.changed == 'true'
        run: |
          set -euo pipefail
          for dir in terraform/*/; do
            [[ -d "$dir" ]] || continue
            echo "Validating: $dir"
            (
              cd "$dir"
              tofu init -backend=false -input=false
              tofu validate
            )
            echo "✅ $dir is valid"
          done
```

- [ ] **Step 2: Add `validate-terraform` to the notify jobs' needs lists**

In `notify-success` (around line 1372) and `notify-failure` (around line 1382), add `validate-terraform` to the `needs` array:

```yaml
  notify-success:
    name: Notify Success
    runs-on: vollminlab
    needs: [validate-changes, integration-test, security-scan, policy-validation, validate-terraform]
    if: success()
```

```yaml
  notify-failure:
    name: Notify Failure
    runs-on: vollminlab
    needs: [validate-changes, integration-test, security-scan, policy-validation, validate-terraform]
    if: failure()
```

- [ ] **Step 3: Verify fmt with the existing module**

```bash
cd /home/vollmin/repos/vollminlab/k8s-vollminlab-cluster
tofu fmt -check terraform/authentik/
```

Expected: exits 0 (or lists files that need formatting — fix them first if so).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yaml
git commit -m "ci: add terraform fmt + validate job for terraform/** PRs"
```

---

## PR 2 — MinIO IaC Module

### Task 2: Create the MinIO Terraform module

**Files:**
- Create: `terraform/minio/versions.tf`
- Create: `terraform/minio/providers.tf`
- Create: `terraform/minio/variables.tf`
- Create: `terraform/minio/buckets.tf`
- Create: `terraform/minio/policies.tf`
- Create: `terraform/minio/users.tf`
- Create: `terraform/minio/imports.tf`

- [ ] **Step 1: Create `terraform/minio/versions.tf`**

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    minio = {
      source  = "aminueza/minio"
      version = "~> 3.1"
    }
  }
}
```

- [ ] **Step 2: Create `terraform/minio/providers.tf`**

```hcl
provider "minio" {
  minio_server   = "minio.minio.svc.cluster.local:9000"
  minio_user     = var.minio_access_key
  minio_password = var.minio_secret_key
  minio_ssl      = false
}
```

- [ ] **Step 3: Create `terraform/minio/variables.tf`**

```hcl
variable "minio_access_key" {
  description = "MinIO root access key for Terraform provider authentication"
  type        = string
  sensitive   = true
}

variable "minio_secret_key" {
  description = "MinIO root secret key for Terraform provider authentication"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 4: Create `terraform/minio/buckets.tf`**

```hcl
resource "minio_s3_bucket" "cnpg_backups" {
  bucket = "cnpg-backups"
  acl    = "private"

  lifecycle {
    prevent_destroy = true
  }
}

resource "minio_s3_bucket" "loki" {
  bucket = "loki"
  acl    = "private"

  lifecycle {
    prevent_destroy = true
  }
}

resource "minio_s3_bucket" "terraform_state" {
  bucket = "terraform-state"
  acl    = "private"

  lifecycle {
    prevent_destroy = true
  }
}

resource "minio_s3_bucket" "velero" {
  bucket = "velero"
  acl    = "private"

  lifecycle {
    prevent_destroy = true
  }
}
```

- [ ] **Step 5: Create `terraform/minio/policies.tf`**

Policy documents are copied from the live audit output above. The `homepage-monitor` user uses the built-in `consoleAdmin` policy so there is no custom resource for it.

```hcl
resource "minio_iam_policy" "cnpg" {
  name = "cnpg-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GetBucketLocation",
          "s3:GetObject",
        ]
        Resource = [
          "arn:aws:s3:::cnpg-backups",
          "arn:aws:s3:::cnpg-backups/*",
        ]
      },
    ]
  })
}

resource "minio_iam_policy" "velero" {
  name = "velero-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetBucketLocation",
          "s3:ListBucket",
          "s3:ListBucketMultipartUploads",
        ]
        Resource = ["arn:aws:s3:::velero"]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:DeleteObject",
          "s3:GetObject",
          "s3:ListMultipartUploadParts",
          "s3:PutObject",
        ]
        Resource = ["arn:aws:s3:::velero/*"]
      },
    ]
  })
}

resource "minio_iam_policy" "tofu_state" {
  name = "tofu-state-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:GetBucketLocation",
        ]
        Resource = [
          "arn:aws:s3:::terraform-state/*",
          "arn:aws:s3:::terraform-state",
        ]
      },
    ]
  })
}
```

- [ ] **Step 6: Create `terraform/minio/users.tf`**

For imported users, `secret` is managed outside Terraform (via SealedSecrets). Use `ignore_changes` so Terraform never rotates keys it didn't create.

```hcl
resource "minio_iam_user" "cnpg_svc" {
  name = "cnpg-svc"

  lifecycle {
    ignore_changes = [secret]
  }
}

resource "minio_iam_user" "homepage_monitor" {
  name = "homepage-monitor"

  lifecycle {
    ignore_changes = [secret]
  }
}

resource "minio_iam_user" "tofu_svc" {
  name = "tofu-svc"

  lifecycle {
    ignore_changes = [secret]
  }
}

resource "minio_iam_user" "velero_svc" {
  name = "velero-svc"

  lifecycle {
    ignore_changes = [secret]
  }
}

resource "minio_iam_user_policy_attachment" "cnpg_svc" {
  user_name   = minio_iam_user.cnpg_svc.name
  policy_name = minio_iam_policy.cnpg.name
}

resource "minio_iam_user_policy_attachment" "homepage_monitor" {
  user_name   = minio_iam_user.homepage_monitor.name
  policy_name = "consoleAdmin"
}

resource "minio_iam_user_policy_attachment" "tofu_svc" {
  user_name   = minio_iam_user.tofu_svc.name
  policy_name = minio_iam_policy.tofu_state.name
}

resource "minio_iam_user_policy_attachment" "velero_svc" {
  user_name   = minio_iam_user.velero_svc.name
  policy_name = minio_iam_policy.velero.name
}
```

- [ ] **Step 7: Create `terraform/minio/imports.tf`**

Import IDs for `aminueza/minio` v3.x:
- `minio_s3_bucket`: bucket name
- `minio_iam_policy`: policy name
- `minio_iam_user`: username (access key)
- `minio_iam_user_policy_attachment`: `<username>/<policy_name>`

```hcl
# Buckets
import {
  to = minio_s3_bucket.cnpg_backups
  id = "cnpg-backups"
}

import {
  to = minio_s3_bucket.loki
  id = "loki"
}

import {
  to = minio_s3_bucket.terraform_state
  id = "terraform-state"
}

import {
  to = minio_s3_bucket.velero
  id = "velero"
}

# IAM Policies
import {
  to = minio_iam_policy.cnpg
  id = "cnpg-policy"
}

import {
  to = minio_iam_policy.velero
  id = "velero-policy"
}

import {
  to = minio_iam_policy.tofu_state
  id = "tofu-state-policy"
}

# IAM Users
import {
  to = minio_iam_user.cnpg_svc
  id = "cnpg-svc"
}

import {
  to = minio_iam_user.homepage_monitor
  id = "homepage-monitor"
}

import {
  to = minio_iam_user.tofu_svc
  id = "tofu-svc"
}

import {
  to = minio_iam_user.velero_svc
  id = "velero-svc"
}

# Policy attachments
import {
  to = minio_iam_user_policy_attachment.cnpg_svc
  id = "cnpg-svc/cnpg-policy"
}

import {
  to = minio_iam_user_policy_attachment.homepage_monitor
  id = "homepage-monitor/consoleAdmin"
}

import {
  to = minio_iam_user_policy_attachment.tofu_svc
  id = "tofu-svc/tofu-state-policy"
}

import {
  to = minio_iam_user_policy_attachment.velero_svc
  id = "velero-svc/velero-policy"
}
```

- [ ] **Step 8: Verify module format and structure**

```bash
cd terraform/minio
tofu fmt
tofu init -backend=false
tofu validate
```

Expected: `Success! The configuration is valid.`

### Task 3: Create MinIO Flux resources

**Files:**
- Create: `clusters/vollminlab-cluster/tofu/minio-config/app/minio-tf-credentials-sealedsecret.yaml`
- Create: `clusters/vollminlab-cluster/tofu/minio-config/app/terraform-cr.yaml`
- Create: `clusters/vollminlab-cluster/tofu/minio-config/app/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/tofu/kustomization.yaml`

- [ ] **Step 1: Retrieve MinIO root credentials via 1Password**

```bash
MINIO_ACCESS_KEY=$(op item get "MinIO Root Credentials" --field username --vault Homelab 2>/dev/null \
  || op item get "MinIO" --field username --vault Homelab 2>/dev/null \
  || echo "root")
MINIO_SECRET_KEY=$(op item get "MinIO Root Credentials" --field password --vault Homelab 2>/dev/null \
  || op item get "MinIO" --field password --vault Homelab 2>/dev/null)
echo "Access key: $MINIO_ACCESS_KEY"
echo "Secret key length: ${#MINIO_SECRET_KEY}"
```

If `op` lookup fails, retrieve from cluster directly (root credentials are not sensitive enough to require op in this case since they're in the cluster as a plain Secret):
```bash
MINIO_ACCESS_KEY=$(kubectl get secret -n minio minio-credentials -o jsonpath='{.data.rootUser}' | base64 -d)
MINIO_SECRET_KEY=$(kubectl get secret -n minio minio-credentials -o jsonpath='{.data.rootPassword}' | base64 -d)
```

- [ ] **Step 2: Create and seal the MinIO provider credentials secret**

```bash
mkdir -p clusters/vollminlab-cluster/tofu/minio-config/app

# Fetch sealing cert
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

# Create and seal (keys must match TF variable names)
kubectl create secret generic minio-tf-credentials \
  -n tofu \
  --from-literal=minio_access_key="${MINIO_ACCESS_KEY}" \
  --from-literal=minio_secret_key="${MINIO_SECRET_KEY}" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/minio-config/app/minio-tf-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 3: Create `clusters/vollminlab-cluster/tofu/minio-config/app/terraform-cr.yaml`**

```yaml
---
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: minio-config
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 10m
  approvePlan: auto
  path: ./terraform/minio
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  backendConfig:
    customConfiguration: |
      backend "s3" {
        bucket                      = "terraform-state"
        key                         = "minio/terraform.tfstate"
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
      name: minio-tf-credentials
```

- [ ] **Step 4: Create `clusters/vollminlab-cluster/tofu/minio-config/app/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: minio-config
  labels:
    app: tofu-controller
    env: production
    category: core
resources:
  - minio-tf-credentials-sealedsecret.yaml
  - terraform-cr.yaml
```

- [ ] **Step 5: Add `minio-config/app` to tofu namespace kustomization**

Edit `clusters/vollminlab-cluster/tofu/kustomization.yaml`, add the new entry alphabetically:

```yaml
resources:
  - namespace.yaml
  - minio-config/app
  - terraform-authentik/app
  - tofu-controller/app
```

- [ ] **Step 6: Commit**

```bash
git add \
  terraform/minio/ \
  clusters/vollminlab-cluster/tofu/minio-config/ \
  clusters/vollminlab-cluster/tofu/kustomization.yaml
git commit -m "feat(minio): add MinIO IaC module with bucket/user/policy management"
```

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin HEAD
gh pr create --title "feat(minio): MinIO IaC module (buckets, users, policies)" --body "$(cat <<'EOF'
## Summary
- Add `terraform/minio/` module managing all 4 buckets, 4 IAM users, 3 custom policies, and 4 policy attachments
- Import all existing MinIO resources into Terraform state
- Add Flux Terraform CR (`minio-config`) wired into `tofu` namespace kustomization
- All existing user secrets are `ignore_changes = [secret]` — no key rotation on import

## Verification
After merge: `kubectl get terraform minio-config -n tofu -w` should show `Applied` within 10 minutes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8: Verify reconciliation**

After PR merges:
```bash
# Watch the Terraform resource
kubectl get terraform minio-config -n tofu -w

# Check for success
kubectl describe terraform minio-config -n tofu | tail -20

# Confirm no drift
kubectl get terraform minio-config -n tofu -o jsonpath='{.status.state}'
# Expected: "Applied"
```

---

## PR 3 — Harbor IaC Module

### Task 4: Create the Harbor Terraform module

**Files:**
- Create: `terraform/harbor/versions.tf`
- Create: `terraform/harbor/providers.tf`
- Create: `terraform/harbor/variables.tf`
- Create: `terraform/harbor/config.tf`
- Create: `terraform/harbor/projects.tf`
- Create: `terraform/harbor/imports.tf`

- [ ] **Step 1: Create `terraform/harbor/versions.tf`**

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    harbor = {
      source  = "goharbor/harbor"
      version = "~> 3.10"
    }
  }
}
```

- [ ] **Step 2: Create `terraform/harbor/providers.tf`**

```hcl
provider "harbor" {
  url      = "https://harbor.vollminlab.com"
  username = "admin"
  password = var.harbor_admin_password
  insecure = false
}
```

- [ ] **Step 3: Create `terraform/harbor/variables.tf`**

```hcl
variable "harbor_admin_password" {
  description = "Harbor admin password for Terraform provider authentication"
  type        = string
  sensitive   = true
}

variable "harbor_oidc_client_secret" {
  description = "OAuth2 client secret for the Harbor application in Authentik"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 4: Create `terraform/harbor/config.tf`**

This resource manages the Harbor authentication configuration. Importing it (`id = "auth"`) takes ownership from the current `extraEnvVars` approach.

```hcl
resource "harbor_config_auth" "oidc" {
  auth_mode          = "oidc_auth"
  oidc_name          = "Authentik"
  oidc_endpoint      = "https://authentik.vollminlab.com/application/o/harbor/"
  oidc_client_id     = "61knXoFusnE1LOVJLSSRZkLtnLFak5NylhhOxDBx" # gitleaks:allow
  oidc_client_secret = var.harbor_oidc_client_secret
  oidc_scope         = "openid,profile,email,groups"
  oidc_groups_claim  = "groups"
  oidc_admin_group   = "Harbor Admins"
  oidc_auto_onboard  = true
  oidc_verify_cert   = true
  primary_auth_mode  = true
}
```

- [ ] **Step 5: Create `terraform/harbor/projects.tf`**

```hcl
resource "harbor_project" "library" {
  name   = "library"
  public = true

  lifecycle {
    prevent_destroy = true
  }
}

resource "harbor_project" "vollminlab" {
  name   = "vollminlab"
  public = true

  lifecycle {
    prevent_destroy = true
  }
}
```

- [ ] **Step 6: Create `terraform/harbor/imports.tf`**

Import IDs for `goharbor/harbor` v3.x:
- `harbor_config_auth`: `"auth"`
- `harbor_project`: numeric project ID (as string)

```hcl
import {
  to = harbor_config_auth.oidc
  id = "auth"
}

import {
  to = harbor_project.library
  id = "1"
}

import {
  to = harbor_project.vollminlab
  id = "4"
}
```

- [ ] **Step 7: Verify module**

```bash
cd terraform/harbor
tofu fmt
tofu init -backend=false
tofu validate
```

Expected: `Success! The configuration is valid.`

### Task 5: Create Harbor Flux resources

**Files:**
- Create: `clusters/vollminlab-cluster/tofu/harbor-config/app/harbor-tf-credentials-sealedsecret.yaml`
- Create: `clusters/vollminlab-cluster/tofu/harbor-config/app/terraform-cr.yaml`
- Create: `clusters/vollminlab-cluster/tofu/harbor-config/app/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/tofu/kustomization.yaml`

- [ ] **Step 1: Retrieve Harbor credentials via 1Password**

```bash
HARBOR_ADMIN_PASS=$(op item get "Harbor Admin" --field password --vault Homelab 2>/dev/null \
  || kubectl get secret -n harbor harbor-admin-credentials -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' | base64 -d)

HARBOR_OIDC_SECRET=$(op item get "Harbor Authentik OIDC" --field "client secret" --vault Homelab 2>/dev/null \
  || op item get "Harbor OIDC" --field password --vault Homelab 2>/dev/null \
  || kubectl get secret -n harbor harbor-oidc-credentials -o jsonpath='{.data.OIDC_CLIENT_SECRET}' | base64 -d)

echo "Admin pass length: ${#HARBOR_ADMIN_PASS}"
echo "OIDC secret length: ${#HARBOR_OIDC_SECRET}"
```

- [ ] **Step 2: Create and seal the Harbor credentials secret**

```bash
mkdir -p clusters/vollminlab-cluster/tofu/harbor-config/app

kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic harbor-tf-credentials \
  -n tofu \
  --from-literal=harbor_admin_password="${HARBOR_ADMIN_PASS}" \
  --from-literal=harbor_oidc_client_secret="${HARBOR_OIDC_SECRET}" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/harbor-config/app/harbor-tf-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 3: Create `clusters/vollminlab-cluster/tofu/harbor-config/app/terraform-cr.yaml`**

```yaml
---
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: harbor-config
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 10m
  approvePlan: auto
  path: ./terraform/harbor
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  backendConfig:
    customConfiguration: |
      backend "s3" {
        bucket                      = "terraform-state"
        key                         = "harbor/terraform.tfstate"
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
      name: harbor-tf-credentials
```

- [ ] **Step 4: Create `clusters/vollminlab-cluster/tofu/harbor-config/app/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: harbor-config
  labels:
    app: tofu-controller
    env: production
    category: core
resources:
  - harbor-tf-credentials-sealedsecret.yaml
  - terraform-cr.yaml
```

- [ ] **Step 5: Update tofu namespace kustomization**

Edit `clusters/vollminlab-cluster/tofu/kustomization.yaml`:

```yaml
resources:
  - namespace.yaml
  - harbor-config/app
  - minio-config/app
  - terraform-authentik/app
  - tofu-controller/app
```

- [ ] **Step 6: Commit and open PR**

```bash
git add \
  terraform/harbor/ \
  clusters/vollminlab-cluster/tofu/harbor-config/ \
  clusters/vollminlab-cluster/tofu/kustomization.yaml
git commit -m "feat(harbor): add Harbor IaC module with OIDC config and project management"
git push -u origin HEAD
gh pr create --title "feat(harbor): Harbor IaC module (OIDC config, projects)" --body "$(cat <<'EOF'
## Summary
- Add `terraform/harbor/` module managing OIDC auth config and 2 projects (`library`, `vollminlab`)
- Import existing Harbor OIDC config and projects into Terraform state
- Terraform now owns Harbor auth config — follow-up PR will remove the `extraEnvVars` from the Harbor Helm values

## Verification
After merge: `kubectl get terraform harbor-config -n tofu -w` should show `Applied` within 10 minutes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Task 6: Harbor migration — remove extraEnvVars (follow-up PR, after harbor-config shows Applied)

**Files:**
- Modify: `clusters/vollminlab-cluster/harbor/harbor/app/configmap.yaml`
- Delete: `clusters/vollminlab-cluster/harbor/harbor/app/harbor-oidc-sealedsecret.yaml`
- Modify: `clusters/vollminlab-cluster/harbor/harbor/app/kustomization.yaml`

**Prerequisite:** Verify Terraform applied successfully first:
```bash
kubectl get terraform harbor-config -n tofu -o jsonpath='{.status.state}'
# Must be "Applied" before proceeding
```

- [ ] **Step 1: Remove `extraEnvVars` block from Harbor configmap**

In `clusters/vollminlab-cluster/harbor/harbor/app/configmap.yaml`, remove the entire `extraEnvVars` block from under `core:`:

```yaml
    core:
      extraEnvVars: []  # OIDC config managed by Terraform (tofu/harbor-config)
```

Replace the entire `core:` section's `extraEnvVars` block with the empty list above. The final `core:` section should look like:

```yaml
    core:
      extraEnvVars: []
      credentials:
        existingSecret: harbor-core-credentials
```

Wait — the original configmap has `credentials` under `registry:`, not under `core:`. Re-read the actual file before editing. The current `core:` block is at line 60-85 of the configmap and contains only `extraEnvVars`. After removing the env vars, `core:` can be set to:

```yaml
    core:
      extraEnvVars: []
```

- [ ] **Step 2: Remove `harbor-oidc-sealedsecret.yaml` from the kustomization**

In `clusters/vollminlab-cluster/harbor/harbor/app/kustomization.yaml`, remove the line referencing `harbor-oidc-sealedsecret.yaml`.

- [ ] **Step 3: Delete the sealed secret file**

```bash
git rm clusters/vollminlab-cluster/harbor/harbor/app/harbor-oidc-sealedsecret.yaml
```

- [ ] **Step 4: Commit and open PR**

```bash
git add clusters/vollminlab-cluster/harbor/harbor/app/
git commit -m "feat(harbor): migrate OIDC config from extraEnvVars to Terraform management"
git push -u origin HEAD
gh pr create --title "feat(harbor): remove OIDC extraEnvVars — Terraform now manages auth config" --body "$(cat <<'EOF'
## Summary
- Remove OIDC `extraEnvVars` from Harbor Helm values (was: AUTH_MODE, OIDC_NAME, OIDC_ENDPOINT, etc.)
- Terraform `harbor-config` already set these via the Harbor API (verified: status=Applied)
- Remove `harbor-oidc-sealedsecret.yaml` — secret no longer needed

## Risk
Low. The OIDC config is already set in Harbor's database by Terraform. Removing the env vars lets Harbor read from DB on restart, which has the same values.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## PR 4 — Grafana IaC Module

### Task 7: Create the Grafana Terraform module

**Files:**
- Create: `terraform/grafana/versions.tf`
- Create: `terraform/grafana/providers.tf`
- Create: `terraform/grafana/variables.tf`
- Create: `terraform/grafana/sso.tf`
- Create: `terraform/grafana/notifications.tf`
- Create: `terraform/grafana/imports.tf`

- [ ] **Step 1: Create `terraform/grafana/versions.tf`**

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    grafana = {
      source  = "grafana/grafana"
      version = "~> 3.7"
    }
  }
}
```

- [ ] **Step 2: Create `terraform/grafana/providers.tf`**

```hcl
provider "grafana" {
  url  = "https://grafana.vollminlab.com"
  auth = "admin:${var.grafana_admin_password}"
}
```

- [ ] **Step 3: Create `terraform/grafana/variables.tf`**

```hcl
variable "grafana_admin_password" {
  description = "Grafana admin password for Terraform provider authentication"
  type        = string
  sensitive   = true
}

variable "grafana_client_secret" {
  description = "Authentik OAuth2 client secret for the Grafana application"
  type        = string
  sensitive   = true
}

variable "pushover_user_key" {
  description = "Pushover user key for Grafana alert contact point"
  type        = string
  sensitive   = true
}

variable "pushover_api_token" {
  description = "Pushover API token for Grafana alert contact point"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 4: Create `terraform/grafana/sso.tf`**

This takes ownership of the generic_oauth settings. The same values are currently in `grafana.ini` — Grafana reads ini on startup (wins over API settings) until we do the follow-up migration PR.

```hcl
resource "grafana_sso_settings" "authentik" {
  provider_name = "generic_oauth"

  oauth2_settings {
    name                  = "Authentik"
    client_id             = "rArLch2402M3G4HWq4eqmyt0B2EThCIyX5M6CHFG" # gitleaks:allow
    client_secret         = var.grafana_client_secret
    auth_url              = "https://authentik.vollminlab.com/application/o/authorize/"
    token_url             = "https://authentik.vollminlab.com/application/o/token/"
    api_url               = "https://authentik.vollminlab.com/application/o/userinfo/"
    scopes                = "openid profile email groups"
    role_attribute_path   = "contains(groups, 'Grafana Admins') && 'Admin' || 'Viewer'"
    signout_redirect_url  = "https://authentik.vollminlab.com/application/o/grafana/end-session/"
    allow_sign_up         = true
    use_pkce              = true
    enabled               = true
  }
}
```

- [ ] **Step 5: Create `terraform/grafana/notifications.tf`**

Grafana currently has no contact points and the default notification policy receiver is "empty" (alert traffic goes through Alertmanager). This adds a Pushover contact point for Grafana-native alerts.

```hcl
resource "grafana_contact_point" "pushover" {
  name = "Pushover"

  pushover {
    user_key  = var.pushover_user_key
    api_token = var.pushover_api_token
    title     = "Grafana Alert: {{ .CommonLabels.alertname }}"
    message   = "{{ range .Alerts }}{{ .Annotations.summary }}\n{{ end }}"
    priority  = 0
  }
}

resource "grafana_notification_policy" "default" {
  group_by      = ["grafana_folder", "alertname"]
  contact_point = grafana_contact_point.pushover.name

  group_wait      = "30s"
  group_interval  = "5m"
  repeat_interval = "4h"
}
```

- [ ] **Step 6: Create `terraform/grafana/imports.tf`**

The SSO settings resource can be imported. The notification policy and contact point have no existing state to import (contact points list was empty; default policy receiver was "empty" with no custom resources).

```hcl
import {
  to = grafana_sso_settings.authentik
  id = "generic_oauth"
}
```

- [ ] **Step 7: Verify module**

```bash
cd terraform/grafana
tofu fmt
tofu init -backend=false
tofu validate
```

Expected: `Success! The configuration is valid.`

### Task 8: Create Grafana Flux resources

**Files:**
- Create: `clusters/vollminlab-cluster/tofu/grafana-config/app/grafana-tf-credentials-sealedsecret.yaml`
- Create: `clusters/vollminlab-cluster/tofu/grafana-config/app/terraform-cr.yaml`
- Create: `clusters/vollminlab-cluster/tofu/grafana-config/app/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/tofu/kustomization.yaml`

- [ ] **Step 1: Retrieve Grafana credentials via 1Password**

```bash
GRAFANA_ADMIN_PASS=$(op item get "Grafana Admin" --field password --vault Homelab 2>/dev/null \
  || kubectl get secret -n monitoring grafana-admin-credentials -o jsonpath='{.data.admin-password}' | base64 -d)

# Grafana OAuth client secret is the same one in authentik/providers_oauth2.tf (var.grafana_client_secret)
GRAFANA_CLIENT_SECRET=$(op item get "Grafana Authentik OAuth" --field "client secret" --vault Homelab 2>/dev/null \
  || op item get "Grafana OAuth" --field password --vault Homelab 2>/dev/null)

PUSHOVER_USER_KEY=$(op item get "Pushover" --field "user key" --vault Homelab 2>/dev/null \
  || op item get "Pushover" --field username --vault Homelab 2>/dev/null)

PUSHOVER_API_TOKEN=$(op item get "Pushover Grafana" --field "api token" --vault Homelab 2>/dev/null \
  || op item get "Pushover" --field password --vault Homelab 2>/dev/null)

echo "Grafana admin pass length: ${#GRAFANA_ADMIN_PASS}"
echo "Client secret length: ${#GRAFANA_CLIENT_SECRET}"
echo "Pushover user key length: ${#PUSHOVER_USER_KEY}"
echo "Pushover api token length: ${#PUSHOVER_API_TOKEN}"
```

If any credential lookup fails, check 1Password items manually:
```bash
op item list --vault Homelab | grep -iE "grafana|pushover"
```

- [ ] **Step 2: Create and seal the Grafana credentials secret**

```bash
mkdir -p clusters/vollminlab-cluster/tofu/grafana-config/app

kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic grafana-tf-credentials \
  -n tofu \
  --from-literal=grafana_admin_password="${GRAFANA_ADMIN_PASS}" \
  --from-literal=grafana_client_secret="${GRAFANA_CLIENT_SECRET}" \
  --from-literal=pushover_user_key="${PUSHOVER_USER_KEY}" \
  --from-literal=pushover_api_token="${PUSHOVER_API_TOKEN}" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/grafana-config/app/grafana-tf-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 3: Create `clusters/vollminlab-cluster/tofu/grafana-config/app/terraform-cr.yaml`**

```yaml
---
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: grafana-config
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 10m
  approvePlan: auto
  path: ./terraform/grafana
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  backendConfig:
    customConfiguration: |
      backend "s3" {
        bucket                      = "terraform-state"
        key                         = "grafana/terraform.tfstate"
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
      name: grafana-tf-credentials
```

- [ ] **Step 4: Create `clusters/vollminlab-cluster/tofu/grafana-config/app/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: grafana-config
  labels:
    app: tofu-controller
    env: production
    category: core
resources:
  - grafana-tf-credentials-sealedsecret.yaml
  - terraform-cr.yaml
```

- [ ] **Step 5: Update tofu namespace kustomization**

Edit `clusters/vollminlab-cluster/tofu/kustomization.yaml`:

```yaml
resources:
  - namespace.yaml
  - grafana-config/app
  - harbor-config/app
  - minio-config/app
  - terraform-authentik/app
  - tofu-controller/app
```

- [ ] **Step 6: Commit and open PR**

```bash
git add \
  terraform/grafana/ \
  clusters/vollminlab-cluster/tofu/grafana-config/ \
  clusters/vollminlab-cluster/tofu/kustomization.yaml
git commit -m "feat(grafana): add Grafana IaC module with SSO settings and Pushover contact point"
git push -u origin HEAD
gh pr create --title "feat(grafana): Grafana IaC module (SSO, Pushover contact point)" --body "$(cat <<'EOF'
## Summary
- Add `terraform/grafana/` module managing SSO (generic_oauth) settings, Pushover contact point, and default notification policy
- Import existing generic_oauth SSO settings into Terraform state
- grafana.ini still has the OAuth config for now — follow-up PR will remove it once Terraform is verified

## Verification
After merge: `kubectl get terraform grafana-config -n tofu -w` should show `Applied` within 10 minutes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Task 9: Grafana migration — remove OAuth from grafana.ini (follow-up PR, after grafana-config shows Applied)

**Files:**
- Modify: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`
- Delete: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/grafana-oauth-sealedsecret.yaml`
- Modify: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/kustomization.yaml`

**Prerequisite:** Verify Terraform applied successfully:
```bash
kubectl get terraform grafana-config -n tofu -o jsonpath='{.status.state}'
# Must be "Applied"
curl -sk -u "admin:${GRAFANA_ADMIN_PASS}" https://grafana.vollminlab.com/api/v1/sso-settings/generic_oauth | python3 -c "import sys,json; d=json.load(sys.stdin); print('enabled:', d['settings']['enabled'])"
# Must be: enabled: True
```

- [ ] **Step 1: Remove `auth.generic_oauth` section from configmap and `envFromSecret` reference**

In `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`:

Remove the `envFromSecret: grafana-oauth-credentials` line from the `grafana:` section.

Remove the entire `[auth.generic_oauth]` block from the `grafana.ini:` section. The ini section should go from:
```yaml
      auth.generic_oauth:
        allow_sign_up: true
        api_url: ...
        auth_url: ...
        client_id: ...
        enabled: true
        name: Authentik
        ...
```
to being completely absent — Grafana will now read OAuth settings from the database (managed by Terraform).

- [ ] **Step 2: Remove sealed secret from kustomization**

In `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/kustomization.yaml`, remove the line referencing `grafana-oauth-sealedsecret.yaml`.

- [ ] **Step 3: Delete the sealed secret file**

```bash
git rm clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/grafana-oauth-sealedsecret.yaml
```

- [ ] **Step 4: Commit and open PR**

```bash
git add clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/
git commit -m "feat(grafana): migrate OAuth config from grafana.ini to Terraform management"
git push -u origin HEAD
gh pr create --title "feat(grafana): remove OAuth from grafana.ini — Terraform manages SSO" --body "$(cat <<'EOF'
## Summary
- Remove `[auth.generic_oauth]` from `grafana.ini` in Helm values (Terraform now owns these settings via the Grafana API)
- Remove `envFromSecret: grafana-oauth-credentials` reference and `grafana-oauth-sealedsecret.yaml`
- No user-visible change: Grafana reads OAuth settings from DB on restart, which Terraform already set

## Risk
Low. Terraform verified Applied with correct settings before this PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Roadmap update (final task, after all PRs merge)

Update `docs/roadmap.md` Phase 3.1 Phase 5c status:

```markdown
- **Phase 5c** `done` — MinIO (buckets/users/policies), Harbor (OIDC/projects), and Grafana (SSO/notifications) all managed by OpenTofu IaC. Terraform CI validation job (`terraform fmt --check` + `tofu validate`) added to CI pipeline. OAuth config migrated out of Helm values into Terraform state for Harbor and Grafana.
```

---

## Self-review checklist

**Spec coverage:**
- [x] Terraform CI job (`validate-terraform` in ci.yaml) — Task 1
- [x] MinIO buckets — Task 2 `buckets.tf`
- [x] MinIO IAM users + scoped policies — Task 2 `users.tf` + `policies.tf`
- [x] Harbor OIDC config — Task 4 `config.tf`
- [x] Harbor projects — Task 4 `projects.tf`
- [x] Harbor robot accounts — none exist; no task needed
- [x] Grafana OAuth config — Task 7 `sso.tf`
- [x] Grafana notification policies + Pushover contact point — Task 7 `notifications.tf`
- [x] All existing resources imported — `imports.tf` in each module
- [x] All new Terraform CRs wired into Flux — Tasks 3, 5, 8

**Type/naming consistency:**
- `minio_iam_user_policy_attachment` resource names match `users.tf` resource names in all import IDs
- `harbor_config_auth.oidc` import ID `"auth"` is consistent with goharbor/harbor docs
- `grafana_sso_settings.authentik` import ID `"generic_oauth"` matches Grafana's SSO provider key

**No placeholders:** All code blocks are complete. All commands are runnable. SealedSecret steps use `op` with fallbacks.

**PR sequencing notes:**
1. PR 1 (CI) — no dependencies, merge first
2. PR 2 (MinIO) — independent, can merge any time
3. PR 3 (Harbor module) — must merge and verify before PR 3b (extraEnvVars removal)
4. PR 4 (Grafana module) — must merge and verify before PR 4b (grafana.ini removal)
5. Harbor extraEnvVars removal and Grafana grafana.ini removal are separate follow-up PRs (Tasks 6 and 9)
