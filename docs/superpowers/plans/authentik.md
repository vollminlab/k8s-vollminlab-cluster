# Authentik SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Authentik as the cluster-wide IdP with CNPG-backed PostgreSQL and an external proxy outpost — then wire every cluster web UI to SSO via native OIDC or nginx forward-auth across four sequential PRs.

**Architecture:** Dedicated CNPG Cluster CR for Authentik's PostgreSQL (Authentik 2025.10+ removed the Redis dependency entirely); Authentik server + worker via official Helm chart; external proxy outpost Deployment handles all nginx forward-auth. OIDC-capable apps integrate natively; remaining apps use nginx forward-auth annotations with app auth disabled.

**Tech Stack:** goauthentik/authentik Helm chart, CNPG Cluster CR, cloudflared Deployment (tunnel token pattern), nginx forward-auth annotations, Flux HelmRelease + OCIRepository/HelmRepository, SealedSecrets.

**Reference:** `docs/authentik-design.md`

---

## Phase 1 — Core Infrastructure (PR 1)

### Task 1: Create branch and look up current chart versions

**Files:** none

- [ ] **Step 1: Start from clean main**

```bash
git checkout main && git pull
```

- [ ] **Step 2: Create branch**

```bash
git checkout -b feat/authentik-core
```

- [ ] **Step 3: Look up current Authentik chart version**

```bash
helm repo add authentik https://charts.goauthentik.io
helm repo update
helm search repo authentik/authentik --versions | head -5
```

Note the latest stable version (e.g. `2025.x.x`). Use this version for both the HelmRelease and the proxy outpost image tag in Phase 2.

- [ ] **Step 4: Look up current Bitnami Redis chart version**

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
helm search repo bitnami/redis --versions | head -5
```

Note the latest stable chart version (e.g. `20.x.x`).

- [ ] **Step 5: Look up current cloudflared image tag**

Check the existing deployed version:
```bash
grep "image:" clusters/vollminlab-cluster/mediastack/cloudflared-jellyfin/app/deployment.yaml
```
Use the same tag for the Authentik cloudflared deployment.

---

### Task 2: Authentik namespace and directory structure

**Files:**
- Create: `clusters/vollminlab-cluster/authentik/namespace.yaml`
- Create: `clusters/vollminlab-cluster/authentik/kustomization.yaml`
- Create: `clusters/vollminlab-cluster/authentik/cnpg/app/kustomization.yaml`
- Create: `clusters/vollminlab-cluster/authentik/authentik/app/kustomization.yaml`
- Create: `clusters/vollminlab-cluster/authentik/cloudflared-authentik/app/kustomization.yaml`

- [ ] **Step 1: Create authentik namespace**

```yaml
# clusters/vollminlab-cluster/authentik/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: authentik
  labels:
    app: authentik
    env: production
    category: security
```

- [ ] **Step 2: Create authentik top-level kustomization**

```yaml
# clusters/vollminlab-cluster/authentik/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: authentik
resources:
  - namespace.yaml
  - cnpg/app/kustomization.yaml
  - authentik/app/kustomization.yaml
  - cloudflared-authentik/app/kustomization.yaml
```

- [ ] **Step 3: Create cnpg app kustomization**

```yaml
# clusters/vollminlab-cluster/authentik/cnpg/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: authentik-cnpg-app
resources:
  - cluster.yaml
  - authentik-db-credentials-sealedsecret.yaml
```

- [ ] **Step 4: Create authentik app kustomization**

```yaml
# clusters/vollminlab-cluster/authentik/authentik/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: authentik-app
resources:
  - helmrelease.yaml
  - configmap.yaml
  - ingress.yaml
  - authentik-credentials-sealedsecret.yaml
```

- [ ] **Step 5: Create cloudflared-authentik app kustomization**

```yaml
# clusters/vollminlab-cluster/authentik/cloudflared-authentik/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: cloudflared-authentik-app
resources:
  - cloudflared-authentik-tunnel-sealedsecret.yaml
  - deployment.yaml
```

---

### Task 3: CNPG Cluster CR for Authentik

**Files:**
- Create: `clusters/vollminlab-cluster/authentik/cnpg/app/cluster.yaml`
- Create: `clusters/vollminlab-cluster/authentik/cnpg/app/authentik-db-credentials-sealedsecret.yaml`

- [ ] **Step 1: Create CNPG Cluster CR**

```yaml
# clusters/vollminlab-cluster/authentik/cnpg/app/cluster.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: authentik-db
  namespace: authentik
  labels:
    app: authentik-db
    env: production
    category: security
spec:
  instances: 1
  inheritedMetadata:
    labels:
      app: authentik-db
      env: production
      category: security
  storage:
    size: 5Gi
    storageClass: longhorn
  bootstrap:
    initdb:
      database: authentik
      owner: authentik
      secret:
        name: authentik-db-credentials
      postInitSQL:
        - GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO authentik
        - GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO authentik
        - ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO authentik
        - ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO authentik
```

- [ ] **Step 2: Verify Longhorn has capacity for a 5Gi PVC (×1 replica = 5Gi needed)**

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,SCHEDULABLE:.metadata.annotations.node\.longhorn\.io/longhorn-schedulable-storage'
```

Confirm at least one node has >5Gi schedulable. (Default replica count is 3 for Longhorn, so actually need 15Gi total spread across 3 nodes.)

- [ ] **Step 3: Generate a strong database password**

```bash
openssl rand -base64 32
```

Store in 1Password as **"Authentik DB Password"** in the Homelab vault.

- [ ] **Step 4: Seal the DB credentials**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic authentik-db-credentials -n authentik \
  --from-literal=username=authentik \
  --from-literal=password='<your-generated-password>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/authentik/cnpg/app/authentik-db-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

---

### Task 4: Authentik HelmRepository and HelmRelease

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/authentik-helmrepository.yaml`
- Create: `clusters/vollminlab-cluster/authentik/authentik/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/authentik/authentik/app/configmap.yaml`

- [ ] **Step 1: Create Authentik HelmRepository**

```yaml
# clusters/vollminlab-cluster/flux-system/repositories/authentik-helmrepository.yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: authentik-repo
  namespace: flux-system
  labels:
    app: authentik
    env: production
    category: security
spec:
  url: https://charts.goauthentik.io
  interval: 12h
```

- [ ] **Step 2: Create Authentik HelmRelease**

```yaml
# clusters/vollminlab-cluster/authentik/authentik/app/helmrelease.yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: authentik
  namespace: authentik
  labels:
    app: authentik
    env: production
    category: security
spec:
  interval: 5m
  chart:
    spec:
      chart: authentik
      version: "2025.x.x"   # replace with version from Task 1 Step 3
      sourceRef:
        kind: HelmRepository
        name: authentik-repo
        namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: authentik-values
      valuesKey: values.yaml
    - kind: Secret
      name: authentik-credentials
      valuesKey: values.yaml
```

- [ ] **Step 3: Create Authentik ConfigMap (non-sensitive values)**

```yaml
# clusters/vollminlab-cluster/authentik/authentik/app/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: authentik-values
  namespace: authentik
  labels:
    app: authentik
    env: production
    category: security
data:
  values.yaml: |
    authentik:
      error_reporting:
        enabled: false
      postgresql:
        host: authentik-db-rw.authentik.svc.cluster.local
        name: authentik
        user: authentik
    server:
      ingress:
        enabled: false
      podLabels:
        app: authentik
        env: production
        category: security
      resources:
        requests:
          cpu: 100m
          memory: 512Mi
        limits:
          cpu: 500m
          memory: 1Gi

    worker:
      podLabels:
        app: authentik
        env: production
        category: security
      resources:
        requests:
          cpu: 50m
          memory: 256Mi
        limits:
          cpu: 300m
          memory: 512Mi
```

---

### Task 5: Authentik credentials SealedSecret

**Files:**
- Create: `clusters/vollminlab-cluster/authentik/authentik/app/authentik-credentials-sealedsecret.yaml`

- [ ] **Step 1: Generate a secret key (64+ chars)**

```bash
openssl rand -base64 60 | tr -d '\n'
```

Store in 1Password as **"Authentik Secret Key"** in the Homelab vault. This value must never change after initial deployment (rotating it invalidates all sessions and tokens).

- [ ] **Step 2: Generate a bootstrap admin password**

```bash
openssl rand -base64 24
```

Store in 1Password as **"Authentik Admin Password"** in the Homelab vault. This is the initial admin password — change it to a proper passphrase after first login.

- [ ] **Step 3: Fetch sealing cert and seal credentials**

The secret uses `valuesFrom` with `values.yaml` key to override Helm values:

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic authentik-credentials -n authentik \
  --from-literal=values.yaml="authentik:
  secret_key: '<your-secret-key>'
  postgresql:
    password: '<your-db-password-from-task-3>'
  bootstrap_password: '<your-bootstrap-admin-password>'" \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/authentik/authentik/app/authentik-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

---

### Task 6: Authentik ingress

**Files:**
- Create: `clusters/vollminlab-cluster/authentik/authentik/app/ingress.yaml`

- [ ] **Step 1: Create Authentik ingress**

```yaml
# clusters/vollminlab-cluster/authentik/authentik/app/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: authentik-ingress
  namespace: authentik
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-buffer-size: "128k"
    shlink.vollminlab.com/slug: authentik
  labels:
    app: authentik
    env: production
    category: security
spec:
  ingressClassName: nginx
  rules:
    - host: authentik.vollminlab.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: authentik-server
                port:
                  number: 80
  tls:
    - hosts:
        - authentik.vollminlab.com
      secretName: wildcard-tls
```

Note: `proxy-buffer-size: 128k` is required for Authentik — its auth headers exceed nginx's default 4k buffer and will cause 502 errors without it.

---

### Task 7: Cloudflared deployment for Authentik

**Files:**
- Create: `clusters/vollminlab-cluster/authentik/cloudflared-authentik/app/deployment.yaml`
- Create: `clusters/vollminlab-cluster/authentik/cloudflared-authentik/app/cloudflared-authentik-tunnel-sealedsecret.yaml`

- [ ] **Step 1: Create a new Cloudflare Tunnel for Authentik**

In the Cloudflare Zero Trust dashboard:
1. Go to **Networks > Tunnels > Create a tunnel**
2. Name it `vollminlab-authentik`
3. Add route: `authentik.vollminlab.com` → `http://authentik-server.authentik.svc.cluster.local:80`
4. Copy the tunnel token

Store the token in 1Password as **"Cloudflare Tunnel Token — Authentik"** in the Homelab vault.

- [ ] **Step 2: Seal the tunnel token**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic cloudflared-authentik-tunnel-credentials -n authentik \
  --from-literal=tunnel-token='<your-tunnel-token>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/authentik/cloudflared-authentik/app/cloudflared-authentik-tunnel-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 3: Create cloudflared Deployment**

Use the same image tag noted in Task 1 Step 5:

