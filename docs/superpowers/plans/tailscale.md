# Tailscale Subnet Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Tailscale as a subnet router so all cluster services and nodes are reachable over Tailscale as a failsafe when Cloudflare or ingress-nginx is unavailable.

**Architecture:** Tailscale operator runs in the `tailscale` namespace and manages a `Connector` CR that advertises `192.168.152.0/24` (nodes + MetalLB VIPs) and `192.168.100.0/24` (Pi-hole). A `terraform/tailscale/` module manages ACL autoApprovers (so routes are approved without manual console steps) and split DNS for `vollminlab.com` → Pi-hole. Two separate least-privilege OAuth clients: one for the operator (`auth_keys:write`), one for Terraform (`acls:write` + `dns:write`).

**Tech Stack:** Tailscale operator chart v1.98.3, `tailscale.com/v1alpha1 Connector` CR, Tailscale Terraform provider v0.29.1, tofu-controller, SealedSecrets, Flux CD.

---

## File Map

**Create — cluster:**
- `clusters/vollminlab-cluster/tailscale/namespace.yaml`
- `clusters/vollminlab-cluster/tailscale/kustomization.yaml`
- `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/helmrelease.yaml`
- `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/configmap.yaml`
- `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/connector.yaml`
- `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/operator-oauth-sealedsecret.yaml`
- `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/kustomization.yaml`
- `clusters/vollminlab-cluster/flux-system/repositories/tailscale-operator-helmrepository.yaml`
- `clusters/vollminlab-cluster/flux-system/flux-kustomizations/tailscale-kustomization.yaml`

**Modify — Flux indexes:**
- `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`
- `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`

**Create — Terraform:**
- `terraform/tailscale/versions.tf`
- `terraform/tailscale/providers.tf`
- `terraform/tailscale/variables.tf`
- `terraform/tailscale/acl.tf`
- `terraform/tailscale/dns.tf`

**Create — tofu workspace:**
- `clusters/vollminlab-cluster/tofu/tailscale-config/app/terraform-cr.yaml`
- `clusters/vollminlab-cluster/tofu/tailscale-config/app/tailscale-tf-credentials-sealedsecret.yaml`
- `clusters/vollminlab-cluster/tofu/tailscale-config/app/kustomization.yaml`

**Modify — tofu namespace index:**
- `clusters/vollminlab-cluster/tofu/kustomization.yaml`

---

## ⚠️ USER ACTION REQUIRED — Task 1 (do this before implementation begins)

> This task cannot be automated. It requires the Tailscale admin console.

- [ ] **Step 1: Create Operator OAuth client**

  Go to `https://login.tailscale.com/admin/settings/oauth` → New OAuth client.
  - Name: `vollminlab-cluster-operator`
  - Scope: **Devices** → Write (this grants `auth_keys:write`)
  - Click Create, copy the **Client ID** and **Client Secret**

- [ ] **Step 2: Save operator credentials to 1Password**

  In 1Password Homelab vault, create item **"Tailscale Operator OAuth Client"**:
  - Field `client_id`: paste Client ID
  - Field `client_secret`: paste Client Secret
  - Tag: `Homelab`

- [ ] **Step 3: Create Terraform OAuth client**

  Back in `https://login.tailscale.com/admin/settings/oauth` → New OAuth client.
  - Name: `vollminlab-terraform`
  - Scopes: **Policy File** → Write, **DNS** → Write
  - Click Create, copy the **Client ID** and **Client Secret**

- [ ] **Step 4: Save Terraform credentials to 1Password**

  In 1Password Homelab vault, create item **"Tailscale Terraform API Key"**:
  - Field `client_id`: paste Client ID
  - Field `client_secret`: paste Client Secret
  - Tag: `Homelab`

---

### Task 2: Create feature branch

- [ ] **Step 1: Checkout main and pull latest**

```bash
git -C /home/vollmin/repos/vollminlab/k8s-vollminlab-cluster checkout main && git -C /home/vollmin/repos/vollminlab/k8s-vollminlab-cluster pull
```

- [ ] **Step 2: Create branch**

```bash
git -C /home/vollmin/repos/vollminlab/k8s-vollminlab-cluster checkout -b feat/tailscale
```

