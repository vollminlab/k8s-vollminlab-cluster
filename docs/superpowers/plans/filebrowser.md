# FileBrowser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy FileBrowser in `mediastack` as a general-purpose file drop, gated by Authentik forward-auth with proxy auth (no second login), writing to a dedicated TrueNAS SMB share accessible from Windows at `\\smb.vollminlab.com\FileBrowser\`.

**Architecture:** A raw Deployment (no Helm) runs `filebrowser/filebrowser:v2.63.3` with two mounts — a Longhorn PVC for the SQLite database and a new SMB PVC for file storage. nginx injects `X-authentik-username` via Authentik forward-auth; FileBrowser trusts that header and maps it to a local user account. A dedicated Cloudflare Tunnel routes external traffic through nginx (unlike OIDC apps that bypass nginx) so the forward-auth header injection is always in the path.

**Tech Stack:** FileBrowser v2.63.3, Cloudflare Tunnel (cloudflared:2026.3.0), Authentik forward-auth via nginx, SMB CSI driver, Longhorn, OpenTofu (tofu-controller auto-applies on merge)

---

## Prerequisites — fill in before starting

The friend's Authentik account details are needed for Task 4. Decide and record:

- `<friend-username>` — Authentik username (e.g. `jsmith`) — must match exactly what FileBrowser will see as `X-authentik-username`
- `<friend-name>` — Display name (e.g. `Jane Smith`)
- `<friend-email>` — Email address

---

## Task 1: Create the feature branch

**Files:** None (git setup)

- [ ] Start from clean main:

  ```bash
  git checkout main && git pull
  ```

- [ ] Create the branch:

  ```bash
  git checkout -b feat/filebrowser
  ```

All commits in Tasks 2–9 go on this branch.

---

## Task 2: TrueNAS — create the SMB share (manual)

**Files:** None (manual TrueNAS UI steps)

- [ ] Log into TrueNAS at `https://truenas.vollminlab.com`
- [ ] Navigate to **Storage → Datasets** → select the same pool as `audiobooks`, `movies`, etc. (pool_0)
- [ ] Create a new dataset named `FileBrowser` with share type **SMB**
- [ ] Navigate to **Shares → Windows (SMB)** → verify the `FileBrowser` share was auto-created, or create it manually pointing to the new dataset
- [ ] Inside the dataset, create a subfolder named `Audiobooks`
- [ ] Set dataset permissions: owner uid=568, gid=568 (matches the mount options used by all other SMB PVs in the cluster)
- [ ] Confirm the share is accessible: from a Windows machine, browse to `\\smb.vollminlab.com\FileBrowser` and verify `Audiobooks` is visible

---

## Task 3: Cloudflare Tunnel — tofu

**Files:**
- Modify: `terraform/cloudflare/tunnels.tf`
- Modify: `terraform/cloudflare/dns.tf`

These changes are committed to `feat/filebrowser`; tofu-controller applies them automatically after the PR merges.

- [ ] **Add the tunnel and config to `terraform/cloudflare/tunnels.tf`**

  Append after the `vollminlab_jellyfin` config block:

  ```hcl
  resource "cloudflare_zero_trust_tunnel_cloudflared" "vollminlab_filebrowser" {
    account_id = var.cloudflare_account_id
    name       = "vollminlab-FileBrowser"
    lifecycle { ignore_changes = all }
  }

  resource "cloudflare_zero_trust_tunnel_cloudflared_config" "vollminlab_filebrowser" {
    account_id = var.cloudflare_account_id
    tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.vollminlab_filebrowser.id
    lifecycle { ignore_changes = all }

    config = {
      ingress_rule = [
        {
          hostname = "filebrowser.vollminlab.com"
          service  = "http://ingress-nginx-controller.ingress-nginx.svc.cluster.local:80"
        },
        {
          service = "http_status:404"
        },
      ]
    }
  }
  ```

  > Unlike audiobookshelf/jellyfin (OIDC), this tunnel routes through nginx so Authentik forward-auth can inject `X-authentik-username`. Bypassing nginx would bypass auth entirely.