```yaml
# clusters/vollminlab-cluster/authentik/cloudflared-authentik/app/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cloudflared-authentik
  namespace: authentik
  labels:
    app: cloudflared-authentik
    env: production
    category: networking
spec:
  replicas: 1
  selector:
    matchLabels:
      app: cloudflared-authentik
  template:
    metadata:
      labels:
        app: cloudflared-authentik
        env: production
        category: networking
    spec:
      containers:
        - name: cloudflared
          image: cloudflare/cloudflared:2026.3.0   # use tag from Task 1 Step 5
          args:
            - tunnel
            - --no-autoupdate
            - run
          env:
            - name: TUNNEL_TOKEN
              valueFrom:
                secretKeyRef:
                  name: cloudflared-authentik-tunnel-credentials
                  key: tunnel-token
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
```

---

### Task 8: Update both Flux index files

**Files:**
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`

- [ ] **Step 1: Add to repositories index (alphabetical order)**

Add `authentik-helmrepository.yaml` to the `resources` list in alphabetical position:

```yaml
# In flux-system/repositories/kustomization.yaml, add:
  - authentik-helmrepository.yaml   # after arc-runners-ocirepository.yaml
```

- [ ] **Step 2: Add to flux-kustomizations index**

Add at the end of the resources list:

```yaml
# In flux-system/flux-kustomizations/kustomization.yaml, add:
  - authentik-kustomization.yaml
```

- [ ] **Step 3: Create the Flux Kustomization CR**

```yaml
# clusters/vollminlab-cluster/flux-system/flux-kustomizations/authentik-kustomization.yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: authentik
  namespace: flux-system
  labels:
    app: authentik
    env: production
    category: security
spec:
  interval: 10m
  path: ./clusters/vollminlab-cluster/authentik
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
```

---

### Task 9: Commit and open PR 1

- [ ] **Step 1: Stage all Phase 1 files**

```bash
git add \
  clusters/vollminlab-cluster/authentik/ \
  clusters/vollminlab-cluster/flux-system/repositories/authentik-helmrepository.yaml \
  clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/flux-kustomizations/authentik-kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml
```

- [ ] **Step 2: Commit**

```bash
git commit -m "feat: deploy Authentik SSO core infrastructure

Add CNPG Cluster CR, Authentik server and worker, nginx ingress, and
cloudflared tunnel for authentik.vollminlab.com.
Phase 1 of 4 — no service integrations yet.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/authentik-core
gh pr create --title "feat: deploy Authentik SSO core infrastructure" --body "$(cat <<'EOF'
## Summary
- CNPG Cluster CR for Authentik PostgreSQL (`authentik-db`, 1 instance, 5Gi)
- Authentik server + worker via official goauthentik Helm chart (2026.2.2 — no Redis dependency)
- Ingress: `authentik.vollminlab.com` with nginx + wildcard TLS
- Cloudflared tunnel for external access (needed for Jellyfin external auth)
- Flux index files updated

Phase 1 of 4. No service integrations in this PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Monitor Flux reconciliation after merge**

```bash
flux get kustomizations authentik --watch
flux get helmreleases -n authentik
```

Expected: all show `Ready=True` within ~5 minutes.

- [ ] **Step 5: Verify Authentik is reachable**

Navigate to `https://authentik.vollminlab.com` — you should see the Authentik login page.

---

### Task 10: Phase 1 manual step — configure Authentik and create proxy outpost

**This task cannot be automated. Complete before starting Phase 2.**

- [ ] **Step 1: Log in with bootstrap credentials**

Go to `https://authentik.vollminlab.com` and log in with:
- Username: `akadmin`
- Password: the bootstrap password from Task 8 Step 2

- [ ] **Step 2: Immediately enforce MFA on the admin account**

Go to **Admin > Directory > Users > akadmin > Edit**. Add a TOTP authenticator device. Do not proceed until MFA is active on this account.

- [ ] **Step 3: Change the admin password**

Set a strong passphrase. Update the entry in 1Password.

- [ ] **Step 4: Create a Proxy Provider for forward-auth**

Go to **Admin > Applications > Providers > Create > Proxy Provider**:
- Name: `Forward Auth (All Domains)`
- Authorization flow: `default-provider-authorization-explicit-consent` (or create a simpler implicit flow)
- Mode: **Forward Auth (single application)** — we will use domain-level forward auth
- Actually use **Forward Auth (domain level)** so one provider covers all forward-auth ingresses
- External host: `https://authentik.vollminlab.com`

- [ ] **Step 5: Create an Application for the outpost**

Go to **Admin > Applications > Applications > Create**:
- Name: `Forward Auth`
- Slug: `forward-auth`
- Provider: select the provider created in Step 4

- [ ] **Step 6: Create the Proxy Outpost**

Go to **Admin > Applications > Outposts > Create**:
- Name: `authentik-proxy`
- Type: `Proxy`
- Integration: `Local Kubernetes` (leave blank — we are deploying manually)
- Applications: add `Forward Auth`

- [ ] **Step 7: Copy the outpost token**

Click on the outpost > **View Deployment Info**. Copy the `AUTHENTIK_TOKEN` value.

Store in 1Password as **"Authentik Proxy Outpost Token"** in the Homelab vault.

---

## Phase 2 — Jellyfin Ecosystem (PR 2)

### Task 14: Create branch for Phase 2

- [ ] **Step 1: Start from clean main (after PR 1 merges)**

```bash
git checkout main && git pull
git checkout -b feat/authentik-jellyfin
```

---

### Task 15: Deploy the proxy outpost

**Files:**
- Create: `clusters/vollminlab-cluster/authentik/authentik-proxy/app/deployment.yaml`
- Create: `clusters/vollminlab-cluster/authentik/authentik-proxy/app/service.yaml`
- Create: `clusters/vollminlab-cluster/authentik/authentik-proxy/app/kustomization.yaml`
- Create: `clusters/vollminlab-cluster/authentik/authentik-proxy/app/authentik-proxy-token-sealedsecret.yaml`
- Modify: `clusters/vollminlab-cluster/authentik/kustomization.yaml`

- [ ] **Step 1: Seal the proxy outpost token from Task 13 Step 7**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic authentik-proxy-token -n authentik \
  --from-literal=token='<outpost-token-from-task-13>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/authentik/authentik-proxy/app/authentik-proxy-token-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 2: Look up the proxy image tag**

The proxy image tag must match the Authentik server version deployed in Phase 1:

```bash
kubectl get helmrelease authentik -n authentik -o jsonpath='{.spec.chart.spec.version}'
```

Use this version as the image tag (e.g. `2025.4.1`).

- [ ] **Step 3: Create the proxy Deployment**

```yaml
# clusters/vollminlab-cluster/authentik/authentik-proxy/app/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: authentik-proxy
  namespace: authentik
  labels:
    app: authentik-proxy
    env: production
    category: security
spec:
  replicas: 1
  selector:
    matchLabels:
      app: authentik-proxy
  template:
    metadata:
      labels:
        app: authentik-proxy
        env: production
        category: security
    spec:
      containers:
        - name: proxy
          image: ghcr.io/goauthentik/proxy:2025.x.x   # use version from Step 2
          env:
            - name: AUTHENTIK_HOST
              value: "https://authentik.vollminlab.com"
            - name: AUTHENTIK_INSECURE
              value: "false"
            - name: AUTHENTIK_TOKEN
              valueFrom:
                secretKeyRef:
                  name: authentik-proxy-token
                  key: token
          ports:
            - containerPort: 9000
              name: http
            - containerPort: 9443
              name: https
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
```

- [ ] **Step 4: Create the proxy Service**

```yaml
# clusters/vollminlab-cluster/authentik/authentik-proxy/app/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: authentik-proxy
  namespace: authentik
  labels:
    app: authentik-proxy
    env: production
    category: security
spec:
  selector:
    app: authentik-proxy
  ports:
    - name: http
      port: 9000
      targetPort: 9000
    - name: https
      port: 9443
      targetPort: 9443
```

- [ ] **Step 5: Create proxy app kustomization**

```yaml
# clusters/vollminlab-cluster/authentik/authentik-proxy/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: authentik-proxy-app
resources:
  - authentik-proxy-token-sealedsecret.yaml
  - deployment.yaml
  - service.yaml
```

- [ ] **Step 6: Add authentik-proxy to the authentik top-level kustomization**

Edit `clusters/vollminlab-cluster/authentik/kustomization.yaml` to add the proxy:

```yaml
resources:
  - namespace.yaml
  - cnpg/app/kustomization.yaml
  - authentik/app/kustomization.yaml
  - cloudflared-authentik/app/kustomization.yaml
  - authentik-proxy/app/kustomization.yaml   # add this line
```

---

### Task 16: Deploy Jellyseerr

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/jellyseerr-ocirepository.yaml`
- Create: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/jellyseerr-kustomization.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/jellyseerr/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/jellyseerr/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/jellyseerr/app/ingress.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/jellyseerr/app/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/mediastack/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`

- [ ] **Step 1: Look up current Jellyseerr TrueCharts tag**

```bash
helm registry login oci.trueforge.org --username guest --password guest 2>/dev/null || true
helm show chart oci://oci.trueforge.org/truecharts/jellyseerr 2>/dev/null | grep "^version:" | head -1
```

If that doesn't work, check https://truecharts.org/charts/stable/jellyseerr/ for the current chart version.

- [ ] **Step 2: Create Jellyseerr OCIRepository**

```yaml
# clusters/vollminlab-cluster/flux-system/repositories/jellyseerr-ocirepository.yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: jellyseerr-repo
  namespace: flux-system
  labels:
    app: jellyseerr
    env: production
    category: media
spec:
  url: oci://oci.trueforge.org/truecharts/jellyseerr
  interval: 5m
  ref:
    tag: "x.x.x"   # replace with version from Step 1
```

- [ ] **Step 3: Create Jellyseerr HelmRelease**

```yaml
# clusters/vollminlab-cluster/mediastack/jellyseerr/app/helmrelease.yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: jellyseerr
  namespace: mediastack
  labels:
    app: jellyseerr
    env: production
    category: media
spec:
  interval: 5m
  chartRef:
    kind: OCIRepository
    name: jellyseerr-repo
    namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: jellyseerr-values
      valuesKey: values.yaml
```

- [ ] **Step 4: Create Jellyseerr ConfigMap**

```yaml
# clusters/vollminlab-cluster/mediastack/jellyseerr/app/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: jellyseerr-values
  namespace: mediastack
  labels:
    app: jellyseerr
    env: production
    category: media
data:
  values.yaml: |
    podLabels:
      app: jellyseerr
      env: production
      category: media
    resources:
      requests:
        cpu: 100m
        memory: 256Mi
      limits:
        cpu: 500m
        memory: 512Mi
    persistence:
      config:
        enabled: true
        existingClaim: pvc-jellyseerr-config
```

- [ ] **Step 5: Create Jellyseerr PVC**

```yaml
# clusters/vollminlab-cluster/mediastack/pvcs/pvc-jellyseerr-config.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-jellyseerr-config
  namespace: mediastack
  labels:
    app: jellyseerr
    env: production
    category: media
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 1Gi
```

