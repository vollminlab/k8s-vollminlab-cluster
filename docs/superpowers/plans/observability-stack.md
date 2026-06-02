# Observability Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy kube-prometheus-stack + Loki + Promtail in the `monitoring` namespace, backed by MinIO object storage, with Alertmanager → PushOver notifications and Grafana as the unified metrics + logs UI.

**Architecture:** Promtail DaemonSet ships logs from all nodes to Loki (SingleBinary, MinIO-backed). Prometheus scrapes cluster metrics. Grafana is the single pane of glass for both. Alertmanager routes all alerts to PushOver via a SealedSecret-backed config.

**Tech Stack:** kube-prometheus-stack (Prometheus + Grafana + Alertmanager), Grafana Loki (SingleBinary), Promtail, MinIO (existing), Flux CD, SealedSecrets, Longhorn PVCs.

---

## File Map

### PR 1 — MinIO loki bucket

| Action | File |
|---|---|
| Modify | `clusters/vollminlab-cluster/minio/minio/app/configmap.yaml` |

### PR 2 — Observability stack (all new unless noted)

| Action | File |
|---|---|
| Create | `clusters/vollminlab-cluster/flux-system/repositories/prometheus-community-helmrepository.yaml` |
| Create | `clusters/vollminlab-cluster/flux-system/repositories/grafana-helmrepository.yaml` |
| **Modify** | `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml` |
| **Modify** | `clusters/vollminlab-cluster/monitoring/kustomization.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/helmrelease.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/ingress.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/alertmanager-sealedsecret.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/grafana-admin-sealedsecret.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/kustomization.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/loki/app/helmrelease.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/loki/app/configmap.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/loki/app/loki-minio-sealedsecret.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/loki/app/kustomization.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/promtail/app/helmrelease.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/promtail/app/configmap.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/promtail/app/kustomization.yaml` |

### PR 3 — ECK removal (deferred, do after Loki confirmed healthy)

| Action | File |
|---|---|
| Delete | `clusters/vollminlab-cluster/elastic-system/` (entire directory) |
| Delete | `clusters/vollminlab-cluster/flux-system/repositories/elastic-helmrepository.yaml` |
| **Modify** | `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml` |
| **Modify** | `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml` |

---

## PR 1 — MinIO loki bucket

### Task 1: Add loki bucket to MinIO

**Files:**
- Modify: `clusters/vollminlab-cluster/minio/minio/app/configmap.yaml`

- [ ] **Step 1: Add loki bucket entry**

In `clusters/vollminlab-cluster/minio/minio/app/configmap.yaml`, find the `buckets:` section and add the `loki` entry after `velero`:

```yaml
    buckets:
      - name: velero
        policy: none
        purge: false
        versioning: false
        objectlocking: false
      - name: loki
        policy: none
        purge: false
        versioning: false
        objectlocking: false
```

- [ ] **Step 2: Verify the file looks correct**

```bash
cat clusters/vollminlab-cluster/minio/minio/app/configmap.yaml
```

Expected: two entries under `buckets:`, velero then loki.

- [ ] **Step 3: Commit**

```bash
git add clusters/vollminlab-cluster/minio/minio/app/configmap.yaml
git commit -m "feat(minio): add loki bucket for log storage backend"
```

- [ ] **Step 4: Push branch and open PR**

```bash
git push -u origin <branch-name>
gh pr create --title "feat(minio): add loki bucket" --body "$(cat <<'EOF'
## Summary
- Adds `loki` bucket to MinIO for Loki log storage backend
- Pre-requisite for observability stack PR

## Test plan
- [ ] PR merges and Flux reconciles minio HelmRelease
- [ ] MinIO console shows `loki` bucket created: `https://minio.vollminlab.com`
EOF
)"
```

---

## PR 2 — Observability Stack

Start from a clean branch off `main` after PR 1 merges.

### Task 2: HelmRepository — prometheus-community

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/prometheus-community-helmrepository.yaml`

- [ ] **Step 1: Create the HelmRepository file**