---

### Task 3: Seal operator OAuth credentials

**Files:**
- Create: `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/operator-oauth-sealedsecret.yaml`

> Requires a live `op` session. Run `op account list` to verify; if not signed in run `op signin` first.

- [ ] **Step 1: Fetch the sealing certificate**

```bash
cd /home/vollmin/repos/vollminlab/k8s-vollminlab-cluster
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem
```

Expected: `/tmp/pub-cert.pem` created, starts with `-----BEGIN CERTIFICATE-----`

- [ ] **Step 2: Pull credentials from 1Password**

```bash
CLIENT_ID=$(op item get "Tailscale Operator OAuth Client" --vault Homelab --format json | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='client_id'))")

CLIENT_SECRET=$(op item get "Tailscale Operator OAuth Client" --vault Homelab --format json | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='client_secret'))")

echo "Got client_id: ${CLIENT_ID:0:8}... (truncated)"
```

- [ ] **Step 3: Create the directory and seal**

```bash
mkdir -p clusters/vollminlab-cluster/tailscale/tailscale-operator/app

kubectl create secret generic operator-oauth \
  -n tailscale \
  --from-literal=client_id="$CLIENT_ID" \
  --from-literal=client_secret="$CLIENT_SECRET" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tailscale/tailscale-operator/app/operator-oauth-sealedsecret.yaml

rm /tmp/pub-cert.pem
unset CLIENT_ID CLIENT_SECRET
```

- [ ] **Step 4: Verify the sealed secret file**

```bash
head -5 clusters/vollminlab-cluster/tailscale/tailscale-operator/app/operator-oauth-sealedsecret.yaml
```

Expected: starts with `apiVersion: bitnami.com/v1alpha1` and `kind: SealedSecret`

- [ ] **Step 5: Add required labels to the SealedSecret template**

The sealed file needs labels in the `.spec.template.metadata` block. Edit the file to add after the `encryptedData` section:

```yaml
  template:
    metadata:
      name: operator-oauth
      namespace: tailscale
      labels:
        app: tailscale-operator
        env: production
        category: networking
```

---

### Task 4: Create namespace and namespace-level kustomization

**Files:**
- Create: `clusters/vollminlab-cluster/tailscale/namespace.yaml`
- Create: `clusters/vollminlab-cluster/tailscale/kustomization.yaml`

- [ ] **Step 1: Create namespace.yaml**

```yaml
# clusters/vollminlab-cluster/tailscale/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: tailscale
  labels:
    app: tailscale-operator
    env: production
    category: networking
```

- [ ] **Step 2: Create kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/tailscale/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: tailscale
  labels:
    app: tailscale-operator
    env: production
    category: networking
resources:
  - namespace.yaml
  - tailscale-operator/app
```

- [ ] **Step 3: Verify**

```bash
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/tailscale/namespace.yaml
```

Expected: `namespace/tailscale created (dry run)`

---

### Task 5: Create operator app manifests

**Files:**
- Create: `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/connector.yaml`
- Create: `clusters/vollminlab-cluster/tailscale/tailscale-operator/app/kustomization.yaml`

- [ ] **Step 1: Create helmrelease.yaml**

```yaml
# clusters/vollminlab-cluster/tailscale/tailscale-operator/app/helmrelease.yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: tailscale-operator
  namespace: tailscale
  labels:
    app: tailscale-operator
    env: production
    category: networking
spec:
  interval: 10m
  releaseName: tailscale-operator
  chart:
    spec:
      chart: tailscale-operator
      version: 1.98.3
      sourceRef:
        kind: HelmRepository
        name: tailscale-operator-repo
        namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: tailscale-operator-values
      valuesKey: values.yaml
```

- [ ] **Step 2: Create configmap.yaml**

```yaml
# clusters/vollminlab-cluster/tailscale/tailscale-operator/app/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: tailscale-operator-values
  namespace: tailscale
  labels:
    app: tailscale-operator
    env: production
    category: networking
data:
  values.yaml: |
    operatorConfig:
      hostname: vollminlab-cluster-operator
      defaultTags:
        - tag:k8s

    resources:
      requests:
        cpu: 10m
        memory: 64Mi
      limits:
        cpu: 100m
        memory: 256Mi