Add `pvc-jellyseerr-config.yaml` to `clusters/vollminlab-cluster/mediastack/pvcs/kustomization.yaml` resources list.

- [ ] **Step 6: Create Jellyseerr ingress**

```yaml
# clusters/vollminlab-cluster/mediastack/jellyseerr/app/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: jellyseerr-ingress
  namespace: mediastack
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    shlink.vollminlab.com/slug: jellyseerr
  labels:
    app: jellyseerr
    env: production
    category: media
spec:
  ingressClassName: nginx
  rules:
    - host: jellyseerr.vollminlab.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: jellyseerr
                port:
                  number: 5055
  tls:
    - hosts:
        - jellyseerr.vollminlab.com
      secretName: wildcard-tls
```

- [ ] **Step 7: Create Jellyseerr app kustomization**

```yaml
# clusters/vollminlab-cluster/mediastack/jellyseerr/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: jellyseerr-app
resources:
  - helmrelease.yaml
  - configmap.yaml
  - ingress.yaml
```

- [ ] **Step 8: Add Jellyseerr to mediastack kustomization**

Add `- jellyseerr/app/kustomization.yaml` to `clusters/vollminlab-cluster/mediastack/kustomization.yaml` resources list. Jellyseerr is reconciled by the existing `mediastack` Flux Kustomization CR — no separate CR needed.

- [ ] **Step 9: Update the repositories Flux index file**

Add to `flux-system/repositories/kustomization.yaml` in alphabetical position (after `jellyfin-helmrepository.yaml`):
```yaml
  - jellyseerr-ocirepository.yaml
```

No change to `flux-system/flux-kustomizations/kustomization.yaml` — mediastack already covers it.

---

### Task 17: Configure Jellyfin OIDC

Jellyfin OIDC requires the SSO plugin. This is configured in Jellyfin's UI after deployment, not via Helm values. The steps below walk through the UI configuration.

**Files:** none (Jellyfin OIDC config is persisted in the `pvc-jellyfin-config` PVC, not in Git)

- [ ] **Step 1: Install the SSO plugin in Jellyfin**

Navigate to `https://jellyfin.vollminlab.com` → Admin Dashboard → Plugins → Catalog → search "SSO Authentication" → Install → Restart Jellyfin.

- [ ] **Step 2: Create an OIDC provider in Authentik**

In Authentik (`https://authentik.vollminlab.com`):
1. Go to **Admin > Applications > Providers > Create > OAuth2/OpenID Provider**
2. Name: `Jellyfin`
3. Authorization flow: implicit (or explicit if preferred)
4. Client type: `Confidential`
5. Redirect URIs: `https://jellyfin.vollminlab.com/sso/OID/redirect/authentik`
6. Copy the **Client ID** and **Client Secret**

- [ ] **Step 3: Create an Application in Authentik**

1. Go to **Admin > Applications > Applications > Create**
2. Name: `Jellyfin`
3. Slug: `jellyfin`
4. Provider: select the provider from Step 2
5. Set an icon/launch URL: `https://jellyfin.vollminlab.com`

- [ ] **Step 4: Configure SSO plugin in Jellyfin**

In Jellyfin → Admin Dashboard → Plugins → SSO Authentication → Configure:
- Provider Name: `authentik`
- OID Endpoint: `https://authentik.vollminlab.com/application/o/jellyfin/`
- Client ID: from Step 2
- Client Secret: from Step 2
- Enabled: true
- Enable Authorization by Plugin: true (disables Jellyfin local auth)

- [ ] **Step 5: Test login**

Log out of Jellyfin and verify SSO redirect to Authentik works. Confirm you can log back in via Authentik.

---

### Task 18: Remove Overseerr

**Files:**
- Delete: `clusters/vollminlab-cluster/mediastack/overseerr/` (entire directory)
- Delete: `clusters/vollminlab-cluster/flux-system/repositories/overseerr-ocirepository.yaml`
- Delete: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/overseerr-kustomization.yaml` (if it exists separately)
- Modify: `clusters/vollminlab-cluster/mediastack/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`

- [ ] **Step 1: Remove Overseerr files**

```bash
git rm -r clusters/vollminlab-cluster/mediastack/overseerr/
git rm clusters/vollminlab-cluster/flux-system/repositories/overseerr-ocirepository.yaml
```

- [ ] **Step 2: Remove from mediastack kustomization**

Edit `clusters/vollminlab-cluster/mediastack/kustomization.yaml` and remove the overseerr line.

- [ ] **Step 3: Remove from both Flux index files**

Remove `overseerr-ocirepository.yaml` from `flux-system/repositories/kustomization.yaml`.
Remove the overseerr kustomization entry from `flux-system/flux-kustomizations/kustomization.yaml`.

---

### Task 19: Commit and open PR 2

- [ ] **Step 1: Stage all Phase 2 files**

```bash
git add \
  clusters/vollminlab-cluster/authentik/ \
  clusters/vollminlab-cluster/mediastack/jellyseerr/ \
  clusters/vollminlab-cluster/mediastack/pvcs/ \
  clusters/vollminlab-cluster/mediastack/kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/
```

- [ ] **Step 2: Commit**

```bash
git commit -m "feat: Authentik proxy outpost + Jellyseerr, remove Overseerr

Deploy external proxy outpost, add Jellyseerr with OIDC, wire Jellyfin
OIDC to Authentik. Remove Overseerr. Phase 2 of 4.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/authentik-jellyfin
gh pr create --title "feat: Authentik proxy outpost + Jellyseerr, remove Overseerr" --body "$(cat <<'EOF'
## Summary
- External proxy outpost Deployment + Service in `authentik` namespace
- Jellyseerr deployed (replaces Overseerr), OIDC configured against Authentik
- Jellyfin OIDC wired to Authentik via SSO plugin
- Overseerr HelmRelease and resources removed

Phase 2 of 4. Unblocks Plex deprecation.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Phase 3 — OIDC Apps (PR 3)

### Task 20: Create branch for Phase 3

- [ ] **Step 1: Start from clean main**

```bash
git checkout main && git pull
git checkout -b feat/authentik-oidc-apps
```

For each OIDC app below, the pattern is:
1. Create an OAuth2/OIDC Provider in Authentik UI
2. Create an Application in Authentik UI
3. Update the app's ConfigMap values with OIDC settings
4. Commit

### Task 21: Grafana OAuth2

**Files:**
- Modify: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`

- [ ] **Step 1: Create Grafana OIDC provider in Authentik**

1. **Admin > Applications > Providers > Create > OAuth2/OpenID Provider**
2. Name: `Grafana`
3. Redirect URI: `https://grafana.vollminlab.com/login/generic_oauth`
4. Copy Client ID and Client Secret

- [ ] **Step 2: Create Authentik Application for Grafana**

Name: `Grafana`, Slug: `grafana`, Provider: from Step 1.

- [ ] **Step 3: Add OAuth2 config to Grafana values in the ConfigMap (non-secret values only)**

In `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`, under the `grafana:` section, add inside `grafana.ini:`:

```yaml
    grafana.ini:
      feature_toggles:
        kubernetesDashboards: false
      server:
        root_url: https://grafana.vollminlab.com
      auth.generic_oauth:
        enabled: true
        name: Authentik
        allow_sign_up: true
        client_id: "<client-id-from-step-1>"
        scopes: "openid profile email"
        auth_url: "https://authentik.vollminlab.com/application/o/authorize/"
        token_url: "https://authentik.vollminlab.com/application/o/token/"
        api_url: "https://authentik.vollminlab.com/application/o/userinfo/"
        role_attribute_path: "contains(groups, 'admins') && 'Admin' || 'Viewer'"
```

Do NOT put `client_secret` in the ConfigMap — that violates the project secrets rule.

- [ ] **Step 4: Seal the Grafana client secret**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic grafana-oauth-credentials -n monitoring \
  --from-literal=GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET='<client-secret-from-step-1>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/grafana-oauth-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 5: Reference the secret as an env var in Grafana values**

In the ConfigMap, add under the `grafana:` section:

```yaml
    extraEnvVars:
      - name: GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET
        valueFrom:
          secretKeyRef:
            name: grafana-oauth-credentials
            key: GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET
```

Also add the SealedSecret to the `monitoring/kube-prometheus-stack/app/kustomization.yaml` resources list.

---

### Task 22: Harbor OIDC

**Files:**
- Modify: `clusters/vollminlab-cluster/harbor/harbor/app/configmap.yaml`

- [ ] **Step 1: Create Harbor OIDC provider in Authentik**

1. **Admin > Applications > Providers > Create > OAuth2/OpenID Provider**
2. Name: `Harbor`
3. Redirect URI: `https://harbor.vollminlab.com/c/oidc/callback`
4. Copy Client ID and Client Secret

- [ ] **Step 2: Create Authentik Application for Harbor**

Name: `Harbor`, Slug: `harbor`, Provider: from Step 1.

- [ ] **Step 3: Configure OIDC in Harbor UI**

Harbor's OIDC is configured via its web UI (not Helm values):
1. Log in to Harbor as admin → Administration → Configuration → Authentication
2. Auth Mode: `OIDC Provider`
3. OIDC Provider Name: `Authentik`
4. OIDC Endpoint: `https://authentik.vollminlab.com/application/o/harbor/`
5. Client ID / Secret: from Step 1
6. Group Claim Name: `groups`
7. Verify Certificate: enabled
8. Save

---

### Task 23: Headlamp OIDC

**Files:**
- Modify: `clusters/vollminlab-cluster/flux-system/headlamp/app/configmap.yaml`

- [ ] **Step 1: Create Headlamp OIDC provider in Authentik**

1. **Admin > Applications > Providers > Create > OAuth2/OpenID Provider**
2. Name: `Headlamp`
3. Redirect URI: `https://headlamp.vollminlab.com/oidc-callback`
4. Copy Client ID and Client Secret

- [ ] **Step 2: Create Authentik Application for Headlamp**

Name: `Headlamp`, Slug: `headlamp`, Provider: from Step 1.

- [ ] **Step 3: Update Headlamp ConfigMap values (non-secret values only)**

In the Headlamp ConfigMap, add OIDC configuration:

```yaml
    config:
      oidc:
        clientID: "<client-id>"
        issuerURL: "https://authentik.vollminlab.com/application/o/headlamp/"
        scopes: "openid profile email groups"
```

Do NOT put `clientSecret` in the ConfigMap.

- [ ] **Step 4: Seal the Headlamp client secret**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic headlamp-oidc-credentials -n flux-system \
  --from-literal=clientSecret='<client-secret-from-step-1>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/flux-system/headlamp/app/headlamp-oidc-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

Check the Headlamp chart values for the exact env var or secret reference key to inject the clientSecret — it may use `HEADLAMP_OIDC_CLIENT_SECRET` or a similar env var. Add a `secretKeyRef` in the chart's `extraEnv` values pointing at this SealedSecret, and add the SealedSecret to `flux-system/headlamp/app/kustomization.yaml`.

