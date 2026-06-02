# Audiobookshelf Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Audiobookshelf in the `mediastack` namespace, accessible externally via a dedicated Cloudflare Tunnel, with audiobooks served from a new NAS SMB share and config stored on Longhorn.

**Architecture:** home-operations OCI HelmRelease in `mediastack`, backed by a 2Gi Longhorn config PVC and an SMB RWX PVC pointing at `//192.168.150.2/audiobooks`. A dedicated `cloudflared-audiobookshelf` Deployment carries external traffic through Cloudflare with no open router ports. Internal access via nginx Ingress at `audiobookshelf.vollminlab.com`.

**Tech Stack:** Flux CD, Helm (home-operations OCI chart), Longhorn, smb-csi-driver, cloudflared, kubeseal, nginx Ingress, cert-manager wildcard TLS.

---

## Prerequisites (manual — do before Task 1)

These cannot be automated and block deployment. Complete them first.

**A. Create the NAS SMB share**

On TrueNAS, create a new SMB share named `audiobooks`. It must be accessible from the cluster using the existing `smb-credentials` secret (same credentials used by movies/tv). Verify connectivity from a cluster node:

```bash
smbclient //192.168.150.2/audiobooks -U <smb-user>
```

**B. Create the Cloudflare tunnel**

1. Go to Cloudflare Zero Trust → Networks → Tunnels → Create tunnel
2. Name it `audiobookshelf`
3. Under Public Hostnames, add a route:
   - Subdomain/domain: whatever public URL friends will use
   - Service: `http://audiobookshelf.mediastack.svc.cluster.local:80`
4. Copy the tunnel token — you will seal it in Task 5

---

## File Map

| Action | Path |
|--------|------|
| Create | `clusters/vollminlab-cluster/flux-system/repositories/audiobookshelf-ocirepository.yaml` |
| Create | `clusters/vollminlab-cluster/clusterwide/pv-audiobooks.yaml` |
| Create | `clusters/vollminlab-cluster/mediastack/pvcs/pvc-audiobooks.yaml` |
| Create | `clusters/vollminlab-cluster/mediastack/audiobookshelf/app/helmrelease.yaml` |
| Create | `clusters/vollminlab-cluster/mediastack/audiobookshelf/app/configmap.yaml` |
| Create | `clusters/vollminlab-cluster/mediastack/audiobookshelf/app/ingress.yaml` |
| Create | `clusters/vollminlab-cluster/mediastack/audiobookshelf/app/pvc-audiobookshelf-config.yaml` |
| Create | `clusters/vollminlab-cluster/mediastack/audiobookshelf/app/kustomization.yaml` |
| Create | `clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/app/deployment.yaml` |
| Create | `clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/app/cloudflared-audiobookshelf-tunnel-sealedsecret.yaml` |
| Create | `clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/app/kustomization.yaml` |
| Modify | `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml` |
| Modify | `clusters/vollminlab-cluster/clusterwide/kustomization.yaml` |
| Modify | `clusters/vollminlab-cluster/mediastack/kustomization.yaml` |
| Modify | `clusters/vollminlab-cluster/mediastack/pvcs/kustomization.yaml` |

---

## Task 1: Branch setup and chart version lookup

**Files:** none modified yet

- [ ] **Create the feature branch**

```bash
git checkout main && git pull
git checkout -b feat/audiobookshelf
```

- [ ] **Look up the current home-operations audiobookshelf chart version**

```bash
crane ls ghcr.io/home-operations/charts/audiobookshelf | sort -V | tail -5
```

If `crane` is not installed: `go install github.com/google/go-containerregistry/cmd/crane@latest` or check https://github.com/home-operations/charts/releases and filter for audiobookshelf.

Note the latest version tag (e.g. `2.4.3`). You will use it in Task 2 and Task 4.

- [ ] **Inspect the chart values schema**

```bash
helm show values oci://ghcr.io/home-operations/charts/audiobookshelf --version <VERSION>
```