```yaml
# clusters/vollminlab-cluster/flux-system/repositories/prometheus-community-helmrepository.yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: prometheus-community-repo
  namespace: flux-system
  labels:
    app: prometheus-community
    env: production
    category: observability
spec:
  interval: 5m
  url: https://prometheus-community.github.io/helm-charts
  timeout: 3m
```

- [ ] **Step 2: Look up the latest stable kube-prometheus-stack chart version**

Run this on the dev sandbox VM (kubectl is pre-configured there):

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm search repo prometheus-community/kube-prometheus-stack --versions | head -5
```

Note the latest stable version (e.g. `67.3.0`). You will use this in Task 5.

### Task 3: HelmRepository — grafana

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/grafana-helmrepository.yaml`

- [ ] **Step 1: Create the HelmRepository file**

```yaml
# clusters/vollminlab-cluster/flux-system/repositories/grafana-helmrepository.yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: grafana-repo
  namespace: flux-system
  labels:
    app: grafana
    env: production
    category: observability
spec:
  interval: 5m
  url: https://grafana.github.io/helm-charts
  timeout: 3m
```

- [ ] **Step 2: Look up latest stable Loki and Promtail chart versions**

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
helm search repo grafana/loki --versions | head -5
helm search repo grafana/promtail --versions | head -5
```

Note the latest stable versions for Loki (e.g. `6.6.2`) and Promtail (e.g. `6.16.6`). You will use these in Tasks 8 and 10.

### Task 4: Register both HelmRepositories in the Flux index

**Files:**
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`

- [ ] **Step 1: Add two entries to the resources list**

In `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`, add to the `resources:` list (alphabetical order near the top):

```yaml
  - grafana-helmrepository.yaml
  - prometheus-community-helmrepository.yaml
```

The final file looks like:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: flux-repositories
  labels:
    app: flux-repositories
    env: production
    category: system
resources:
  - cnpg-helmrepository.yaml
  - arc-controller-helmrepository.yaml
  - arc-runners-helmrepository.yaml
  - bazarr-helmrepository.yaml
  - cert-manager-helmrepository.yaml
  - grafana-helmrepository.yaml
  - ingress-nginx-helmrepository.yaml
  - capacitor-helmrepository.yaml
  - elastic-helmrepository.yaml
  - external-dns-helmrepository.yaml
  - homepage-helmrepository.yaml
  - kyverno-helmrepository.yaml
  - kyverno-policyreporter-helmrepository.yaml
  - local-path-provisioner-gitrepository.yaml
  - longhorn-helmrepository.yaml
  - metallb-helmrepository.yaml
  - metrics-server-helmrepository.yaml
  - minecraft-helmrepository.yaml
  - overseerr-helmrepository.yaml
  - portainer-helmrepository.yaml
  - prometheus-community-helmrepository.yaml
  - prowlarr-helmrepository.yaml
  - radarr-helmrepository.yaml
  - sabnzbd-helmrepository.yaml
  - sealed-secrets-helmrepository.yaml
  - smb-csi-driver-helmrepository.yaml
  - sonarr-helmrepository.yaml
  - tautulli-helmrepository.yaml
  - minio-helmrepository.yaml
  - renovate-helmrepository.yaml
  - shlink-helmrepository.yaml
  - velero-helmrepository.yaml
  - harbor-helmrepository.yaml
  - vollminlab-oci-helmrepository.yaml
```

### Task 5: Seal the Alertmanager PushOver config secret

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/alertmanager-sealedsecret.yaml`

The Alertmanager SealedSecret contains a full `alertmanager.yaml` config with PushOver credentials. Never write the plain secret to disk.

- [ ] **Step 1: Get PushOver credentials from 1Password**

In 1Password → Homelab vault, find the "PushOver" entry. Note the **app token** and **user key**.

- [ ] **Step 2: Create and seal the secret (on dev sandbox VM)**

Replace `<APP_TOKEN>` and `<USER_KEY>` with the values from 1Password:

```bash
# Fetch current sealing cert
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

# Write alertmanager config to a temp file (never commit this file)
cat > /tmp/alertmanager.yaml <<'AMEOF'
global:
  resolve_timeout: 5m
route:
  group_by: ['alertname', 'job']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 12h
  receiver: pushover
receivers:
  - name: pushover
    pushover_configs:
      - token: <APP_TOKEN>
        user_key: <USER_KEY>
AMEOF

# Create secret and seal it — pipe only, no plain secret on disk
kubectl create secret generic alertmanager-pushover-config \
  -n monitoring \
  --from-file=alertmanager.yaml=/tmp/alertmanager.yaml \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/alertmanager-sealedsecret.yaml

# Clean up
rm /tmp/pub-cert.pem /tmp/alertmanager.yaml
```

- [ ] **Step 3: Verify the sealed secret looks correct**

```bash
head -20 clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/alertmanager-sealedsecret.yaml
```

Expected: `kind: SealedSecret`, `metadata.name: alertmanager-pushover-config`, `metadata.namespace: monitoring`, `encryptedData.alertmanager.yaml` field present.

### Task 6: Seal the Grafana admin credentials secret

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/grafana-admin-sealedsecret.yaml`

Per `secrets.md`: never put passwords in ConfigMaps. Grafana admin password must be in a SealedSecret.

- [ ] **Step 1: Generate a strong password and store it in 1Password**

```bash
# Generate a 32-character password
openssl rand -base64 24
```

Save it to 1Password → Homelab vault as "Grafana Admin Password" (username: `admin`).

- [ ] **Step 2: Seal the secret (on dev sandbox VM)**

Replace `<GRAFANA_ADMIN_PASSWORD>` with the generated password:

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic grafana-admin-credentials \
  -n monitoring \
  --from-literal=admin-user=admin \
  --from-literal=admin-password='<GRAFANA_ADMIN_PASSWORD>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/grafana-admin-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 3: Verify**

```bash
head -15 clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/grafana-admin-sealedsecret.yaml
```

Expected: `kind: SealedSecret`, `metadata.name: grafana-admin-credentials`, `metadata.namespace: monitoring`.

### Task 7: kube-prometheus-stack HelmRelease, ConfigMap, and Ingress

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/ingress.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/kustomization.yaml`

- [ ] **Step 1: Create helmrelease.yaml**

Replace `<CHART_VERSION>` with the version you found in Task 2 Step 2:

```yaml
# clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/helmrelease.yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: kube-prometheus-stack
  namespace: monitoring
  labels:
    app: kube-prometheus-stack
    env: production
    category: observability
spec:
  interval: 5m
  releaseName: kube-prometheus-stack
  chart:
    spec:
      chart: kube-prometheus-stack
      version: <CHART_VERSION>
      sourceRef:
        kind: HelmRepository
        name: prometheus-community-repo
        namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: kube-prometheus-stack-values
      valuesKey: values.yaml
```

- [ ] **Step 2: Create configmap.yaml**

```yaml
# clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: kube-prometheus-stack-values
  namespace: monitoring
  labels:
    app: kube-prometheus-stack
    env: production
    category: observability
data:
  values.yaml: |
    fullnameOverride: kube-prometheus-stack

    prometheus:
      prometheusSpec:
        scrapeInterval: 30s
        retention: 15d
        serviceMonitorSelectorNilUsesHelmValues: false
        serviceMonitorSelector: {}
        serviceMonitorNamespaceSelector: {}
        podMetadata:
          labels:
            app: prometheus
            env: production
            category: observability
        resources:
          requests:
            cpu: 250m
            memory: 512Mi
          limits:
            cpu: 1000m
            memory: 1Gi
        storageSpec:
          volumeClaimTemplate:
            spec:
              storageClassName: longhorn
              accessModes:
                - ReadWriteOnce
              resources:
                requests:
                  storage: 5Gi

    grafana:
      enabled: true
      ingress:
        enabled: false
      admin:
        existingSecret: grafana-admin-credentials
        userKey: admin-user
        passwordKey: admin-password
      podLabels:
        app: grafana
        env: production
        category: observability
      resources:
        requests:
          cpu: 100m
          memory: 128Mi
        limits:
          cpu: 500m
          memory: 256Mi
      additionalDataSources:
        - name: Loki
          type: loki
          url: http://loki.monitoring.svc.cluster.local:3100
          access: proxy

    alertmanager:
      alertmanagerSpec:
        configSecret: alertmanager-pushover-config
        podMetadata:
          labels:
            app: alertmanager
            env: production
            category: observability
        resources:
          requests:
            cpu: 50m
            memory: 64Mi
          limits:
            cpu: 200m
            memory: 128Mi
        storage:
          volumeClaimTemplate:
            spec:
              storageClassName: longhorn
              accessModes:
                - ReadWriteOnce
              resources:
                requests:
                  storage: 1Gi

    prometheus-node-exporter:
      tolerations:
        - key: dmz
          operator: Equal
          value: "true"
          effect: NoSchedule
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      podLabels:
        app: node-exporter
        env: production
        category: observability

    kube-state-metrics:
      podLabels:
        app: kube-state-metrics
        env: production
        category: observability
```