---

### Task 24: Portainer OAuth2

**Files:**
- No Helm values change needed — Portainer OAuth2 is configured via UI.

- [ ] **Step 1: Create Portainer OAuth2 provider in Authentik**

1. **Admin > Applications > Providers > Create > OAuth2/OpenID Provider**
2. Name: `Portainer`
3. Redirect URI: `https://portainer.vollminlab.com`
4. Copy Client ID and Client Secret

- [ ] **Step 2: Create Authentik Application for Portainer**

Name: `Portainer`, Slug: `portainer`, Provider: from Step 1.

- [ ] **Step 3: Configure OAuth2 in Portainer UI**

Portainer → Settings → Authentication → OAuth:
- Use SSO: enabled
- Client ID / Secret: from Step 1
- Authorization URL: `https://authentik.vollminlab.com/application/o/authorize/`
- Access Token URL: `https://authentik.vollminlab.com/application/o/token/`
- Resource URL: `https://authentik.vollminlab.com/application/o/userinfo/`
- Logout URL: `https://authentik.vollminlab.com/application/o/portainer/end-session/`
- User identifier: `preferred_username`
- Scopes: `openid profile email`

---

### Task 25: Audiobookshelf OIDC

**Files:**
- No Helm values change needed — Audiobookshelf OIDC is configured via UI.

- [ ] **Step 1: Create Audiobookshelf OIDC provider in Authentik**

1. **Admin > Applications > Providers > Create > OAuth2/OpenID Provider**
2. Name: `Audiobookshelf`
3. Redirect URI: `https://audiobookshelf.vollminlab.com/auth/openid/callback`
4. Copy Client ID and Client Secret

- [ ] **Step 2: Create Authentik Application for Audiobookshelf**

Name: `Audiobookshelf`, Slug: `audiobookshelf`, Provider: from Step 1.

- [ ] **Step 3: Configure OIDC in Audiobookshelf UI**

Audiobookshelf → Settings → Authentication → OpenID Connect:
- Issuer URL: `https://authentik.vollminlab.com/application/o/audiobookshelf/`
- Client ID / Secret: from Step 1
- Enable OIDC: on

---

### Task 26: MinIO Console OIDC

**Files:**
- Modify: `clusters/vollminlab-cluster/minio/minio/app/configmap.yaml`

- [ ] **Step 1: Create MinIO OIDC provider in Authentik**

1. **Admin > Applications > Providers > Create > OAuth2/OpenID Provider**
2. Name: `MinIO`
3. Redirect URI: `https://minio.vollminlab.com/oauth_callback`
4. Copy Client ID and Client Secret

- [ ] **Step 2: Create Authentik Application for MinIO**

Name: `MinIO`, Slug: `minio`, Provider: from Step 1.

- [ ] **Step 3: Add OIDC config to MinIO ConfigMap values (non-secret values only)**

In the MinIO ConfigMap, add under the appropriate values section:

```yaml
    oidc:
      enabled: true
      configUrl: "https://authentik.vollminlab.com/application/o/minio/.well-known/openid-configuration"
      clientId: "<client-id>"
      claimName: "policy"
      scopes: "openid,profile,email"
      redirectUri: "https://minio.vollminlab.com/oauth_callback"
      callbackStyle: "web"
```

Do NOT put `clientSecret` in the ConfigMap.

- [ ] **Step 4: Seal the MinIO OIDC client secret**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic minio-oidc-credentials -n minio \
  --from-literal=MINIO_IDENTITY_OPENID_CLIENT_SECRET='<client-secret-from-step-1>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/minio/minio/app/minio-oidc-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

Verify the MinIO chart's env var name for the OIDC client secret (`MINIO_IDENTITY_OPENID_CLIENT_SECRET` is the standard MinIO env var). Add a `secretKeyRef` in the chart's `extraEnv` values, and add the SealedSecret to `minio/minio/app/kustomization.yaml`.

---

### Task 27: Commit and open PR 3

- [ ] **Step 1: Stage all Phase 3 changes**

```bash
git add \
  clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml \
  clusters/vollminlab-cluster/flux-system/headlamp/app/configmap.yaml \
  clusters/vollminlab-cluster/minio/minio/app/configmap.yaml
```

- [ ] **Step 2: Commit**

```bash
git commit -m "feat: wire OIDC apps to Authentik (Grafana, Harbor, Headlamp, Portainer, Audiobookshelf, MinIO)

Phase 3 of 4. All native OIDC integrations. Harbor and Portainer OIDC
configured via UI; Grafana and MinIO via Helm values.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/authentik-oidc-apps
gh pr create --title "feat: OIDC integrations for Grafana, Harbor, Headlamp, Portainer, Audiobookshelf, MinIO" --body "$(cat <<'EOF'
## Summary
- Grafana: OAuth2 via kube-prometheus-stack values
- Harbor: OIDC configured via UI
- Headlamp: OIDC via Helm values
- Portainer: OAuth2 via UI
- Audiobookshelf: OIDC via UI
- MinIO Console: OIDC via Helm values

Phase 3 of 4. No forward-auth changes in this PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Phase 4 — Forward-auth Sweep (PR 4)

### Task 28: Create branch for Phase 4

- [ ] **Step 1: Start from clean main**

```bash
git checkout main && git pull
git checkout -b feat/authentik-forward-auth
```

### Task 29: Create forward-auth Authentik application in UI

Before adding ingress annotations, create a single Application + Provider in Authentik that covers all forward-auth domains.

- [ ] **Step 1: Verify the domain-level forward auth provider exists**

The provider created in Task 13 Step 4 should already cover domain-level forward auth. If not, create it now using the same steps.

- [ ] **Step 2: Add each forward-auth service as a protected application**

For each service in the forward-auth list, in Authentik:
1. **Admin > Applications > Applications > Create**
2. Name: `<Service Name>` (e.g. `Longhorn`, `Homepage`, `Radarr`, etc.)
3. Slug: `<service-name>`
4. Provider: select the Forward Auth domain-level provider
5. Launch URL: `https://<service>.vollminlab.com`

Do this for: Longhorn, Homepage, Radarr, Sonarr, Bazarr, Prowlarr, SABnzbd, Tautulli, Shlink Web, Policy Reporter.

---

### Task 30: Forward-auth ingress annotations — reference snippet

All forward-auth ingresses get identical annotations. The service name, host, and backend differ; the auth annotations are the same for all.

**Standard forward-auth annotation block** (reference — do not repeat in every task below):

```yaml
annotations:
  nginx.ingress.kubernetes.io/ssl-redirect: "true"
  nginx.ingress.kubernetes.io/auth-url: "http://authentik-proxy.authentik.svc.cluster.local:9000/outpost.goauthentik.io/auth/nginx"
  nginx.ingress.kubernetes.io/auth-signin: "https://authentik.vollminlab.com/outpost.goauthentik.io/start?rd=$scheme://$http_host$escaped_request_uri"
  nginx.ingress.kubernetes.io/auth-response-headers: "Set-Cookie,X-authentik-username,X-authentik-groups,X-authentik-email,X-authentik-name,X-authentik-uid"
  nginx.ingress.kubernetes.io/auth-snippet: |
    proxy_set_header X-Original-URL $scheme://$http_host$request_uri;
  nginx.ingress.kubernetes.io/proxy-buffer-size: "128k"
```

---

### Task 31: Longhorn UI and Homepage forward-auth

**Files:**
- Modify: ingress for Longhorn UI
- Modify: ingress for Homepage

- [ ] **Step 1: Find and update the Longhorn UI ingress**

```bash
find clusters/vollminlab-cluster/longhorn-system -name "ingress.yaml"
```

Add the standard forward-auth annotation block from Task 30 to the Longhorn ingress.

- [ ] **Step 2: Find and update the Homepage ingress**

```bash
find clusters/vollminlab-cluster/homepage -name "ingress.yaml"
```

Add the standard forward-auth annotation block. Homepage has no built-in auth — adding these annotations is all that's needed.

---

### Task 32: Arr stack forward-auth (Radarr, Sonarr, Bazarr, Prowlarr, SABnzbd)

For each arr app: add forward-auth ingress annotations AND disable the app's own authentication in its ConfigMap values. Both changes must be in the same commit — never leave an app with auth disabled and no forward-auth in place.

**Files (repeat pattern for each app):**
- Modify: `clusters/vollminlab-cluster/mediastack/<app>/app/ingress.yaml`
- Modify: `clusters/vollminlab-cluster/mediastack/<app>/app/configmap.yaml`

- [ ] **Step 1: Radarr — add forward-auth annotations to ingress and disable app auth**

In `clusters/vollminlab-cluster/mediastack/radarr/app/ingress.yaml`, add the standard forward-auth annotation block from Task 30.

The arr apps (Radarr, Sonarr, Bazarr, Prowlarr) all support auth configuration via environment variables using double-underscore notation. Add to the configmap `env:` section:

```yaml
    env:
      RADARR__AUTH__METHOD: External
      RADARR__AUTH__REQUIRED: DisabledForLocalAddresses
```

This is fully config-managed — no UI steps needed for the arr apps. The `External` method tells the app to trust the upstream reverse proxy (Authentik forward-auth) rather than enforcing its own auth. Verify the exact key names against the deployed chart during Phase 4 implementation — the double-underscore pattern is standard across all *arr apps but the section name may vary slightly (`AUTH` vs `Authentication`).

- [ ] **Step 2: Sonarr — same pattern as Radarr**

Add forward-auth annotations to `mediastack/sonarr/app/ingress.yaml`.
Disable auth in `mediastack/sonarr/app/configmap.yaml`.

- [ ] **Step 3: Bazarr — same pattern**

Add forward-auth annotations to `mediastack/bazarr/app/ingress.yaml`.
Disable auth in `mediastack/bazarr/app/configmap.yaml`.

- [ ] **Step 4: Prowlarr — same pattern**

Add forward-auth annotations to `mediastack/prowlarr/app/ingress.yaml`.
Disable auth in `mediastack/prowlarr/app/configmap.yaml`.

- [ ] **Step 5: SABnzbd — same pattern**

Add forward-auth annotations to `mediastack/sabnzbd/app/ingress.yaml`.
In the SABnzbd ConfigMap, disable web authentication:

```yaml
    sabnzbd:
      misc:
        username: ""
        password: ""
        no_authload: 1
```

---

### Task 33: Tautulli, Shlink Web, Policy Reporter forward-auth

**Files:**
- Modify: `clusters/vollminlab-cluster/mediastack/tautulli/app/ingress.yaml`
- Modify: `clusters/vollminlab-cluster/mediastack/tautulli/app/configmap.yaml`
- Modify: ingress for Shlink Web
- Modify: ingress for Policy Reporter UI

- [ ] **Step 1: Tautulli — add forward-auth and disable app auth**

Add standard forward-auth annotation block to the Tautulli ingress.