Skim the output for the persistence keys (likely `persistence.config` and `persistence.audiobooks` or similar), the security context keys, and the resource limit keys. You need these exact key names for Task 4's configmap. If the keys differ from what Task 4 shows, adjust Task 4 accordingly.

---

## Task 2: OCIRepository source

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/audiobookshelf-ocirepository.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`

- [ ] **Create the OCIRepository**

```yaml
# clusters/vollminlab-cluster/flux-system/repositories/audiobookshelf-ocirepository.yaml
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: OCIRepository
metadata:
  name: audiobookshelf-repo
  namespace: flux-system
  labels:
    app: audiobookshelf
    env: production
    category: media
spec:
  url: oci://ghcr.io/home-operations/charts/audiobookshelf
  interval: 5m
  ref:
    tag: <VERSION>   # replace with version from Task 1
```

- [ ] **Add to repositories/kustomization.yaml**

Open `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`. Insert `- audiobookshelf-ocirepository.yaml` in alphabetical order — after `arc-runners-ocirepository.yaml`, before `bazarr-helmrepository.yaml`:

```yaml
  - arc-runners-ocirepository.yaml
  - audiobookshelf-ocirepository.yaml   # add this line
  - bazarr-helmrepository.yaml
```

- [ ] **Commit**

```bash
git add clusters/vollminlab-cluster/flux-system/repositories/audiobookshelf-ocirepository.yaml \
        clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml
git commit -m "feat: add audiobookshelf OCIRepository source"
```

---

## Task 3: SMB PersistentVolume and PVC

**Files:**
- Create: `clusters/vollminlab-cluster/clusterwide/pv-audiobooks.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/pvcs/pvc-audiobooks.yaml`
- Modify: `clusters/vollminlab-cluster/clusterwide/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/mediastack/pvcs/kustomization.yaml`

- [ ] **Create the PersistentVolume**

```yaml
# clusters/vollminlab-cluster/clusterwide/pv-audiobooks.yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: pv-audiobooks
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
    volumeHandle: audiobooks
    volumeAttributes:
      source: "//192.168.150.2/audiobooks"
    nodeStageSecretRef:
      name: smb-credentials
      namespace: mediastack
  mountOptions:
    - uid=568
    - gid=568
    - dir_mode=0755
    - file_mode=0755
```

- [ ] **Create the PVC**

```yaml
# clusters/vollminlab-cluster/mediastack/pvcs/pvc-audiobooks.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-audiobooks
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
  volumeName: pv-audiobooks
  storageClassName: smb
```

- [ ] **Add PV to clusterwide/kustomization.yaml**

Insert `- pv-audiobooks.yaml` in alphabetical order — before `pv-completed-downloads.yaml`:

```yaml
  - pv-audiobooks.yaml
  - pv-completed-downloads.yaml
  - pv-incomplete-downloads.yaml
  - pv-movies.yaml
  - pv-tv.yaml
```

- [ ] **Add PVC to pvcs/kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/mediastack/pvcs/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: mediastack-pvcs
resources:
  - pvc-audiobooks.yaml
  - pvc-completed-downloads.yaml
  - pvc-incomplete-downloads.yaml
  - pvc-movies.yaml
  - pvc-tv.yaml
```

- [ ] **Commit**

```bash
git add clusters/vollminlab-cluster/clusterwide/pv-audiobooks.yaml \
        clusters/vollminlab-cluster/clusterwide/kustomization.yaml \
        clusters/vollminlab-cluster/mediastack/pvcs/pvc-audiobooks.yaml \
        clusters/vollminlab-cluster/mediastack/pvcs/kustomization.yaml
git commit -m "feat: add audiobooks SMB PV and PVC"
```

---

## Task 4: Audiobookshelf app files

**Files:**
- Create: `clusters/vollminlab-cluster/mediastack/audiobookshelf/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/audiobookshelf/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/audiobookshelf/app/ingress.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/audiobookshelf/app/pvc-audiobookshelf-config.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/audiobookshelf/app/kustomization.yaml`

- [ ] **Create the config PVC**