- [ ] **Add the DNS record to `terraform/cloudflare/dns.tf`**

  Append after the `jellyfin` record:

  ```hcl
  resource "cloudflare_dns_record" "filebrowser" {
    zone_id = var.cloudflare_zone_id
    name    = "filebrowser.vollminlab.com"
    type    = "CNAME"
    content = "${cloudflare_zero_trust_tunnel_cloudflared.vollminlab_filebrowser.id}.cfargotunnel.com"
    proxied = true
    ttl     = 1
  }
  ```

- [ ] **Commit**:

  ```bash
  git add terraform/cloudflare/tunnels.tf terraform/cloudflare/dns.tf
  git commit -m "feat(cloudflare): add FileBrowser tunnel and DNS record"
  ```

- [ ] **After merge:** verify tofu-controller applied:

  ```bash
  kubectl get terraform cloudflare-config -n tofu \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].message}'
  # Expected: Applied successfully
  ```

- [ ] **Get the tunnel token** (needed for Task 8):
  1. Go to Cloudflare Zero Trust → **Networks → Tunnels**
  2. Find `vollminlab-FileBrowser` → three-dot menu → **Configure**
  3. Copy the tunnel token from the Docker run command shown under "Install connector"
  4. Store temporarily in 1Password: item `"FileBrowser Cloudflare Tunnel Token"`, vault `Homelab`

---

## Task 4: Authentik — tofu

**Files:**
- Modify: `terraform/authentik/applications.tf`
- Modify: `terraform/authentik/users.tf`

- [ ] **Add the FileBrowser application to `terraform/authentik/applications.tf`** (keep alphabetical order):

  ```hcl
  resource "authentik_application" "filebrowser" {
    name            = "FileBrowser"
    slug            = "filebrowser"
    meta_launch_url = "https://filebrowser.vollminlab.com"
    open_in_new_tab = false
  }
  ```

- [ ] **Add the friend user to `terraform/authentik/users.tf`** (replace placeholders with real values from prerequisites):

  ```hcl
  resource "authentik_user" "<friend-username>" {
    username  = "<friend-username>"
    name      = "<friend-name>"
    email     = "<friend-email>"
    is_active = true

    lifecycle {
      ignore_changes = [password, groups]
    }
  }
  ```

  > **Security note:** Without `authentik_policy_binding` on other apps, a user with a valid Authentik session can reach all forward-auth services (Radarr, SABnzbd, etc.). For a trusted contact this is acceptable. To restrict the friend to FileBrowser only, add `authentik_policy_binding` to every other forward-auth app — a separate follow-up task.

- [ ] **Commit**:

  ```bash
  git add terraform/authentik/applications.tf terraform/authentik/users.tf
  git commit -m "feat(authentik-tf): add FileBrowser app and friend user"
  ```

- [ ] **After merge:** verify tofu applied:

  ```bash
  kubectl get terraform authentik-config -n tofu \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].message}'
  # Expected: Applied successfully
  ```

- [ ] **Send the friend a password reset link**: in Authentik admin, go to **Directory → Users** → find the new user → **Send recovery email**

---

## Task 5: Clusterwide PV

**Files:**
- Create: `clusters/vollminlab-cluster/clusterwide/pv-filebrowser.yaml`
- Modify: `clusters/vollminlab-cluster/clusterwide/kustomization.yaml`

- [ ] **Create `clusters/vollminlab-cluster/clusterwide/pv-filebrowser.yaml`**:

  ```yaml
  apiVersion: v1
  kind: PersistentVolume
  metadata:
    name: pv-filebrowser
    labels:
      app: mediastack
      env: production
      category: media
  spec:
    capacity:
      storage: 100Gi
    accessModes:
      - ReadWriteMany
    persistentVolumeReclaimPolicy: Retain
    storageClassName: smb
    csi:
      driver: smb.csi.k8s.io
      volumeHandle: filebrowser
      volumeAttributes:
        source: "//192.168.150.2/FileBrowser"
      nodeStageSecretRef:
        name: smb-credentials
        namespace: mediastack
    mountOptions:
      - uid=568
      - gid=568
      - dir_mode=0755
      - file_mode=0755
  ```