In the Tautulli ConfigMap, disable local auth (Tautulli has `http_username`/`http_password` settings — set both to empty to disable):

```yaml
    tautulli:
      auth:
        enabled: false
```

(Check the TrueCharts Tautulli chart for the exact values path.)

- [ ] **Step 2: Shlink Web — add forward-auth annotations**

```bash
find clusters/vollminlab-cluster/shlink -name "ingress.yaml" | xargs grep -l "shlink-web\|shlink.vollminlab"
```

Add the standard forward-auth annotation block to the Shlink Web ingress.

- [ ] **Step 3: Policy Reporter UI — add forward-auth annotations**

```bash
find clusters/vollminlab-cluster/kyverno -name "ingress.yaml" | xargs grep -l "policy-reporter"
```

Add the standard forward-auth annotation block to the Policy Reporter UI ingress. Policy Reporter UI has no built-in auth — annotations are all that's needed.

---

### Task 34: Commit and open PR 4

- [ ] **Step 1: Stage all Phase 4 changes**

```bash
git add \
  clusters/vollminlab-cluster/longhorn-system/ \
  clusters/vollminlab-cluster/homepage/ \
  clusters/vollminlab-cluster/mediastack/radarr/ \
  clusters/vollminlab-cluster/mediastack/sonarr/ \
  clusters/vollminlab-cluster/mediastack/bazarr/ \
  clusters/vollminlab-cluster/mediastack/prowlarr/ \
  clusters/vollminlab-cluster/mediastack/sabnzbd/ \
  clusters/vollminlab-cluster/mediastack/tautulli/ \
  clusters/vollminlab-cluster/shlink/ \
  clusters/vollminlab-cluster/kyverno/
```

- [ ] **Step 2: Commit**

```bash
git commit -m "feat: forward-auth sweep — all remaining services behind Authentik

Add nginx forward-auth annotations to all non-OIDC services and disable
per-app authentication where applicable. Phase 4 of 4.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/authentik-forward-auth
gh pr create --title "feat: forward-auth sweep — all remaining services behind Authentik" --body "$(cat <<'EOF'
## Summary
- Forward-auth annotations added to: Longhorn UI, Homepage, Radarr, Sonarr, Bazarr, Prowlarr, SABnzbd, Tautulli, Shlink Web, Policy Reporter UI
- App-level auth disabled for arr stack, Tautulli, SABnzbd, Shlink Web
- Homepage and Policy Reporter UI have no built-in auth — forward-auth is the sole gate

Phase 4 of 4 — Authentik rollout complete.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Phase 5 — Authentik Config as IaC (PR 5a + PR 5b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all UI-managed Authentik configuration with GitOps-reconciled Terraform, making the full cluster rebuild-from-Git compliant.

**Architecture:** `flux-iac/tofu-controller` v0.16.3 runs in a new `tofu` namespace; a `Terraform` CR (kind `infra.contrib.fluxcd.io/v1alpha2`) points at `./terraform/authentik/` in this repo; state lives in MinIO bucket `terraform-state` with a scoped `tofu-svc` access key; all secrets injected via `spec.backendConfigsFrom` / `spec.varsFrom`. Import strategy: OpenTofu native `import {}` blocks in `imports.tf` — Flux handles the import on first apply.

**Tech Stack:** flux-iac/tofu-controller v0.16.3, goauthentik/authentik provider v2026.2.0, portainer/portainer provider v1.29, MinIO S3 backend, SealedSecrets.

**Scoping resolved (do not re-investigate):**
- `portainer/portainer` v1.29 supports `portainer_settings` with `oauth_settings` block — Portainer OAuth is **fully manageable via Terraform**.
- Audiobookshelf has no Terraform provider — **Authentik side** (provider + application + groups) goes in Terraform; **ABS app settings** (OIDC config in its UI) remain UI-only.
- Terraform CR kind is `Terraform` (`infra.contrib.fluxcd.io/v1alpha2`), **not** `TerraformObject`.
- Backend creds are NOT in `spec.backendConfig.customConfiguration` — they are injected via `spec.backendConfigsFrom`.

**File layout:**

```
clusters/vollminlab-cluster/tofu/
  namespace.yaml
  kustomization.yaml
  tofu-controller/app/
    helmrelease.yaml
    configmap.yaml
    kustomization.yaml
  terraform-authentik/app/
    terraform-cr.yaml
    authentik-tf-credentials-sealedsecret.yaml
    portainer-tf-credentials-sealedsecret.yaml
    tofu-minio-credentials-sealedsecret.yaml
    kustomization.yaml

terraform/authentik/
  versions.tf       # required_providers + backend "s3" block (no creds — injected at runtime)
  variables.tf      # authentik_token, portainer_password, portainer_client_secret
  providers.tf      # provider "authentik" + provider "portainer" config
  data.tf           # data sources: flows (authorization, invalidation), user svollmin1
  groups.tf         # 4 admin groups + svollmin1 membership
  scope_mappings.tf # MinIO Policy Claim, Audiobookshelf Policy Claim (custom only)
  providers_oauth2.tf  # 7 OAuth2/OIDC providers (Grafana, MinIO, Headlamp, Harbor, Portainer, Jellyfin, Audiobookshelf)
  providers_proxy.tf   # vollminlab-forward-auth (domain-level proxy provider)
  applications.tf  # 7 OIDC apps + 12 forward-auth portal apps
  outpost.tf        # vollminlab-proxy outpost + provider attachment
  portainer.tf      # portainer_settings (authentication_method = 3, oauth_settings)
  imports.tf        # import {} blocks for all existing objects (IDs filled in from akshell query)
```

---

### PR 5a: tofu-controller + MinIO state bucket

---

### Task P5-1: Create PR 5a branch

**Files:** none

- [ ] **Step 1: Start from clean main**

```bash
git checkout main && git pull
git checkout -b feat/tofu-controller
```

---

### Task P5-2: Deploy tofu-controller

**Files:**
- Create: `clusters/vollminlab-cluster/tofu/namespace.yaml`
- Create: `clusters/vollminlab-cluster/tofu/kustomization.yaml`
- Create: `clusters/vollminlab-cluster/tofu/tofu-controller/app/kustomization.yaml`
- Create: `clusters/vollminlab-cluster/tofu/tofu-controller/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/tofu/tofu-controller/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/flux-system/repositories/tofu-controller-helmrepository.yaml`
- Create: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/tofu-kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`

- [ ] **Step 1: Create tofu namespace**

```yaml
# clusters/vollminlab-cluster/tofu/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
```

- [ ] **Step 2: Create tofu top-level kustomization**

```yaml
# clusters/vollminlab-cluster/tofu/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - tofu-controller/app
```

- [ ] **Step 3: Create tofu-controller HelmRepository**

```yaml
# clusters/vollminlab-cluster/flux-system/repositories/tofu-controller-helmrepository.yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: tofu-controller-repo
  namespace: flux-system
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  url: https://flux-iac.github.io/tofu-controller/
  interval: 12h
```

- [ ] **Step 4: Create tofu-controller ConfigMap**

```yaml
# clusters/vollminlab-cluster/tofu/tofu-controller/app/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: tofu-controller-values
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
data:
  values.yaml: |
    replicaCount: 1

    podLabels:
      app: tofu-controller
      env: production
      category: core

    resources:
      requests:
        cpu: 200m
        memory: 256Mi
      limits:
        cpu: 1000m
        memory: 512Mi

    runner:
      serviceAccount:
        create: true
        name: tf-runner
      podLabels:
        app: tf-runner
        env: production
        category: core
      resources:
        requests:
          cpu: 100m
          memory: 256Mi
        limits:
          cpu: 500m
          memory: 512Mi
```

- [ ] **Step 5: Create tofu-controller HelmRelease**

```yaml
# clusters/vollminlab-cluster/tofu/tofu-controller/app/helmrelease.yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: tofu-controller
  namespace: tofu
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 5m
  chart:
    spec:
      chart: tofu-controller
      version: "0.16.3"
      sourceRef:
        kind: HelmRepository
        name: tofu-controller-repo
        namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: tofu-controller-values
      valuesKey: values.yaml
```

- [ ] **Step 6: Create tofu-controller app kustomization**

```yaml
# clusters/vollminlab-cluster/tofu/tofu-controller/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - helmrelease.yaml
  - configmap.yaml
```

- [ ] **Step 7: Create Flux Kustomization CR for tofu**

```yaml
# clusters/vollminlab-cluster/flux-system/flux-kustomizations/tofu-kustomization.yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: tofu
  namespace: flux-system
  labels:
    app: tofu-controller
    env: production
    category: core
spec:
  interval: 10m
  path: ./clusters/vollminlab-cluster/tofu
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
  dependsOn:
    - name: sealed-secrets
```

- [ ] **Step 8: Update repositories Flux index (alphabetical)**

In `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`, add after `sonarr-ocirepository.yaml`:

```yaml
  - tofu-controller-helmrepository.yaml
```

- [ ] **Step 9: Update flux-kustomizations Flux index**

In `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`, add at end:

```yaml
  - tofu-kustomization.yaml
```

---

### Task P5-3: Add MinIO terraform-state resources

**Files:**
- Modify: `clusters/vollminlab-cluster/minio/minio/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/minio/minio/app/tofu-minio-user-sealedsecret.yaml`
- Modify: `clusters/vollminlab-cluster/minio/minio/app/kustomization.yaml`

- [ ] **Step 1: Generate a secret key for tofu-svc**

```bash
openssl rand -base64 32
```

Store in 1Password as **"MinIO tofu-svc Secret Key"** in the Homelab vault.

- [ ] **Step 2: Seal the tofu-svc user secret**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic tofu-minio-user -n minio \
  --from-literal=secretKey='<your-generated-secret-key>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/minio/minio/app/tofu-minio-user-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 3: Add tofu policy, user, and bucket to MinIO ConfigMap**

In `clusters/vollminlab-cluster/minio/minio/app/configmap.yaml`, append under the `policies:` list:

```yaml
      - name: tofu-state-policy
        statements:
          - effect: Allow
            resources:
              - "arn:aws:s3:::terraform-state"
              - "arn:aws:s3:::terraform-state/*"
            actions:
              - "s3:GetObject"
              - "s3:PutObject"
              - "s3:DeleteObject"
              - "s3:ListBucket"
              - "s3:GetBucketLocation"
```

Under the `users:` list, append:

```yaml
      - accessKey: tofu-svc
        existingSecret: tofu-minio-user
        existingSecretKey: secretKey
        policy: tofu-state-policy
```

Under the `buckets:` list, append:

```yaml
      - name: terraform-state
        policy: none
        purge: false
        versioning: false
        objectlocking: false
```

- [ ] **Step 4: Add SealedSecret to minio kustomization**

In `clusters/vollminlab-cluster/minio/minio/app/kustomization.yaml`, add:

```yaml
  - tofu-minio-user-sealedsecret.yaml
```

---

### Task P5-4: Commit and open PR 5a

- [ ] **Step 1: Stage files**