```

- [ ] **Step 3: Create connector.yaml**

```yaml
# clusters/vollminlab-cluster/tailscale/tailscale-operator/app/connector.yaml
apiVersion: tailscale.com/v1alpha1
kind: Connector
metadata:
  name: vollminlab-cluster
  namespace: tailscale
  labels:
    app: tailscale-operator
    env: production
    category: networking
spec:
  hostname: vollminlab-cluster
  subnetRouter:
    advertiseRoutes:
      - "192.168.152.0/24"
      - "192.168.100.0/24"
```

- [ ] **Step 4: Create app kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/tailscale/tailscale-operator/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: tailscale-operator-deployment
  namespace: flux-system
  labels:
    app: tailscale-operator
    env: production
    category: networking
resources:
  - helmrelease.yaml
  - configmap.yaml
  - connector.yaml
  - operator-oauth-sealedsecret.yaml
```

- [ ] **Step 5: Verify manifests**

```bash
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/tailscale/tailscale-operator/app/helmrelease.yaml
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/tailscale/tailscale-operator/app/configmap.yaml
```

Expected: both return `(dry run)` with no errors.

> **Kyverno note:** The operator creates a pod for the connector (`vollminlab-cluster` Connector CR). If Kyverno blocks that pod for missing resource limits, create a `ProxyClass` resource in `tailscale` namespace with resource limits and reference it in the Connector CR via `spec.proxyClass`. This is an operator-managed pod; the resource limits in `configmap.yaml` only apply to the operator itself.

---

### Task 6: Create HelmRepository + Flux Kustomization CR + wire indexes

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/tailscale-operator-helmrepository.yaml`
- Create: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/tailscale-kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`

- [ ] **Step 1: Create tailscale-operator-helmrepository.yaml**

```yaml
# clusters/vollminlab-cluster/flux-system/repositories/tailscale-operator-helmrepository.yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: tailscale-operator-repo
  namespace: flux-system
  labels:
    app: tailscale-operator
    env: production
    category: networking
spec:
  interval: 5m
  url: https://pkgs.tailscale.com/helmcharts
  timeout: 3m
```

- [ ] **Step 2: Create tailscale-kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/flux-system/flux-kustomizations/tailscale-kustomization.yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: tailscale
  namespace: flux-system
  labels:
    app: tailscale-operator
    env: production
    category: networking
spec:
  interval: 10m
  path: ./clusters/vollminlab-cluster/tailscale
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
  targetNamespace: tailscale
  timeout: 5m
  dependsOn:
    - name: sealed-secrets
```

- [ ] **Step 3: Add to flux-kustomizations index**

Open `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml` and add `- tailscale-kustomization.yaml` in alphabetical order (between `sealed-secrets-kustomization.yaml` and `tofu-kustomization.yaml`).

- [ ] **Step 4: Add to repositories index**

Open `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml` and add `- tailscale-operator-helmrepository.yaml` in alphabetical order (between `sonarr-ocirepository.yaml` and `tofu-controller-helmrepository.yaml`).

- [ ] **Step 5: Verify all four files**

```bash
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/flux-system/repositories/tailscale-operator-helmrepository.yaml
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/flux-system/flux-kustomizations/tailscale-kustomization.yaml
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml
kubectl apply --dry-run=client -f clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml
```

Expected: all four return `(dry run)` with no errors.

---

### Task 7: Create Terraform module

**Files:**
- Create: `terraform/tailscale/versions.tf`
- Create: `terraform/tailscale/providers.tf`
- Create: `terraform/tailscale/variables.tf`
- Create: `terraform/tailscale/acl.tf`
- Create: `terraform/tailscale/dns.tf`

- [ ] **Step 1: Create versions.tf**

```hcl
# terraform/tailscale/versions.tf
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    tailscale = {
      source  = "tailscale/tailscale"
      version = "~> 0.29"
    }
  }
}
```

- [ ] **Step 2: Create providers.tf**

```hcl
# terraform/tailscale/providers.tf
provider "tailscale" {
  oauth_client_id     = var.tailscale_oauth_client_id
  oauth_client_secret = var.tailscale_oauth_client_secret
}
```

- [ ] **Step 3: Create variables.tf**

```hcl
# terraform/tailscale/variables.tf
variable "tailscale_oauth_client_id" {
  type      = string
  sensitive = true
}