- [ ] **Add to `clusters/vollminlab-cluster/clusterwide/kustomization.yaml`** (keep alphabetical order in the resources list):

  ```yaml
  - pv-filebrowser.yaml
  ```

- [ ] **Commit**:

  ```bash
  git add clusters/vollminlab-cluster/clusterwide/pv-filebrowser.yaml \
          clusters/vollminlab-cluster/clusterwide/kustomization.yaml
  git commit -m "feat(clusterwide): add pv-filebrowser SMB persistent volume"
  ```

---

## Task 6: SMB PVC

**Files:**
- Create: `clusters/vollminlab-cluster/mediastack/pvcs/pvc-filebrowser.yaml`
- Modify: `clusters/vollminlab-cluster/mediastack/pvcs/kustomization.yaml`

- [ ] **Create `clusters/vollminlab-cluster/mediastack/pvcs/pvc-filebrowser.yaml`**:

  ```yaml
  apiVersion: v1
  kind: PersistentVolumeClaim
  metadata:
    name: pvc-filebrowser
    namespace: mediastack
    labels:
      app: mediastack
      env: production
      category: media
  spec:
    accessModes:
      - ReadWriteMany
    resources:
      requests:
        storage: 100Gi
    volumeName: pv-filebrowser
    storageClassName: smb
  ```

- [ ] **Add to `clusters/vollminlab-cluster/mediastack/pvcs/kustomization.yaml`** (keep alphabetical order):

  ```yaml
  - pvc-filebrowser.yaml
  ```

- [ ] **Commit**:

  ```bash
  git add clusters/vollminlab-cluster/mediastack/pvcs/pvc-filebrowser.yaml \
          clusters/vollminlab-cluster/mediastack/pvcs/kustomization.yaml
  git commit -m "feat(mediastack): add pvc-filebrowser SMB claim"
  ```

---

## Task 7: FileBrowser app directory

**Files:**
- Create: `clusters/vollminlab-cluster/mediastack/filebrowser/app/pvc-filebrowser-config.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/filebrowser/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/filebrowser/app/deployment.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/filebrowser/app/service.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/filebrowser/app/ingress.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/filebrowser/app/kustomization.yaml`

- [ ] **Create the directory**:

  ```bash
  mkdir -p clusters/vollminlab-cluster/mediastack/filebrowser/app
  ```

- [ ] **Create `pvc-filebrowser-config.yaml`** (Longhorn PVC for the SQLite database — 1Gi, ample for the database, confirmed schedulable across workers 01/02/04):

  ```yaml
  apiVersion: v1
  kind: PersistentVolumeClaim
  metadata:
    name: pvc-filebrowser-config
    namespace: mediastack
    labels:
      app: filebrowser
      env: production
      category: apps
  spec:
    accessModes:
      - ReadWriteOnce
    resources:
      requests:
        storage: 1Gi
    storageClassName: longhorn
  ```

- [ ] **Create `configmap.yaml`** (FileBrowser server settings; auth method is set via env vars in the Deployment):

  ```yaml
  apiVersion: v1
  kind: ConfigMap
  metadata:
    name: filebrowser-settings
    namespace: mediastack
    labels:
      app: filebrowser
      env: production
      category: apps
  data:
    settings.json: |
      {
        "port": 80,
        "baseURL": "",
        "address": "",
        "log": "stdout",
        "database": "/config/database.db",
        "root": "/srv",
        "signup": false
      }
  ```

- [ ] **Create `deployment.yaml`**:

  ```yaml
  apiVersion: apps/v1
  kind: Deployment
  metadata:
    name: filebrowser
    namespace: mediastack
    labels:
      app: filebrowser
      env: production
      category: apps
      app.kubernetes.io/name: filebrowser
  spec:
    replicas: 1
    selector:
      matchLabels:
        app: filebrowser
    template:
      metadata:
        labels:
          app: filebrowser
          env: production
          category: apps
          app.kubernetes.io/name: filebrowser
      spec:
        containers:
          - name: filebrowser
            image: filebrowser/filebrowser:v2.63.3
            args: ["--config", "/config/settings.json"]
            env:
              - name: FB_AUTH_METHOD
                value: proxy
              - name: FB_AUTH_HEADER
                value: X-authentik-username
            ports:
              - containerPort: 80
            volumeMounts:
              - name: config
                mountPath: /config
              - name: settings
                mountPath: /config/settings.json
                subPath: settings.json
              - name: data
                mountPath: /srv
            resources:
              requests:
                cpu: 50m
                memory: 64Mi
              limits:
                cpu: 200m
                memory: 256Mi
        volumes:
          - name: config
            persistentVolumeClaim:
              claimName: pvc-filebrowser-config
          - name: settings
            configMap:
              name: filebrowser-settings
          - name: data
            persistentVolumeClaim:
              claimName: pvc-filebrowser
  ```