```bash
git add \
  clusters/vollminlab-cluster/tofu/ \
  clusters/vollminlab-cluster/flux-system/repositories/tofu-controller-helmrepository.yaml \
  clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/flux-kustomizations/tofu-kustomization.yaml \
  clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml \
  clusters/vollminlab-cluster/minio/minio/app/configmap.yaml \
  clusters/vollminlab-cluster/minio/minio/app/tofu-minio-user-sealedsecret.yaml \
  clusters/vollminlab-cluster/minio/minio/app/kustomization.yaml
```

- [ ] **Step 2: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(tofu): deploy tofu-controller + MinIO terraform-state bucket

Add flux-iac/tofu-controller v0.16.3 to new tofu namespace.
Add terraform-state MinIO bucket with scoped tofu-svc access key.
Phase 5a of 5 — no Terraform CR or .tf files yet.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/tofu-controller
gh pr create --title "feat(tofu): deploy tofu-controller + MinIO terraform-state bucket" --body "$(cat <<'EOF'
## Summary
- New `tofu` namespace with flux-iac/tofu-controller v0.16.3 HelmRelease
- MinIO: added `terraform-state` bucket, `tofu-state-policy`, `tofu-svc` user (scoped)
- Both Flux index files updated and alphabetized

Phase 5a — no Terraform code or Terraform CR yet. Safe to merge independently.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Wait for PR 5a to merge, then pull main**

```bash
# After merge:
git checkout main && git pull
```

---

### PR 5b: Terraform code + Flux wiring

---

### Task P5-5: Create PR 5b branch

- [ ] **Step 1: Create branch from clean main**

```bash
git checkout -b feat/authentik-terraform-iac
```

---

### Task P5-6: Manual — export Authentik config and gather resource IDs

**This task is manual. Do not skip it — IDs from this step fill in `imports.tf`.**

- [ ] **Step 1: Export Authentik config as reference**

Navigate to `https://authentik.vollminlab.com` → Admin → System → Export. Save the exported YAML as a local reference file (do NOT commit it — it may contain hashed secrets). This is your source of truth for verifying resource names and slugs.

- [ ] **Step 2: Create a long-lived API token in Authentik**

In Authentik Admin → Directory → Tokens → Create:
- Identifier: `terraform-provider`
- User: `akadmin` (or your admin user)
- Intent: `API`
- Expiry: blank (never expires)

Copy the token value. Store in 1Password as **"Authentik Terraform API Token"** in the Homelab vault.

- [ ] **Step 3: Query Authentik for all resource IDs needed for imports.tf**

Run the following akshell commands to collect the numeric and UUID IDs. Record all output — you will paste these into `imports.tf`.

```bash
AUTHENTIK_POD=$(kubectl get pods -n authentik -l app.kubernetes.io/name=authentik,app.kubernetes.io/component=server -o name | head -1 | cut -d/ -f2)

# OAuth2 provider IDs (numeric)
kubectl exec -n authentik $AUTHENTIK_POD -- ak shell -c "
from authentik.providers.oauth2.models import OAuth2Provider
for p in OAuth2Provider.objects.all().order_by('name'):
    print(f'oauth2/{p.pk}: {p.name}  client_id={p.client_id}')
"

# Proxy provider IDs (numeric)
kubectl exec -n authentik $AUTHENTIK_POD -- ak shell -c "
from authentik.providers.proxy.models import ProxyProvider
for p in ProxyProvider.objects.all().order_by('name'):
    print(f'proxy/{p.pk}: {p.name}')
"

# Group UUIDs
kubectl exec -n authentik $AUTHENTIK_POD -- ak shell -c "
from authentik.core.models import Group
for g in Group.objects.all().order_by('name'):
    print(f'group/{g.pk}: {g.name}')
"

# Application slugs (ID = slug for authentik_application)
kubectl exec -n authentik $AUTHENTIK_POD -- ak shell -c "
from authentik.core.models import Application
for a in Application.objects.all().order_by('slug'):
    print(f'app/{a.slug}: {a.name}  provider_id={a.provider_id}')
"

# Outpost UUID
kubectl exec -n authentik $AUTHENTIK_POD -- ak shell -c "
from authentik.outposts.models import Outpost
for o in Outpost.objects.all():
    print(f'outpost/{o.pk}: {o.name}')
"

# Scope mapping UUIDs (custom only — not built-in managed ones)
kubectl exec -n authentik $AUTHENTIK_POD -- ak shell -c "
from authentik.core.models import PropertyMapping
for m in PropertyMapping.objects.filter(managed__isnull=True).order_by('name'):
    print(f'mapping/{m.pk}: {m.name}')
"
```

Record all output before proceeding to the next task.

---

### Task P5-7: Create SealedSecrets in tofu namespace

**Files:**
- Create: `clusters/vollminlab-cluster/tofu/terraform-authentik/app/authentik-tf-credentials-sealedsecret.yaml`
- Create: `clusters/vollminlab-cluster/tofu/terraform-authentik/app/portainer-tf-credentials-sealedsecret.yaml`
- Create: `clusters/vollminlab-cluster/tofu/terraform-authentik/app/tofu-minio-credentials-sealedsecret.yaml`

These three secrets are in the `tofu` namespace and feed into `spec.varsFrom` / `spec.backendConfigsFrom` on the Terraform CR.

- [ ] **Step 1: Fetch sealing cert**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem
```

- [ ] **Step 2: Seal Authentik API token**

```bash
kubectl create secret generic authentik-tf-credentials -n tofu \
  --from-literal=authentik_token='<token-from-task-p5-6-step-2>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/terraform-authentik/app/authentik-tf-credentials-sealedsecret.yaml
```

- [ ] **Step 3: Seal Portainer admin password + Portainer OAuth client secret**

Look up the Portainer admin password from 1Password (**"Portainer Admin Password"**) and the Portainer OAuth client secret from the existing `portainer-oauth-credentials` SealedSecret (read its unsealed value from 1Password or from Authentik Admin → Providers → Portainer → Client Secret).

```bash
kubectl create secret generic portainer-tf-credentials -n tofu \
  --from-literal=portainer_password='<portainer-admin-password>' \
  --from-literal=portainer_client_secret='<portainer-oauth-client-secret>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/terraform-authentik/app/portainer-tf-credentials-sealedsecret.yaml
```

- [ ] **Step 4: Seal MinIO backend credentials**

Use the `tofu-svc` access key (`tofu-svc`) and the secret key from 1Password (**"MinIO tofu-svc Secret Key"**):

```bash
kubectl create secret generic tofu-minio-credentials -n tofu \
  --from-literal=access_key='tofu-svc' \
  --from-literal=secret_key='<secret-key-from-1password>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/tofu/terraform-authentik/app/tofu-minio-credentials-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

---

### Task P5-8: Create terraform/authentik/versions.tf

**Files:**
- Create: `terraform/authentik/versions.tf`

- [ ] **Step 1: Write versions.tf**

The backend block is declared here but credentials are intentionally omitted — tofu-controller injects them via `spec.backendConfigsFrom` at runtime.

```hcl
# terraform/authentik/versions.tf
terraform {
  required_version = ">= 1.5"

  required_providers {
    authentik = {
      source  = "goauthentik/authentik"
      version = "2026.2.0"
    }
    portainer = {
      source  = "portainer/portainer"
      version = "~> 1.29"
    }
  }

  backend "s3" {
    bucket = "terraform-state"
    key    = "authentik/terraform.tfstate"
    region = "us-east-1"

    endpoint                    = "http://minio.minio.svc.cluster.local:9000"
    force_path_style            = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    # access_key and secret_key injected by tofu-controller via backendConfigsFrom
  }
}
```

---

### Task P5-9: Create terraform/authentik/variables.tf

**Files:**
- Create: `terraform/authentik/variables.tf`

- [ ] **Step 1: Write variables.tf**

```hcl
# terraform/authentik/variables.tf
variable "authentik_token" {
  description = "Authentik API token for the Terraform provider"
  type        = string
  sensitive   = true
}

variable "portainer_password" {
  description = "Portainer admin password"
  type        = string
  sensitive   = true
}

variable "portainer_client_secret" {
  description = "Authentik OAuth2 client_secret for the Portainer provider"
  type        = string
  sensitive   = true
}
```

---

### Task P5-10: Create terraform/authentik/providers.tf

**Files:**
- Create: `terraform/authentik/providers.tf`

- [ ] **Step 1: Write providers.tf**

Authentik is accessed via its internal cluster service (avoids TLS cert validation on the Helm-managed service). Portainer is accessed on its internal service port 9000.

```hcl
# terraform/authentik/providers.tf
provider "authentik" {
  url   = "http://authentik-server.authentik.svc.cluster.local"
  token = var.authentik_token
}

provider "portainer" {
  endpoint = "http://portainer.portainer.svc.cluster.local:9000"
  username = "admin"
  password = var.portainer_password
}
```

- [ ] **Step 2: Verify Portainer internal service name and port**

```bash
kubectl get svc -n portainer
```

Confirm the service name and port match what's in providers.tf. Adjust if needed.

---

### Task P5-11: Create terraform/authentik/data.tf

**Files:**
- Create: `terraform/authentik/data.tf`

- [ ] **Step 1: Write data.tf**

```hcl
# terraform/authentik/data.tf

# Built-in authorization flow (implicit consent — used for all providers on this cluster)
data "authentik_flow" "default_authorization_implicit" {
  slug = "default-provider-authorization-implicit-consent"
}

# Built-in invalidation flow (required field on all providers)
data "authentik_flow" "default_invalidation" {
  slug = "default-provider-invalidation-flow"
}

# Admin user (for group membership)
data "authentik_user" "svollmin1" {
  username = "svollmin1"
}
```

---

### Task P5-12: Create terraform/authentik/groups.tf

**Files:**
- Create: `terraform/authentik/groups.tf`

- [ ] **Step 1: Write groups.tf**

```hcl
# terraform/authentik/groups.tf
resource "authentik_group" "grafana_admins" {
  name  = "Grafana Admins"
  users = [data.authentik_user.svollmin1.id]
}

resource "authentik_group" "minio_admins" {
  name  = "MinIO Admins"
  users = [data.authentik_user.svollmin1.id]
}

resource "authentik_group" "audiobookshelf_admins" {
  name  = "Audiobookshelf Admins"
  users = [data.authentik_user.svollmin1.id]
}

resource "authentik_group" "headlamp_admins" {
  name  = "Headlamp Admins"
  users = [data.authentik_user.svollmin1.id]
}
```

**Note on `users` field type:** The goauthentik provider schema declares `users` as List of Number (integer PKs). If the plan fails with a type error on `users`, get the integer PK via akshell and hardcode it:

```bash
AUTHENTIK_POD=$(kubectl get pods -n authentik -l app.kubernetes.io/name=authentik,app.kubernetes.io/component=server -o name | head -1 | cut -d/ -f2)
kubectl exec -n authentik $AUTHENTIK_POD -- ak shell -c "
from authentik.core.models import User
u = User.objects.get(username='svollmin1')
print(f'pk={u.pk}')
"
```