variable "tailscale_oauth_client_secret" {
  type      = string
  sensitive = true
}
```

- [ ] **Step 4: Create acl.tf**

> Before writing this file, check the current ACL in the Tailscale admin console at `https://login.tailscale.com/admin/acls`. If there are any customizations beyond the default allow-all rule, include them verbatim in the `acls` block below.

```hcl
# terraform/tailscale/acl.tf
resource "tailscale_acl" "main" {
  acl = jsonencode({
    tagOwners = {
      "tag:k8s" = []
    }

    autoApprovers = {
      routes = {
        "192.168.152.0/24" = ["tag:k8s"]
        "192.168.100.0/24" = ["tag:k8s"]
      }
    }

    acls = [
      {
        action = "accept"
        src    = ["*"]
        dst    = ["*:*"]
      }
    ]
  })
}
```

- [ ] **Step 5: Create dns.tf**

```hcl
# terraform/tailscale/dns.tf
resource "tailscale_dns_split_nameservers" "vollminlab" {
  domain      = "vollminlab.com"
  nameservers = ["192.168.100.2"]
}
```

> If `vollminlab.com` split DNS already exists in the Tailscale admin console, import it before applying:
> ```bash
> tofu -chdir=terraform/tailscale import tailscale_dns_split_nameservers.vollminlab vollminlab.com
> ```

- [ ] **Step 6: Validate the module**

```bash
cd /home/vollmin/repos/vollminlab/k8s-vollminlab-cluster
terraform -chdir=terraform/tailscale fmt --check
tofu -chdir=terraform/tailscale validate 2>/dev/null || terraform -chdir=terraform/tailscale validate
```

Expected: `fmt --check` exits 0 (no formatting issues); `validate` prints `Success!`

---

### Task 8: Seal Terraform credentials + create tofu workspace

**Files:**
- Create: `clusters/vollminlab-cluster/tofu/tailscale-config/app/tailscale-tf-credentials-sealedsecret.yaml`
- Create: `clusters/vollminlab-cluster/tofu/tailscale-config/app/terraform-cr.yaml`
- Create: `clusters/vollminlab-cluster/tofu/tailscale-config/app/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/tofu/kustomization.yaml`

- [ ] **Step 1: Fetch sealing cert**

```bash
cd /home/vollmin/repos/vollminlab/k8s-vollminlab-cluster
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem
```

- [ ] **Step 2: Pull Terraform credentials from 1Password**

```bash
TF_CLIENT_ID=$(op item get "Tailscale Terraform API Key" --vault Homelab --format json | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='client_id'))")

TF_CLIENT_SECRET=$(op item get "Tailscale Terraform API Key" --vault Homelab --format json | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f['label']=='client_secret'))")

echo "Got client_id: ${TF_CLIENT_ID:0:8}... (truncated)"
```

- [ ] **Step 3: Seal and create directory**

```bash
mkdir -p clusters/vollminlab-cluster/tofu/tailscale-config/app

kubectl create secret generic tailscale-tf-credentials \
  -n tofu \
  --from-literal=tailscale_oauth_client_id="$TF_CLIENT_ID" \
  --from-literal=tailscale_oauth_client_secret="$TF_CLIENT_SECRET" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/tailscale-config/app/tailscale-tf-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
unset TF_CLIENT_ID TF_CLIENT_SECRET
```

- [ ] **Step 4: Add labels to SealedSecret template**

Edit `clusters/vollminlab-cluster/tofu/tailscale-config/app/tailscale-tf-credentials-sealedsecret.yaml` and add after the `encryptedData` section:

```yaml
  template:
    metadata:
      name: tailscale-tf-credentials
      namespace: tofu
      labels:
        app: tofu-controller
        env: production
        category: core
```

- [ ] **Step 5: Create terraform-cr.yaml**