- [ ] **Step 3: Create ingress.yaml**

```yaml
# clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: grafana-ingress
  namespace: monitoring
  labels:
    app: grafana
    env: production
    category: observability
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    shlink.vollminlab.com/slug: grafana
spec:
  ingressClassName: nginx
  rules:
    - host: grafana.vollminlab.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: kube-prometheus-stack-grafana
                port:
                  number: 80
  tls:
    - hosts:
        - grafana.vollminlab.com
      secretName: wildcard-tls
```

- [ ] **Step 4: Create kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: kube-prometheus-stack-deployment
  namespace: flux-system
resources:
  - helmrelease.yaml
  - configmap.yaml
  - ingress.yaml
  - alertmanager-sealedsecret.yaml
  - grafana-admin-sealedsecret.yaml
```

### Task 8: Seal the Loki MinIO credentials secret

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/loki/app/loki-minio-sealedsecret.yaml`

SealedSecrets are namespace-scoped. Even though MinIO's own secret exists in the `minio` namespace, Loki needs its own seal of the same credentials in `monitoring`.

- [ ] **Step 1: Get MinIO root credentials from 1Password**

In 1Password → Homelab vault, find the "MinIO Credentials" entry. Note the **root user** (access key) and **root password** (secret key).

- [ ] **Step 2: Create and seal the secret (on dev sandbox VM)**

Replace `<MINIO_ROOT_USER>` and `<MINIO_ROOT_PASSWORD>` with values from 1Password:

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem

kubectl create secret generic loki-minio-credentials \
  -n monitoring \
  --from-literal=access-key='<MINIO_ROOT_USER>' \
  --from-literal=secret-key='<MINIO_ROOT_PASSWORD>' \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/monitoring/loki/app/loki-minio-sealedsecret.yaml

rm /tmp/pub-cert.pem
```

- [ ] **Step 3: Verify**

```bash
head -15 clusters/vollminlab-cluster/monitoring/loki/app/loki-minio-sealedsecret.yaml
```

Expected: `kind: SealedSecret`, `metadata.name: loki-minio-credentials`, `metadata.namespace: monitoring`.

### Task 9: Loki HelmRelease, ConfigMap, and kustomization

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/loki/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/loki/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/loki/app/kustomization.yaml`

- [ ] **Step 1: Create helmrelease.yaml**

Replace `<LOKI_CHART_VERSION>` with the version you found in Task 3 Step 2:

```yaml
# clusters/vollminlab-cluster/monitoring/loki/app/helmrelease.yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: loki
  namespace: monitoring
  labels:
    app: loki
    env: production
    category: observability
spec:
  interval: 5m
  releaseName: loki
  chart:
    spec:
      chart: loki
      version: <LOKI_CHART_VERSION>
      sourceRef:
        kind: HelmRepository
        name: grafana-repo
        namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: loki-values
      valuesKey: values.yaml
```

- [ ] **Step 2: Create configmap.yaml**

Loki SingleBinary mode. Credentials are injected via `extraEnv` from the SealedSecret — the Loki config itself does not contain credential values. The AWS SDK picks up `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` automatically.