Then replace `data.authentik_user.svollmin1.id` with the integer literal in all four group resources.

---

### Task P5-13: Create terraform/authentik/scope_mappings.tf

**Files:**
- Create: `terraform/authentik/scope_mappings.tf`

These are the two custom scope mappings created via akshell during Phase 3. Built-in Authentik scope mappings (including the `email_verified` fix) are NOT imported — modifying built-in managed mappings risks conflicts on Authentik upgrades.

- [ ] **Step 1: Write scope_mappings.tf**

```hcl
# terraform/authentik/scope_mappings.tf

resource "authentik_property_mapping_provider_scope" "minio_policy_claim" {
  name       = "MinIO Policy Claim"
  scope_name = "minio_policy"
  expression = <<-EOT
    if ak_is_group_member(request.user, name="MinIO Admins"):
        return {"policy": "consoleAdmin"}
    return None
  EOT
}

resource "authentik_property_mapping_provider_scope" "audiobookshelf_groups_claim" {
  name       = "Audiobookshelf Policy Claim"
  scope_name = "audiobookshelf_groups"
  expression = <<-EOT
    if ak_is_group_member(request.user, name="Audiobookshelf Admins"):
        return {"groups": ["admin"]}
    return None
  EOT
}
```

---

### Task P5-14: Create terraform/authentik/providers_oauth2.tf

**Files:**
- Create: `terraform/authentik/providers_oauth2.tf`

Client IDs were collected in Task P5-6 Step 3. Replace the placeholders with the actual values. `lifecycle { ignore_changes = [client_secret] }` prevents Terraform from touching the existing secret — it stays in the SealedSecrets already deployed.

- [ ] **Step 1: Write providers_oauth2.tf**

```hcl
# terraform/authentik/providers_oauth2.tf

resource "authentik_provider_oauth2" "grafana" {
  name               = "Grafana"
  client_id          = "<grafana-client-id>"   # from task P5-6 Step 3
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_invalidation.id
  redirect_uris      = ["https://grafana.vollminlab.com/login/generic_oauth"]
  property_mappings  = []
  lifecycle {
    ignore_changes = [client_secret]
  }
}

resource "authentik_provider_oauth2" "minio" {
  name               = "MinIO"
  client_id          = "<minio-client-id>"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_invalidation.id
  redirect_uris      = ["https://minio.vollminlab.com/oauth_callback"]
  property_mappings  = [authentik_property_mapping_provider_scope.minio_policy_claim.id]
  lifecycle {
    ignore_changes = [client_secret]
  }
}

resource "authentik_provider_oauth2" "headlamp" {
  name               = "Headlamp"
  client_id          = "<headlamp-client-id>"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_invalidation.id
  redirect_uris      = ["https://headlamp.vollminlab.com/oidc-callback"]
  property_mappings  = []
  lifecycle {
    ignore_changes = [client_secret]
  }
}

resource "authentik_provider_oauth2" "harbor" {
  name               = "Harbor"
  client_id          = "<harbor-client-id>"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_invalidation.id
  redirect_uris      = ["https://harbor.vollminlab.com/c/oidc/callback"]
  property_mappings  = []
  lifecycle {
    ignore_changes = [client_secret]
  }
}

resource "authentik_provider_oauth2" "portainer" {
  name               = "Portainer"
  client_id          = "<portainer-client-id>"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_invalidation.id
  redirect_uris      = ["https://portainer.vollminlab.com"]
  property_mappings  = []
  lifecycle {
    ignore_changes = [client_secret]
  }
}

resource "authentik_provider_oauth2" "jellyfin" {
  name               = "Jellyfin"
  client_id          = "<jellyfin-client-id>"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_invalidation.id
  redirect_uris      = ["https://jellyfin.vollminlab.com/sso/OID/redirect/authentik"]
  property_mappings  = []
  lifecycle {
    ignore_changes = [client_secret]
  }
}

resource "authentik_provider_oauth2" "audiobookshelf" {
  name               = "Audiobookshelf"
  client_id          = "<audiobookshelf-client-id>"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_invalidation.id
  redirect_uris      = ["https://audiobookshelf.vollminlab.com/auth/openid/callback"]
  property_mappings  = [authentik_property_mapping_provider_scope.audiobookshelf_groups_claim.id]
  lifecycle {
    ignore_changes = [client_secret]
  }
}
```

---

### Task P5-15: Create terraform/authentik/providers_proxy.tf

**Files:**
- Create: `terraform/authentik/providers_proxy.tf`

- [ ] **Step 1: Write providers_proxy.tf**

```hcl
# terraform/authentik/providers_proxy.tf

resource "authentik_provider_proxy" "vollminlab_forward_auth" {
  name               = "vollminlab-forward-auth"
  external_host      = "https://authentik.vollminlab.com"
  authorization_flow = data.authentik_flow.default_authorization_implicit.id
  invalidation_flow  = data.authentik_flow.default_invalidation.id
  mode               = "forward_domain"
  cookie_domain      = "vollminlab.com"
}
```

---

### Task P5-16: Create terraform/authentik/applications.tf

**Files:**
- Create: `terraform/authentik/applications.tf`

- [ ] **Step 1: Write applications.tf**

OIDC applications reference their provider by numeric ID. Forward-auth portal applications have no `protocol_provider`.

```hcl
# terraform/authentik/applications.tf

# --- OIDC applications ---

resource "authentik_application" "grafana" {
  name              = "Grafana"
  slug              = "grafana"
  protocol_provider = authentik_provider_oauth2.grafana.id
  meta_launch_url   = "https://grafana.vollminlab.com"
}

resource "authentik_application" "minio" {
  name              = "MinIO"
  slug              = "minio"
  protocol_provider = authentik_provider_oauth2.minio.id
  meta_launch_url   = "https://minio.vollminlab.com"
}

resource "authentik_application" "headlamp" {
  name              = "Headlamp"
  slug              = "headlamp"
  protocol_provider = authentik_provider_oauth2.headlamp.id
  meta_launch_url   = "https://headlamp.vollminlab.com"
}

resource "authentik_application" "harbor" {
  name              = "Harbor"
  slug              = "harbor"
  protocol_provider = authentik_provider_oauth2.harbor.id
  meta_launch_url   = "https://harbor.vollminlab.com"
}

resource "authentik_application" "portainer" {
  name              = "Portainer"
  slug              = "portainer"
  protocol_provider = authentik_provider_oauth2.portainer.id
  meta_launch_url   = "https://portainer.vollminlab.com"
}

resource "authentik_application" "jellyfin" {
  name              = "Jellyfin"
  slug              = "jellyfin"
  protocol_provider = authentik_provider_oauth2.jellyfin.id
  meta_launch_url   = "https://jellyfin.vollminlab.com"
}

resource "authentik_application" "audiobookshelf" {
  name              = "Audiobookshelf"
  slug              = "audiobookshelf"
  protocol_provider = authentik_provider_oauth2.audiobookshelf.id
  meta_launch_url   = "https://audiobookshelf.vollminlab.com"
}

# --- Forward-auth domain application (wired to the proxy outpost) ---

resource "authentik_application" "forward_auth" {
  name              = "Forward Auth"
  slug              = "forward-auth"
  protocol_provider = authentik_provider_proxy.vollminlab_forward_auth.id
  meta_launch_url   = "https://authentik.vollminlab.com"
}

# --- Forward-auth portal applications (display in Authentik portal only, no provider) ---

resource "authentik_application" "homepage" {
  name            = "Homepage"
  slug            = "homepage"
  meta_launch_url = "https://homepage.vollminlab.com"
}

resource "authentik_application" "longhorn" {
  name            = "Longhorn"
  slug            = "longhorn"
  meta_launch_url = "https://longhorn.vollminlab.com"
}

resource "authentik_application" "radarr" {
  name            = "Radarr"
  slug            = "radarr"
  meta_launch_url = "https://radarr.vollminlab.com"
}

resource "authentik_application" "sonarr" {
  name            = "Sonarr"
  slug            = "sonarr"
  meta_launch_url = "https://sonarr.vollminlab.com"
}

resource "authentik_application" "bazarr" {
  name            = "Bazarr"
  slug            = "bazarr"
  meta_launch_url = "https://bazarr.vollminlab.com"
}

resource "authentik_application" "prowlarr" {
  name            = "Prowlarr"
  slug            = "prowlarr"
  meta_launch_url = "https://prowlarr.vollminlab.com"
}

resource "authentik_application" "sabnzbd" {
  name            = "SABnzbd"
  slug            = "sabnzbd"
  meta_launch_url = "https://sabnzbd.vollminlab.com"
}

resource "authentik_application" "shlink" {
  name            = "Shlink"
  slug            = "shlink"
  meta_launch_url = "https://shlink.vollminlab.com"
}

resource "authentik_application" "policy_reporter" {
  name            = "Policy Reporter"
  slug            = "policy-reporter"
  meta_launch_url = "https://policy-reporter.vollminlab.com"
}

resource "authentik_application" "jellystat" {
  name            = "Jellystat"
  slug            = "jellystat"
  meta_launch_url = "https://jellystat.vollminlab.com"
}

resource "authentik_application" "seerr" {
  name            = "Seerr"
  slug            = "seerr"
  meta_launch_url = "https://seerr.vollminlab.com"
}
```

---

### Task P5-17: Create terraform/authentik/outpost.tf

**Files:**
- Create: `terraform/authentik/outpost.tf`

- [ ] **Step 1: Write outpost.tf**