- [ ] **Create `service.yaml`**:

  ```yaml
  apiVersion: v1
  kind: Service
  metadata:
    name: filebrowser
    namespace: mediastack
    labels:
      app: filebrowser
      env: production
      category: apps
  spec:
    selector:
      app: filebrowser
    ports:
      - name: http
        port: 80
        targetPort: 80
  ```

- [ ] **Create `ingress.yaml`** (standard Authentik forward-auth annotations, identical to sabnzbd/sonarr/etc.):

  ```yaml
  apiVersion: networking.k8s.io/v1
  kind: Ingress
  metadata:
    name: filebrowser-ingress
    namespace: mediastack
    annotations:
      nginx.ingress.kubernetes.io/ssl-redirect: "true"
      nginx.ingress.kubernetes.io/auth-url: "http://authentik-proxy.authentik.svc.cluster.local:9000/outpost.goauthentik.io/auth/nginx"
      nginx.ingress.kubernetes.io/auth-signin: "https://authentik.vollminlab.com/outpost.goauthentik.io/start?rd=$scheme://$http_host$escaped_request_uri"
      nginx.ingress.kubernetes.io/auth-response-headers: "Set-Cookie,X-authentik-username,X-authentik-groups,X-authentik-email,X-authentik-name,X-authentik-uid"
      nginx.ingress.kubernetes.io/proxy-buffer-size: "128k"
      nginx.ingress.kubernetes.io/auth-snippet: |
        proxy_set_header X-Forwarded-Host $http_host;
      shlink.vollminlab.com/slug: filebrowser
    labels:
      app: filebrowser
      env: production
      category: apps
  spec:
    ingressClassName: nginx
    rules:
      - host: filebrowser.vollminlab.com
        http:
          paths:
            - path: /
              pathType: Prefix
              backend:
                service:
                  name: filebrowser
                  port:
                    number: 80
    tls:
      - hosts:
          - filebrowser.vollminlab.com
        secretName: wildcard-tls
  ```

- [ ] **Create `kustomization.yaml`**:

  ```yaml
  apiVersion: kustomize.config.k8s.io/v1beta1
  kind: Kustomization
  metadata:
    name: filebrowser-app
  resources:
    - pvc-filebrowser-config.yaml
    - configmap.yaml
    - deployment.yaml
    - service.yaml
    - ingress.yaml
  ```

- [ ] **Commit**:

  ```bash
  git add clusters/vollminlab-cluster/mediastack/filebrowser/
  git commit -m "feat(mediastack): add FileBrowser deployment, service, ingress, and config PVC"
  ```

---

## Task 8: cloudflared-filebrowser

**Files:**
- Create: `clusters/vollminlab-cluster/mediastack/cloudflared-filebrowser/app/deployment.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/cloudflared-filebrowser/app/cloudflared-filebrowser-tunnel-credentials-sealedsecret.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/cloudflared-filebrowser/app/kustomization.yaml`

- [ ] **Create the directory**:

  ```bash
  mkdir -p clusters/vollminlab-cluster/mediastack/cloudflared-filebrowser/app
  ```