```yaml
# clusters/vollminlab-cluster/monitoring/loki/app/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: loki-values
  namespace: monitoring
  labels:
    app: loki
    env: production
    category: observability
data:
  values.yaml: |
    deploymentMode: SingleBinary

    loki:
      auth_enabled: false
      commonConfig:
        replication_factor: 1
      storage:
        type: s3
        s3:
          endpoint: minio.minio.svc.cluster.local:9000
          region: us-east-1
          bucketNames:
            chunks: loki
            ruler: loki
            admin: loki
          s3ForcePathStyle: true
          insecure: true
      schemaConfig:
        configs:
          - from: "2024-01-01"
            store: tsdb
            object_store: s3
            schema: v13
            index:
              prefix: index_
              period: 24h
      limits_config:
        retention_period: 720h

    singleBinary:
      replicas: 1
      resources:
        requests:
          cpu: 100m
          memory: 256Mi
        limits:
          cpu: 500m
          memory: 512Mi
      persistence:
        enabled: true
        storageClass: longhorn
        size: 2Gi
      podLabels:
        app: loki
        env: production
        category: observability
      extraEnv:
        - name: AWS_ACCESS_KEY_ID
          valueFrom:
            secretKeyRef:
              name: loki-minio-credentials
              key: access-key
        - name: AWS_SECRET_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: loki-minio-credentials
              key: secret-key

    chunksCache:
      enabled: false

    resultsCache:
      enabled: false

    backend:
      replicas: 0

    read:
      replicas: 0

    write:
      replicas: 0

    gateway:
      enabled: false

    test:
      enabled: false

    monitoring:
      selfMonitoring:
        enabled: false
        grafanaAgent:
          installOperator: false
      lokiCanary:
        enabled: false
```

- [ ] **Step 3: Create kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/monitoring/loki/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: loki-deployment
  namespace: flux-system
resources:
  - helmrelease.yaml
  - configmap.yaml
  - loki-minio-sealedsecret.yaml
```

### Task 10: Promtail HelmRelease, ConfigMap, and kustomization

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/promtail/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/promtail/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/promtail/app/kustomization.yaml`

- [ ] **Step 1: Create helmrelease.yaml**

Replace `<PROMTAIL_CHART_VERSION>` with the version you found in Task 3 Step 2:

```yaml
# clusters/vollminlab-cluster/monitoring/promtail/app/helmrelease.yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: promtail
  namespace: monitoring
  labels:
    app: promtail
    env: production
    category: observability
spec:
  interval: 5m
  releaseName: promtail
  chart:
    spec:
      chart: promtail
      version: <PROMTAIL_CHART_VERSION>
      sourceRef:
        kind: HelmRepository
        name: grafana-repo
        namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: promtail-values
      valuesKey: values.yaml
```

- [ ] **Step 2: Create configmap.yaml**

```yaml
# clusters/vollminlab-cluster/monitoring/promtail/app/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: promtail-values
  namespace: monitoring
  labels:
    app: promtail
    env: production
    category: observability
data:
  values.yaml: |
    config:
      clients:
        - url: http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/push

    tolerations:
      - key: dmz
        operator: Equal
        value: "true"
        effect: NoSchedule
      - key: node-role.kubernetes.io/control-plane
        operator: Exists
        effect: NoSchedule

    resources:
      requests:
        cpu: 50m
        memory: 128Mi
      limits:
        cpu: 200m
        memory: 256Mi

    podLabels:
      app: promtail
      env: production
      category: observability
```

- [ ] **Step 3: Create kustomization.yaml**

```yaml
# clusters/vollminlab-cluster/monitoring/promtail/app/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: promtail-deployment
  namespace: flux-system
resources:
  - helmrelease.yaml
  - configmap.yaml
```

### Task 11: Update monitoring namespace kustomization

**Files:**
- Modify: `clusters/vollminlab-cluster/monitoring/kustomization.yaml`

- [ ] **Step 1: Update the file to list all three apps**

The current file only lists `namespace.yaml`. Replace it with:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: monitoring
resources:
  - namespace.yaml
  - ./kube-prometheus-stack/app
  - ./loki/app
  - ./promtail/app