```yaml
# clusters/vollminlab-cluster/mediastack/audiobookshelf/app/pvc-audiobookshelf-config.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: pvc-audiobookshelf-config
  namespace: mediastack
  labels:
    app: audiobookshelf
    env: production
    category: media
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
```

- [ ] **Create the HelmRelease**

```yaml
# clusters/vollminlab-cluster/mediastack/audiobookshelf/app/helmrelease.yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: audiobookshelf
  namespace: mediastack
  labels:
    app: audiobookshelf
    env: production
    category: media
spec:
  interval: 5m
  chartRef:
    kind: OCIRepository
    name: audiobookshelf-repo
    namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: audiobookshelf-values
      valuesKey: values.yaml
```

- [ ] **Create the ConfigMap (Helm values)**

> **Note:** Verify the exact persistence/securityContext key names from `helm show values` in Task 1 before writing this file. The keys below are representative of the home-operations chart schema — adjust if the chart uses different key paths.

```yaml
# clusters/vollminlab-cluster/mediastack/audiobookshelf/app/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: audiobookshelf-values
  namespace: mediastack
  labels:
    app: audiobookshelf
    env: production
    category: media
data:
  values.yaml: |
    persistence:
      config:
        existingClaim: pvc-audiobookshelf-config
      audiobooks:
        existingClaim: pvc-audiobooks

    resources:
      requests:
        cpu: 100m
        memory: 256Mi
      limits:
        cpu: 500m
        memory: 512Mi

    defaultPodOptions:
      securityContext:
        runAsUser: 568
        runAsGroup: 568
        fsGroup: 568
        fsGroupChangePolicy: OnRootMismatch

    podLabels:
      app: audiobookshelf
      env: production
      category: media
```

- [ ] **Create the Ingress**

```yaml
# clusters/vollminlab-cluster/mediastack/audiobookshelf/app/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: audiobookshelf-ingress
  namespace: mediastack
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    shlink.vollminlab.com/slug: audiobookshelf
  labels:
    app: audiobookshelf
    env: production
    category: media
spec:
  ingressClassName: nginx
  rules:
    - host: audiobookshelf.vollminlab.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: audiobookshelf
                port:
                  number: 80
  tls:
    - hosts:
        - audiobookshelf.vollminlab.com
      secretName: wildcard-tls
```

> **Note:** The Service name (`audiobookshelf`) must match what the home-operations chart creates. Verify with `helm show values` or the chart README. If the service name differs, update `backend.service.name` accordingly.

- [ ] **Create the app kustomization**

```yaml
# clusters/vollminlab-cluster/mediastack/audiobookshelf/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: audiobookshelf-app
resources:
  - helmrelease.yaml
  - configmap.yaml
  - ingress.yaml
  - pvc-audiobookshelf-config.yaml
```

- [ ] **Commit**

```bash
git add clusters/vollminlab-cluster/mediastack/audiobookshelf/
git commit -m "feat: add audiobookshelf HelmRelease, configmap, ingress, and config PVC"
```

---

## Task 5: Cloudflare tunnel

**Files:**
- Create: `clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/app/deployment.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/app/cloudflared-audiobookshelf-tunnel-sealedsecret.yaml`
- Create: `clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/app/kustomization.yaml`

- [ ] **Seal the tunnel token** (requires tunnel created in Prerequisites)

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic cloudflared-audiobookshelf-tunnel-credentials \
  -n mediastack \
  --from-literal=tunnel-token=<PASTE_TOKEN_HERE> \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/app/cloudflared-audiobookshelf-tunnel-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

Verify the output file contains `kind: SealedSecret` and no plain-text token.

- [ ] **Create the cloudflared Deployment**

```yaml
# clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/app/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cloudflared-audiobookshelf
  namespace: mediastack
  labels:
    app: cloudflared-audiobookshelf
    env: production
    category: networking
spec:
  replicas: 1
  selector:
    matchLabels:
      app: cloudflared-audiobookshelf
  template:
    metadata:
      labels:
        app: cloudflared-audiobookshelf
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
                  name: cloudflared-audiobookshelf-tunnel-credentials
                  key: tunnel-token
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
```