- [ ] **Seal the tunnel token** (retrieve from 1Password where you stored it in Task 3):

  ```bash
  TUNNEL_TOKEN=$(op item get "FileBrowser Cloudflare Tunnel Token" --vault Homelab --format json \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f.get('label')=='password' or f.get('type')=='CONCEALED'))")

  kubeseal --fetch-cert \
    --controller-namespace sealed-secrets \
    --controller-name sealed-secrets-controller > pub-cert.pem

  kubectl create secret generic cloudflared-filebrowser-tunnel-credentials \
    -n mediastack \
    --from-literal=tunnel-token="$TUNNEL_TOKEN" \
    --dry-run=client -o yaml | \
  kubeseal --cert pub-cert.pem --format yaml \
    > clusters/vollminlab-cluster/mediastack/cloudflared-filebrowser/app/cloudflared-filebrowser-tunnel-credentials-sealedsecret.yaml

  rm pub-cert.pem
  ```

- [ ] **Add required labels to the SealedSecret** — `kubeseal` does not emit labels automatically. Edit the generated file and ensure `spec.template.metadata.labels` is present:

  ```yaml
  spec:
    encryptedData:
      tunnel-token: <sealed-value>
    template:
      metadata:
        name: cloudflared-filebrowser-tunnel-credentials
        namespace: mediastack
        labels:
          app: cloudflared-filebrowser
          env: production
          category: networking
  ```

  Compare against `clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/app/cloudflared-audiobookshelf-tunnel-credentials-sealedsecret.yaml` to confirm structure.

- [ ] **Create `deployment.yaml`** (mirrors cloudflared-audiobookshelf exactly):

  ```yaml
  apiVersion: apps/v1
  kind: Deployment
  metadata:
    name: cloudflared-filebrowser
    namespace: mediastack
    labels:
      app: cloudflared-filebrowser
      env: production
      category: networking
  spec:
    replicas: 1
    selector:
      matchLabels:
        app: cloudflared-filebrowser
    template:
      metadata:
        labels:
          app: cloudflared-filebrowser
          env: production
          category: networking
      spec:
        containers:
          - name: cloudflared
            image: cloudflare/cloudflared:2026.3.0
            args:
              - tunnel
              - --no-autoupdate
              - run
            env:
              - name: TUNNEL_TOKEN
                valueFrom:
                  secretKeyRef:
                    name: cloudflared-filebrowser-tunnel-credentials
                    key: tunnel-token
            resources:
              requests:
                cpu: 50m
                memory: 64Mi
              limits:
                cpu: 200m
                memory: 128Mi
  ```

- [ ] **Create `kustomization.yaml`**:

  ```yaml
  apiVersion: kustomize.config.k8s.io/v1beta1
  kind: Kustomization
  metadata:
    name: cloudflared-filebrowser-app
  resources:
    - cloudflared-filebrowser-tunnel-credentials-sealedsecret.yaml
    - deployment.yaml
  ```

- [ ] **Commit**:

  ```bash
  git add clusters/vollminlab-cluster/mediastack/cloudflared-filebrowser/
  git commit -m "feat(mediastack): add cloudflared-filebrowser tunnel deployment"
  ```

---

## Task 9: Flux wiring

**Files:**
- Modify: `clusters/vollminlab-cluster/mediastack/kustomization.yaml`

The `mediastack` Flux Kustomization CR covers the entire `mediastack/` directory — no new Flux Kustomization CRs needed. FileBrowser uses a raw Deployment (no HelmRelease) — no HelmRepository entry needed. Only the namespace-level resource list needs updating.

- [ ] **Add both new apps to `clusters/vollminlab-cluster/mediastack/kustomization.yaml`** (alphabetical in the resources list):

  ```yaml
  resources:
    - namespace.yaml
    - arr-media-dashboard-configmap.yaml
    - ./secrets
    - ./audiobookshelf/app
    - ./bazarr/app
    - ./cloudflared-audiobookshelf/app
    - ./cloudflared-filebrowser/app       # ← add
    - ./cloudflared-jellyfin/app
    - ./bazarr-exportarr/app
    - ./filebrowser/app                   # ← add
    - ./jellyfin/app
    - ./jellystat-db/app
    - ./jellystat/app
    - ./prowlarr/app
    - ./radarr/app
    - ./sabnzbd/app
    - ./seerr/app
    - ./sonarr/app
    - ./pvcs
  ```