```

### Task 12: Commit all PR 2 files

- [ ] **Step 1: Stage all new and modified files explicitly**

```bash
git add \
  clusters/vollminlab-cluster/flux-system/repositories/prometheus-community-helmrepository.yaml \
  clusters/vollminlab-cluster/flux-system/repositories/grafana-helmrepository.yaml \
  clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml \
  clusters/vollminlab-cluster/monitoring/kustomization.yaml \
  clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/helmrelease.yaml \
  clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml \
  clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/ingress.yaml \
  clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/alertmanager-sealedsecret.yaml \
  clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/grafana-admin-sealedsecret.yaml \
  clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/kustomization.yaml \
  clusters/vollminlab-cluster/monitoring/loki/app/helmrelease.yaml \
  clusters/vollminlab-cluster/monitoring/loki/app/configmap.yaml \
  clusters/vollminlab-cluster/monitoring/loki/app/loki-minio-sealedsecret.yaml \
  clusters/vollminlab-cluster/monitoring/loki/app/kustomization.yaml \
  clusters/vollminlab-cluster/monitoring/promtail/app/helmrelease.yaml \
  clusters/vollminlab-cluster/monitoring/promtail/app/configmap.yaml \
  clusters/vollminlab-cluster/monitoring/promtail/app/kustomization.yaml
```

- [ ] **Step 2: Verify no plain secrets are staged**

```bash
git diff --cached | grep -E "kind: Secret|password:|token:" | grep -v "SealedSecret\|secretKeyRef\|existingSecret\|configSecret"
```

Expected: no output (zero matches means nothing suspicious staged).

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(monitoring): deploy kube-prometheus-stack, Loki, Promtail observability stack"
```

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin <branch-name>
gh pr create --title "feat(monitoring): observability stack — kube-prometheus-stack + Loki + Promtail" --body "$(cat <<'EOF'
## Summary
- Adds kube-prometheus-stack (Prometheus + Grafana + Alertmanager) to monitoring namespace
- Adds Loki (SingleBinary, MinIO-backed) for log storage
- Adds Promtail DaemonSet on all nodes (including DMZ + control-plane)
- Registers prometheus-community and grafana HelmRepositories in Flux
- Alertmanager → PushOver via SealedSecret
- Loki MinIO credentials via SealedSecret (namespace-scoped re-seal of minio credentials)
- Grafana admin password via SealedSecret (never in ConfigMap)
- Pre-requisite: PR 1 (loki bucket) must be merged and MinIO reconciled first

## Test plan
- [ ] `flux get helmreleases -n monitoring` — all three HelmReleases show Ready
- [ ] `kubectl get pods -n monitoring` — all pods Running
- [ ] `kubectl get pvc -n monitoring` — prometheus (5Gi) and alertmanager (1Gi) PVCs Bound
- [ ] `kubectl get pvc -n monitoring` — loki WAL PVC (2Gi) Bound
- [ ] Grafana reachable at https://grafana.vollminlab.com (login with admin credentials from 1Password)
- [ ] Grafana → Explore → Loki data source → run a label query `{namespace="monitoring"}` — logs appear
- [ ] Grafana → Explore → Prometheus data source — metrics query `up` returns results
- [ ] PushOver notification received when a test alert fires (optional: `amtool alert add --alertmanager.url=...`)
- [ ] `vollm.in/grafana` short URL resolves correctly
EOF
)"
```

---

## PR 3 — ECK Removal (defer until Loki confirmed healthy)

**Do this only after:**
1. Grafana is accessible
2. Log query `{namespace="monitoring"}` returns data in Loki
3. Loki has been running at least 24 hours without errors

### Task 13: Remove ECK

**Files:**
- Delete: `clusters/vollminlab-cluster/elastic-system/` (entire directory)
- Delete: `clusters/vollminlab-cluster/flux-system/repositories/elastic-helmrepository.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`

- [ ] **Step 1: Remove elastic-system directory**

```bash
git rm -r clusters/vollminlab-cluster/elastic-system/
```

- [ ] **Step 2: Remove elastic HelmRepository file**

```bash
git rm clusters/vollminlab-cluster/flux-system/repositories/elastic-helmrepository.yaml
```

- [ ] **Step 3: Remove elastic-system from flux-kustomizations index**

In `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`, remove this line:

```yaml
  - elastic-system-kustomization.yaml
```

- [ ] **Step 4: Remove elastic-helmrepository from repositories index**

In `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`, remove this line:

```yaml
  - elastic-helmrepository.yaml