The outpost is deployed manually (not via Authentik's Kubernetes integration), so `config` only needs the `authentik_host` value. Use `lifecycle { ignore_changes = [config] }` to prevent Terraform from drifting on Authentik-internal outpost state fields it appends automatically.

```hcl
# terraform/authentik/outpost.tf

resource "authentik_outpost" "vollminlab_proxy" {
  name = "vollminlab-proxy"
  type = "proxy"
  protocol_providers = [
    authentik_provider_proxy.vollminlab_forward_auth.id
  ]
  config = jsonencode({
    authentik_host          = "https://authentik.vollminlab.com"
    authentik_host_insecure = false
    log_level               = "info"
  })
  lifecycle {
    ignore_changes = [config]
  }
}
```

---

### Task P5-18: Create terraform/authentik/portainer.tf

**Files:**
- Create: `terraform/authentik/portainer.tf`

- [ ] **Step 1: Write portainer.tf**

```hcl
# terraform/authentik/portainer.tf

resource "portainer_settings" "oauth" {
  authentication_method = 3  # 3 = OAuth/OIDC

  oauth_settings {
    client_id         = "<portainer-client-id>"   # same value as providers_oauth2.tf portainer block
    client_secret     = var.portainer_client_secret
    authorization_uri = "https://authentik.vollminlab.com/application/o/authorize/"
    access_token_uri  = "https://authentik.vollminlab.com/application/o/token/"
    redirect_uri      = "https://portainer.vollminlab.com"
    resource_uri      = "https://authentik.vollminlab.com/application/o/userinfo/"
    user_identifier   = "preferred_username"
    scopes            = "openid profile email"
    sso               = true
  }
}
```

---

### Task P5-19: Create terraform/authentik/imports.tf

**Files:**
- Create: `terraform/authentik/imports.tf`

Use IDs collected in Task P5-6 Step 3. The placeholders below must all be replaced before committing.

- [ ] **Step 1: Write imports.tf with IDs from akshell output**

```hcl
# terraform/authentik/imports.tf
# IDs collected via akshell on <DATE>. Remove import blocks after first successful apply.

# Groups (UUIDs from akshell group query)
import { to = authentik_group.grafana_admins;        id = "<grafana-admins-uuid>" }
import { to = authentik_group.minio_admins;          id = "<minio-admins-uuid>" }
import { to = authentik_group.audiobookshelf_admins; id = "<audiobookshelf-admins-uuid>" }
import { to = authentik_group.headlamp_admins;       id = "<headlamp-admins-uuid>" }

# Scope mappings (UUIDs from akshell mapping query)
import { to = authentik_property_mapping_provider_scope.minio_policy_claim;         id = "<minio-policy-claim-uuid>" }
import { to = authentik_property_mapping_provider_scope.audiobookshelf_groups_claim; id = "<audiobookshelf-groups-claim-uuid>" }

# OAuth2 providers (numeric PKs from akshell oauth2 query)
import { to = authentik_provider_oauth2.grafana;       id = "<grafana-provider-pk>" }
import { to = authentik_provider_oauth2.minio;         id = "<minio-provider-pk>" }
import { to = authentik_provider_oauth2.headlamp;      id = "<headlamp-provider-pk>" }
import { to = authentik_provider_oauth2.harbor;        id = "<harbor-provider-pk>" }
import { to = authentik_provider_oauth2.portainer;     id = "<portainer-provider-pk>" }
import { to = authentik_provider_oauth2.jellyfin;      id = "<jellyfin-provider-pk>" }
import { to = authentik_provider_oauth2.audiobookshelf; id = "<audiobookshelf-provider-pk>" }

# Proxy provider (numeric PK from akshell proxy query)
import { to = authentik_provider_proxy.vollminlab_forward_auth; id = "<forward-auth-provider-pk>" }

# Applications (ID = slug)
import { to = authentik_application.grafana;         id = "grafana" }
import { to = authentik_application.minio;           id = "minio" }
import { to = authentik_application.headlamp;        id = "headlamp" }
import { to = authentik_application.harbor;          id = "harbor" }
import { to = authentik_application.portainer;       id = "portainer" }
import { to = authentik_application.jellyfin;        id = "jellyfin" }
import { to = authentik_application.audiobookshelf;  id = "audiobookshelf" }
import { to = authentik_application.forward_auth;    id = "forward-auth" }
import { to = authentik_application.homepage;        id = "homepage" }
import { to = authentik_application.longhorn;        id = "longhorn" }
import { to = authentik_application.radarr;          id = "radarr" }
import { to = authentik_application.sonarr;          id = "sonarr" }
import { to = authentik_application.bazarr;          id = "bazarr" }
import { to = authentik_application.prowlarr;        id = "prowlarr" }
import { to = authentik_application.sabnzbd;         id = "sabnzbd" }
import { to = authentik_application.shlink;          id = "shlink" }
import { to = authentik_application.policy_reporter; id = "policy-reporter" }
import { to = authentik_application.jellystat;       id = "jellystat" }
import { to = authentik_application.seerr;           id = "seerr" }

# Outpost (UUID from akshell outpost query)
import { to = authentik_outpost.vollminlab_proxy; id = "<vollminlab-proxy-outpost-uuid>" }

# Portainer settings (singleton — import ID is typically "1"; verify with:
#   curl -s http://portainer.portainer.svc.cluster.local:9000/api/settings | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Id',1))"
# If the endpoint returns no Id field, use "1" as the import ID.)
import { to = portainer_settings.oauth; id = "1" }
```

- [ ] **Step 2: Verify no placeholder strings remain**

```bash
grep "<" terraform/authentik/imports.tf
```

Expected output: nothing. If any `<...>` placeholders remain, fill them in before proceeding.

---

### Task P5-20: Create Terraform CR and cluster wiring

**Files:**
- Create: `clusters/vollminlab-cluster/tofu/terraform-authentik/app/terraform-cr.yaml`
- Create: `clusters/vollminlab-cluster/tofu/terraform-authentik/app/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/tofu/kustomization.yaml`

- [ ] **Step 1: Write the Terraform CR**

```yaml
# clusters/vollminlab-cluster/tofu/terraform-authentik/app/terraform-cr.yaml
apiVersion: infra.contrib.fluxcd.io/v1alpha2
kind: Terraform
metadata:
  name: authentik
  namespace: tofu
  labels:
    app: tofu-authentik
    env: production
    category: security
spec:
  interval: 5m
  approvePlan: "auto"
  path: ./terraform/authentik
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  backendConfig:
    customConfiguration: |
      backend "s3" {
        bucket                      = "terraform-state"
        key                         = "authentik/terraform.tfstate"
        region                      = "us-east-1"
        endpoint                    = "http://minio.minio.svc.cluster.local:9000"
        force_path_style            = true
        skip_credentials_validation = true
        skip_metadata_api_check     = true
        skip_region_validation      = true
        skip_requesting_account_id  = true
      }
  backendConfigsFrom:
    - kind: Secret
      name: tofu-minio-credentials
      keys:
        - access_key
        - secret_key
      optional: false
  varsFrom:
    - kind: Secret
      name: authentik-tf-credentials
      varsKeys:
        - authentik_token
    - kind: Secret
      name: portainer-tf-credentials
      varsKeys:
        - portainer_password
        - portainer_client_secret
  runnerPodTemplate:
    spec:
      containers:
        - name: runner
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
```

- [ ] **Step 2: Create terraform-authentik app kustomization**

```yaml
# clusters/vollminlab-cluster/tofu/terraform-authentik/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - terraform-cr.yaml
  - authentik-tf-credentials-sealedsecret.yaml
  - portainer-tf-credentials-sealedsecret.yaml
  - tofu-minio-credentials-sealedsecret.yaml
```

- [ ] **Step 3: Add terraform-authentik to tofu top-level kustomization**

Edit `clusters/vollminlab-cluster/tofu/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - tofu-controller/app
  - terraform-authentik/app
```

---

### Task P5-21: Commit and open PR 5b

- [ ] **Step 1: Stage all PR 5b files**

```bash
git add \
  terraform/authentik/ \
  clusters/vollminlab-cluster/tofu/terraform-authentik/ \
  clusters/vollminlab-cluster/tofu/kustomization.yaml
```

- [ ] **Step 2: Verify no plaintext secrets in staged files**

```bash
git diff --staged | grep -E "(token|password|secret|key)" | grep -v "sealed\|encryptedData\|SealedSecret\|secretKeyRef\|secretKey:\|gitleaks:allow\|<.*>"
```

Expected: no output. If anything appears, review it carefully — secrets must be in SealedSecrets only.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(tofu): Terraform IaC for all Authentik config

Add goauthentik/authentik + portainer Terraform files under terraform/authentik/.
Covers: 4 groups, 2 scope mappings, 7 OAuth2 providers, 1 proxy provider,
19 applications, 1 outpost, Portainer OAuth settings.
Import blocks ensure first apply is idempotent against live Authentik state.
Wire Flux Terraform CR — tofu-controller reconciles going forward.

Phase 5b of 5 — completes Authentik IaC.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin feat/authentik-terraform-iac
gh pr create --title "feat(tofu): Terraform IaC for all Authentik config" --body "$(cat <<'EOF'
## Summary
- `terraform/authentik/` — full OpenTofu code for Authentik via goauthentik provider v2026.2.0
- `terraform/authentik/imports.tf` — native `import {}` blocks for all 30+ existing Authentik objects
- `portainer.tf` — Portainer OAuth settings via portainer/portainer provider v1.29
- Flux `Terraform` CR wired to tofu-controller; `approvePlan: auto`; state in MinIO `terraform-state`
- All credentials injected via `spec.backendConfigsFrom` / `spec.varsFrom` from SealedSecrets in `tofu` ns

**Critical before merge:** verify that `terraform plan` (visible in the Terraform CR status) shows ONLY `import` and `no-change` operations — zero `create` or `destroy` actions. A `create` means an import block is missing.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

### Task P5-22: Monitor first reconciliation and verify clean plan

- [ ] **Step 1: Watch the Terraform CR status after merge**

```bash
kubectl get terraform authentik -n tofu -w
```

Expected progression: `Initializing` → `Importing` → `Planning` → `Applying` → `Applied`

- [ ] **Step 2: Check the plan output for unexpected operations**

```bash
kubectl describe terraform authentik -n tofu | grep -A 40 "Plan:"
```

Acceptable: `import`, `no-op`. **Not acceptable:** `create`, `destroy`, `update` (unless the diff is a known attribute you deliberately want to reconcile). If any unexpected creates appear, stop and investigate before the apply completes.

- [ ] **Step 3: If plan shows unexpected creates — remediate before applying**

```bash
# Suspend the Terraform CR to prevent auto-apply
kubectl patch terraform authentik -n tofu --type=merge -p '{"spec":{"suspend":true}}'
```

Then add the missing import block to `imports.tf`, push, and unsuspend:

```bash
kubectl patch terraform authentik -n tofu --type=merge -p '{"spec":{"suspend":false}}'
```

- [ ] **Step 4: Verify Authentik is still functional after first apply**

```bash
# Check Authentik pods are still running
kubectl get pods -n authentik

# Test SSO still works
curl -s -o /dev/null -w "%{http_code}" https://authentik.vollminlab.com/api/v3/root/config/
```

Expected: `200`

- [ ] **Step 5: Confirm ongoing reconciliation is clean**

Wait for the next reconciliation interval (5m) and confirm the Terraform CR status stays `Applied` with no new plan operations.

---

## Post-deployment checklist

- [ ] All Flux kustomizations show `Ready=True`: `flux get kustomizations -A`
- [ ] All HelmReleases show `Ready=True`: `flux get helmreleases -A`
- [ ] Authentik login page reachable externally at `https://authentik.vollminlab.com`
- [ ] Jellyfin SSO works from outside the LAN via cloudflared
- [ ] Jellyseerr accessible at `https://jellyseerr.vollminlab.com` with OIDC login
- [ ] Grafana login redirects to Authentik
- [ ] Longhorn UI requires Authentik session (forward-auth working)
- [ ] Arr stack apps (Radarr, Sonarr, etc.) require Authentik session, no local login prompt
- [ ] Policy violations report clean: `kubectl get policyreport -A`
- [ ] Update roadmap: mark Phase 3.1 Authentik as `done`
- [ ] Update roadmap: mark Phase 3.5 Tautulli as `done` (already complete per earlier conversation)