- [ ] **Commit**:

  ```bash
  git add clusters/vollminlab-cluster/mediastack/kustomization.yaml
  git commit -m "feat(mediastack): wire filebrowser and cloudflared-filebrowser into Flux"
  ```

---

## Task 10: Open the PR

- [ ] **Push and open the PR**:

  ```bash
  git push -u origin feat/filebrowser
  gh pr create \
    --title "feat(mediastack): deploy FileBrowser file drop service" \
    --body "Deploys FileBrowser in mediastack as a general-purpose authenticated file drop. Authentik forward-auth via nginx handles SSO with proxy auth mode (no second login prompt). Cloudflare Tunnel routes through nginx to keep the forward-auth chain intact. Includes Cloudflare tunnel/DNS and Authentik application/user tofu changes."
  ```

- [ ] **Wait for CI** (Flux Helm values check, Kyverno CI, secret scanning)

- [ ] **After merge**, verify Flux reconciles cleanly:

  ```bash
  flux get kustomizations mediastack -n flux-system
  # Expected: Applied revision: main/<sha>, True, True

  kubectl get pods -n mediastack -l app=filebrowser
  # Expected: 1/1 Running

  kubectl get pods -n mediastack -l app=cloudflared-filebrowser
  # Expected: 1/1 Running
  ```

---

## Task 11: Bootstrap FileBrowser users

Runs **after** the pod is Running. FileBrowser stores user accounts in its SQLite database; they are not auto-created from proxy auth headers.

- [ ] **Get the pod name**:

  ```bash
  FILEBROWSER_POD=$(kubectl get pods -n mediastack -l app=filebrowser \
    -o jsonpath='{.items[0].metadata.name}')
  echo $FILEBROWSER_POD
  ```

- [ ] **Create your admin account** (password is random and unused — proxy auth bypasses it):

  ```bash
  kubectl exec -n mediastack $FILEBROWSER_POD -- \
    filebrowser users add vollmin "$(openssl rand -base64 24)" \
    --perm.admin \
    --database /config/database.db
  # Expected: INFO  users: created user vollmin
  ```

- [ ] **Create the friend's scoped account** (replace `<friend-username>` with the actual Authentik username):

  ```bash
  kubectl exec -n mediastack $FILEBROWSER_POD -- \
    filebrowser users add <friend-username> "$(openssl rand -base64 24)" \
    --scope /Audiobooks \
    --perm.create=true \
    --perm.upload=true \
    --perm.download=true \
    --perm.delete=false \
    --perm.rename=false \
    --perm.modify=false \
    --perm.admin=false \
    --database /config/database.db
  # Expected: INFO  users: created user <friend-username>
  ```

- [ ] **List users to confirm both exist**:

  ```bash
  kubectl exec -n mediastack $FILEBROWSER_POD -- \
    filebrowser users ls --database /config/database.db
  # Expected: two rows — vollmin (admin, scope /) and <friend-username> (scope /Audiobooks)
  ```

- [ ] **Verify proxy auth mode** — check the startup logs for auth method:

  ```bash
  kubectl logs -n mediastack $FILEBROWSER_POD | grep -i "auth"
  # Expected: lines referencing proxy auth, no "login" form references
  ```

  If `FB_AUTH_METHOD` env var was not picked up (rare — only if database was pre-existing with password auth), force it:

  ```bash
  kubectl exec -n mediastack $FILEBROWSER_POD -- \
    filebrowser config set \
    --auth.method proxy \
    --auth.header X-authentik-username \
    --database /config/database.db
  kubectl rollout restart deployment/filebrowser -n mediastack
  ```

- [ ] **Smoke test — admin**: navigate to `https://filebrowser.vollminlab.com`, authenticate via Authentik with your `vollmin` account → you should land directly in FileBrowser (no second login prompt) and see the full `FileBrowser/` directory

- [ ] **Smoke test — friend**: authenticate via Authentik with the friend's account → verify they see only the `Audiobooks/` folder and can upload but not delete or rename

- [ ] **End-to-end upload test**: from the friend's session, upload a small test file to `Audiobooks/` → verify it appears at `\\smb.vollminlab.com\FileBrowser\Audiobooks\` from Windows