```

- [ ] **Step 5: Delete the elastic-system Flux Kustomization CR file**

```bash
git rm clusters/vollminlab-cluster/flux-system/flux-kustomizations/elastic-system-kustomization.yaml
```

- [ ] **Step 6: Commit**

```bash
git add clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml \
        clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml
git commit -m "chore(elastic-system): remove ECK operator — replaced by Loki"
```

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin <branch-name>
gh pr create --title "chore(elastic-system): remove ECK — replaced by Loki" --body "$(cat <<'EOF'
## Summary
- Deletes elastic-system namespace, eck-operator HelmRelease, and elastic HelmRepository
- Flux prune will garbage-collect the eck-operator and elastic-system namespace in-cluster
- Pre-requisite: Loki confirmed healthy (logs flowing in Grafana for 24h+)

## Test plan
- [ ] `flux get helmreleases -A` — eck-operator no longer appears
- [ ] `kubectl get ns` — elastic-system namespace no longer exists
- [ ] Grafana Loki still shows logs after ECK is gone
EOF
)"
```

---

## Self-Review Checklist

### Spec coverage

| Spec requirement | Task |
|---|---|
| MinIO loki bucket | Task 1 |
| kube-prometheus-stack HelmRepository | Task 2 |
| Grafana HelmRepository (shared with Promtail) | Task 3 |
| Both repos in flux-system/repositories/kustomization.yaml | Task 4 |
| Alertmanager SealedSecret (PushOver) | Task 5 |
| Grafana admin secret (required by secrets.md, not explicit in spec) | Task 6 |
| kube-prometheus-stack HelmRelease, ConfigMap | Task 7 |
| Prometheus: 30s scrape, 15d retention, 5Gi Longhorn PVC | Task 7 configmap |
| Grafana: ingress at grafana.vollminlab.com, wildcard-tls | Tasks 7 ingress |
| Grafana: Loki data source pre-wired | Task 7 configmap `additionalDataSources` |
| Alertmanager: PushOver config via configSecret | Task 7 configmap |
| Alertmanager: 1Gi Longhorn PVC | Task 7 configmap |
| Node exporter: DMZ + control-plane tolerations | Task 7 configmap |
| serviceMonitorSelectorNilUsesHelmValues: false (scrape all ServiceMonitors) | Task 7 configmap |
| Loki MinIO credentials SealedSecret | Task 8 |
| Loki HelmRelease: SingleBinary, MinIO S3 backend, 2Gi WAL PVC | Task 9 |
| Loki: 30-day retention | Task 9 configmap |
| Loki: no Ingress | ✓ (none created) |
| Promtail: DaemonSet all nodes, DMZ + control-plane tolerations | Task 10 |
| monitoring/kustomization.yaml updated | Task 11 |
| flux-system/repositories/kustomization.yaml updated | Task 4 |
| flux-system/flux-kustomizations/kustomization.yaml — monitoring already present | ✓ (verified in existing file, no change needed) |
| ECK removal (deferred) | Task 13 |
| shlink annotation on Grafana ingress | Task 7 ingress |
| All resources have app/env/category labels | ✓ (all files include required labels) |

### Potential gotchas

- **Longhorn capacity:** Before this PR merges, verify Longhorn can schedule 5Gi (Prometheus) + 1Gi (Alertmanager) + 2Gi (Loki WAL) = 8Gi PVCs. With 3 replicas: ~24Gi needed across the cluster. Check: `kubectl get nodes -o custom-columns='NAME:.metadata.name,SCHEDULABLE:.metadata.annotations.node\.longhorn\.io/longhorn-schedulable-storage'`
- **kube-prometheus-stack CRDs:** The chart installs Prometheus/Alertmanager CRDs on first deploy. If Flux times out on first reconcile (2m timeout set), force-reconcile once CRDs are installed: `flux reconcile helmrelease kube-prometheus-stack -n monitoring --with-source`
- **Grafana service name:** The ingress backend uses `kube-prometheus-stack-grafana` — this assumes `fullnameOverride: kube-prometheus-stack` in the chart values. If the service name differs, check with `kubectl get svc -n monitoring` after first deploy and update `ingress.yaml`.