- [ ] **Create the kustomization**

```yaml
# clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: cloudflared-audiobookshelf-app
resources:
  - cloudflared-audiobookshelf-tunnel-sealedsecret.yaml
  - deployment.yaml
```

- [ ] **Commit**

```bash
git add clusters/vollminlab-cluster/mediastack/cloudflared-audiobookshelf/
git commit -m "feat: add cloudflared-audiobookshelf tunnel deployment and sealed secret"
```

---

## Task 6: Wire up mediastack/kustomization.yaml

**Files:**
- Modify: `clusters/vollminlab-cluster/mediastack/kustomization.yaml`

- [ ] **Add both new app directories**

Open `clusters/vollminlab-cluster/mediastack/kustomization.yaml` and add the two new entries in alphabetical order:

```yaml
resources:
  - namespace.yaml
  - arr-media-dashboard-configmap.yaml
  - ./secrets
  - ./audiobookshelf/app          # add — before bazarr
  - ./bazarr/app
  - ./cloudflared/app
  - ./cloudflared-audiobookshelf/app   # add — between cloudflared and cloudflared-jellyfin
  - ./cloudflared-jellyfin/app
  - ./exportarr/app
  - ./overseerr/app
  - ./jellyfin/app
  - ./plex/app
  - ./prowlarr/app
  - ./radarr/app
  - ./sabnzbd/app
  - ./sonarr/app
  - ./tautulli/app
  - ./pvcs
```

- [ ] **Commit**

```bash
git add clusters/vollminlab-cluster/mediastack/kustomization.yaml
git commit -m "feat: wire audiobookshelf and cloudflared-audiobookshelf into mediastack"
```

---

## Task 7: Open PR and verify

- [ ] **Cross-check before pushing**

Verify both Flux index files have been updated:

```bash
grep audiobookshelf clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml
grep audiobookshelf clusters/vollminlab-cluster/mediastack/kustomization.yaml
grep audiobookshelf clusters/vollminlab-cluster/clusterwide/kustomization.yaml
grep audiobookshelf clusters/vollminlab-cluster/mediastack/pvcs/kustomization.yaml
```

All four must return a match. If any is missing, add it before pushing.

- [ ] **Push and open PR**

```bash
git push -u origin feat/audiobookshelf
gh pr create --title "feat: deploy audiobookshelf" --body "$(cat <<'EOF'
## Summary
- Adds Audiobookshelf to the mediastack namespace via home-operations OCI Helm chart
- Audiobook files served from new NAS SMB share (`//192.168.150.2/audiobooks`)
- Config/metadata stored on a 2Gi Longhorn PVC
- External access via dedicated `cloudflared-audiobookshelf` tunnel (no open router ports)
- Internal access at `audiobookshelf.vollminlab.com` with wildcard TLS and shlink slug

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Watch Flux reconcile after merge**

```bash
flux get kustomizations -A --watch
# wait for mediastack to show Ready=True

kubectl get helmrelease audiobookshelf -n mediastack
# expect: Ready=True, REVISION = chart version

kubectl get pods -n mediastack -l app=audiobookshelf
# expect: Running

kubectl get pods -n mediastack -l app=cloudflared-audiobookshelf
# expect: Running
```

- [ ] **Verify internal access**

```bash
kubectl port-forward svc/audiobookshelf -n mediastack 8080:80
# browse to http://localhost:8080 — ABS login page should appear
```

- [ ] **Verify external access**

Open the public Cloudflare hostname you configured in the tunnel. ABS login page should appear. Complete initial setup: create admin account with a strong password, disable the guest account in Settings → Users.

- [ ] **Verify shlink short URL**

```bash
# The shlink-ingress-controller should have auto-created vollm.in/audiobookshelf
curl -I https://vollm.in/audiobookshelf
# expect: 302 redirect to audiobookshelf.vollminlab.com
```

---

## Post-deploy: add audiobook files

Once the service is running, copy your audiobooks to the NAS share. ABS will scan the `/audiobooks` mount on demand — go to Settings → Libraries → Scan to trigger a library scan after uploading files.