```yaml
# clusters/vollminlab-cluster/tofu/tailscale-config/app/terraform-cr.yaml
---
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: tailscale-config
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 10m
  approvePlan: auto
  path: ./terraform/tailscale
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  backendConfig:
    customConfiguration: |
      backend "s3" {
        bucket                      = "terraform-state"
        key                         = "tailscale/terraform.tfstate"
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
      name: tailscale-tf-credentials
```

- [ ] **Step 6: Create workspace kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/tofu/tailscale-config/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: tailscale-config
  labels:
    app: tofu-controller
    env: production
    category: core
resources:
  - tailscale-tf-credentials-sealedsecret.yaml
  - terraform-cr.yaml
```

- [ ] **Step 7: Add tailscale-config to tofu namespace kustomization**

Open `clusters/vollminlab-cluster/tofu/kustomization.yaml` and add `- tailscale-config/app` in alphabetical order (between `sonarr-config/app` and `tofu-controller/app`).

---

### Task 9: Commit and open PR

- [ ] **Step 1: Stage all files explicitly**

```bash
git add \
  clusters/vollminlab-cluster/tailscale/namespace.yaml \
  clusters/vollminlab-cluster/tailscale/kustomization.yaml \
  clusters/vollminlab-cluster/tailscale/tailscale-operator/app/helmrelease.yaml \
  clusters/vollminlab-cluster/tailscale/tailscale-operator/app/configmap.yaml \
  clusters/vollminlab-cluster/tailscale/tailscale-operator/app/connector.yaml \
  clusters/vollminlab-cluster/tailscale/tailscale-operator/app/operator-oauth-sealedsecret.yaml \
  clusters/vollminlab-cluster/tailscale/tailscale-operator/app/kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/repositories/tailscale-operator-helmrepository.yaml \
  clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/flux-kustomizations/tailscale-kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml \
  clusters/vollminlab-cluster/tofu/tailscale-config/app/terraform-cr.yaml \
  clusters/vollminlab-cluster/tofu/tailscale-config/app/tailscale-tf-credentials-sealedsecret.yaml \
  clusters/vollminlab-cluster/tofu/tailscale-config/app/kustomization.yaml \
  clusters/vollminlab-cluster/tofu/kustomization.yaml \
  terraform/tailscale/versions.tf \
  terraform/tailscale/providers.tf \
  terraform/tailscale/variables.tf \
  terraform/tailscale/acl.tf \
  terraform/tailscale/dns.tf \
  docs/superpowers/plans/tailscale.md
```

- [ ] **Step 2: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(tailscale): deploy subnet router operator and Terraform IaC

Deploys Tailscale operator v1.98.3 in `tailscale` namespace with a
Connector CR advertising 192.168.152.0/24 (nodes + VIPs) and
192.168.100.0/24 (Pi-hole). Terraform module manages ACL autoApprovers
and split DNS for vollminlab.com, eliminating manual console steps.

Two separate least-privilege OAuth clients: operator (auth_keys:write)
and Terraform provider (acls:write + dns:write).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/tailscale
gh pr create \
  --title "feat(tailscale): deploy subnet router as cluster failsafe" \
  --body "$(cat <<'EOF'
## Summary

- Deploys Tailscale operator v1.98.3 in dedicated `tailscale` namespace
- `Connector` CR advertises `192.168.152.0/24` (nodes + MetalLB VIPs) and `192.168.100.0/24` (Pi-hole)
- `terraform/tailscale/` module manages ACL `autoApprovers` and split DNS for `vollminlab.com` → Pi-hole
- Two separate least-privilege OAuth clients (operator + Terraform provider)
- No manual Tailscale admin console steps required after merge

When connected to Tailscale: `*.vollminlab.com` resolves via Pi-hole → routes through ingress-nginx exactly as if on the home LAN. SSH to any node IP works directly.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Post-merge verification**

After the PR merges and Flux reconciles (~10 min):

```bash
# Operator running
kubectl get pods -n tailscale

# Connector device registered (look for vollminlab-cluster)
kubectl get connector -n tailscale

# Tofu workspace reconciling
kubectl get terraform tailscale-config -n tofu

# From a Tailscale-connected device (not on home LAN):
# nslookup grafana.vollminlab.com    → should return 192.168.152.244
# curl -sk https://grafana.vollminlab.com  → should respond
# ssh 192.168.152.8                  → should reach k8scp01
```
