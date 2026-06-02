# CNPG Native Backups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add WAL archiving and daily scheduled base backups to all three CNPG clusters (authentik-db, harbor-db, shlink-db) using MinIO as the barman object store.

**Architecture:** Each CNPG Cluster CR gets a `backup.barmanObjectStore` spec pointing at a new `cnpg-backups` MinIO bucket. A scoped MinIO service account (`cnpg-svc`) is provisioned via the MinIO Helm chart's `users` + `policies` values, so no manual console steps are required. Each Kubernetes namespace gets its own `cnpg-minio-credentials` SealedSecret containing the same access key + secret key, because SealedSecrets are namespace-scoped. A `ScheduledBackup` CR in each namespace triggers a daily base backup at 1 AM UTC (before Velero's 2 AM run).

**Tech Stack:** CloudNativePG v1.24 (chart 0.28.0), Bitnami MinIO v5.4.0, kubeseal, SealedSecrets

---

## Pre-flight checks

Before starting, verify the live cluster is healthy so you have a clean baseline:

```bash
kubectl get clusters.postgresql.cnpg.io -A
# Expected: authentik-db, harbor-db, shlink-db all READY with no conditions

kubectl get helmreleases -n minio
# Expected: minio READY=True

kubectl get pods -n minio
# Expected: minio-0 Running
```

---

## File Map

| File | Action |
|---|---|
| `clusters/vollminlab-cluster/minio/minio/app/configmap.yaml` | Add bucket, policy, user to MinIO values |
| `clusters/vollminlab-cluster/minio/minio/app/cnpg-minio-user-sealedsecret.yaml` | New — sealed secretKey for cnpg-svc user in minio namespace |
| `clusters/vollminlab-cluster/minio/minio/app/kustomization.yaml` | Add new SealedSecret |
| `clusters/vollminlab-cluster/authentik/cnpg/app/cluster.yaml` | Add backup spec |
| `clusters/vollminlab-cluster/authentik/cnpg/app/scheduled-backup.yaml` | New — ScheduledBackup CR |
| `clusters/vollminlab-cluster/authentik/cnpg/app/cnpg-minio-credentials-sealedsecret.yaml` | New — sealed access+secret key for authentik namespace |
| `clusters/vollminlab-cluster/authentik/cnpg/app/kustomization.yaml` | Add new files |
| `clusters/vollminlab-cluster/harbor/harbor-db/app/cluster.yaml` | Add backup spec |
| `clusters/vollminlab-cluster/harbor/harbor-db/app/scheduled-backup.yaml` | New — ScheduledBackup CR |
| `clusters/vollminlab-cluster/harbor/harbor-db/app/cnpg-minio-credentials-sealedsecret.yaml` | New — sealed access+secret key for harbor namespace |
| `clusters/vollminlab-cluster/harbor/harbor-db/app/kustomization.yaml` | Add new files |
| `clusters/vollminlab-cluster/shlink/shlink-db/app/cluster.yaml` | Add backup spec |
| `clusters/vollminlab-cluster/shlink/shlink-db/app/scheduled-backup.yaml` | New — ScheduledBackup CR |
| `clusters/vollminlab-cluster/shlink/shlink-db/app/cnpg-minio-credentials-sealedsecret.yaml` | New — sealed access+secret key for shlink namespace |
| `clusters/vollminlab-cluster/shlink/shlink-db/app/kustomization.yaml` | Add new files |

---

## Task 1: Generate and store the cnpg-svc secret key

This step happens entirely outside the repo. The value you generate here will be used in Tasks 2 and 4.

- [ ] **Step 1: Generate a secure random secret key**

```bash
CNPG_SECRET_KEY=$(openssl rand -base64 32)
echo "ACCESS KEY: cnpg-svc"
echo "SECRET KEY: $CNPG_SECRET_KEY"
```

- [ ] **Step 2: Save to 1Password**

Open the 1Password Homelab vault. Create a new Login item named **"MinIO cnpg-svc access key"** with:
- Username: `cnpg-svc`
- Password: the value of `$CNPG_SECRET_KEY`

Do NOT close this terminal session — you will need `$CNPG_SECRET_KEY` in Tasks 2 and 4. If the session dies, retrieve the value from 1Password.

---

## Task 2: Add cnpg-svc user provisioning to MinIO Helm values

The MinIO chart's provisioning job will create the user, policy, and bucket automatically on the next Helm upgrade. The access key (`cnpg-svc`) is not sensitive and lives in the ConfigMap. The secret key lives in a SealedSecret in the minio namespace.

- [ ] **Step 1: Fetch the sealing certificate**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem
```

- [ ] **Step 2: Create and seal the cnpg-svc secret key for the minio namespace**

Replace `<SECRET_KEY>` with the value of `$CNPG_SECRET_KEY` from Task 1.

```bash
kubectl create secret generic cnpg-minio-user \
  -n minio \
  --from-literal=secretKey=<SECRET_KEY> \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/minio/minio/app/cnpg-minio-user-sealedsecret.yaml
```

- [ ] **Step 3: Update MinIO configmap.yaml to provision the bucket, policy, and user**

Modify `clusters/vollminlab-cluster/minio/minio/app/configmap.yaml`. Add three new sections to the `values.yaml` block: `policies`, `users`, and a new entry in `buckets`. The full file after edits:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: minio-values
  namespace: minio
data:
  values.yaml: |
    mode: standalone
    replicas: 1

    deploymentUpdate:
      type: Recreate

    existingSecret: "minio-credentials"

    podLabels:
      env: production
      category: storage

    resources:
      requests:
        cpu: 150m
        memory: 512Mi
      limits:
        cpu: 1000m
        memory: 1Gi

    persistence:
      enabled: true
      storageClass: "longhorn"
      size: 60Gi

    service:
      type: ClusterIP
      port: "9000"

    consoleService:
      type: ClusterIP
      port: "9001"

    policies:
      - name: cnpg-policy
        statements:
          - resources:
              - "arn:aws:s3:::cnpg-backups"
              - "arn:aws:s3:::cnpg-backups/*"
            actions:
              - "s3:GetObject"
              - "s3:PutObject"
              - "s3:DeleteObject"
              - "s3:ListBucket"
              - "s3:GetBucketLocation"

    users:
      - accessKey: cnpg-svc
        existingSecret: cnpg-minio-user
        existingSecretKey: secretKey
        policy: cnpg-policy

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
      - name: cnpg-backups
        policy: none
        purge: false
        versioning: false
        objectlocking: false

    oidc:
      enabled: true
      configUrl: "https://authentik.vollminlab.com/application/o/minio/.well-known/openid-configuration"
      clientId: "GKq5oNsz9lgsa1kIOCM7uTa4qIBVe6SUsfVjeFCN" # gitleaks:allow
      existingClientSecretName: "minio-oidc-credentials"
      existingClientSecretKey: "MINIO_IDENTITY_OPENID_CLIENT_SECRET"
      claimName: "policy"
      scopes: "openid,profile,email"
      redirectUri: "https://minio.vollminlab.com/oauth_callback"
      displayName: "Authentik"

    extraEnvVars:
      - name: MINIO_IDENTITY_OPENID_PKCE_ENABLED
        value: "on"
      - name: MINIO_PROMETHEUS_AUTH_TYPE
        value: "public"

    makeBucketJob:
      resources:
        requests:
          cpu: 250m
          memory: 128Mi
        limits:
          cpu: 500m
          memory: 256Mi
```

- [ ] **Step 4: Add the new SealedSecret to the minio app kustomization**

`clusters/vollminlab-cluster/minio/minio/app/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - helmrelease.yaml
  - configmap.yaml
  - ingress.yaml
  - ingress-s3.yaml
  - cnpg-minio-user-sealedsecret.yaml
  - minio-credentials-sealedsecret.yaml
  - minio-oidc-sealedsecret.yaml
  - servicemonitor.yaml
```

- [ ] **Step 5: Commit**

```bash
git add \
  clusters/vollminlab-cluster/minio/minio/app/configmap.yaml \
  clusters/vollminlab-cluster/minio/minio/app/cnpg-minio-user-sealedsecret.yaml \
  clusters/vollminlab-cluster/minio/minio/app/kustomization.yaml
git commit -m "chore(minio): provision cnpg-svc user, cnpg-policy, and cnpg-backups bucket"
```

---

## Task 3: Seal cnpg-minio-credentials for all three app namespaces

Each namespace needs its own SealedSecret containing both the access key (`cnpg-svc`) and secret key. SealedSecrets are namespace-scoped — you cannot share one across namespaces.

Replace `<SECRET_KEY>` with the value of `$CNPG_SECRET_KEY` from Task 1.

- [ ] **Step 1: Seal for the authentik namespace**

```bash
kubectl create secret generic cnpg-minio-credentials \
  -n authentik \
  --from-literal=ACCESS_KEY_ID=cnpg-svc \
  --from-literal=ACCESS_SECRET_KEY=<SECRET_KEY> \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/authentik/cnpg/app/cnpg-minio-credentials-sealedsecret.yaml
```

- [ ] **Step 2: Seal for the harbor namespace**

```bash
kubectl create secret generic cnpg-minio-credentials \
  -n harbor \
  --from-literal=ACCESS_KEY_ID=cnpg-svc \
  --from-literal=ACCESS_SECRET_KEY=<SECRET_KEY> \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/harbor/harbor-db/app/cnpg-minio-credentials-sealedsecret.yaml
```

- [ ] **Step 3: Seal for the shlink namespace**

```bash
kubectl create secret generic cnpg-minio-credentials \
  -n shlink \
  --from-literal=ACCESS_KEY_ID=cnpg-svc \
  --from-literal=ACCESS_SECRET_KEY=<SECRET_KEY> \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/shlink/shlink-db/app/cnpg-minio-credentials-sealedsecret.yaml
```

- [ ] **Step 4: Clean up the cert file**

```bash
rm /tmp/pub-cert.pem
```

---

## Task 4: Add backup spec and ScheduledBackup for authentik-db

- [ ] **Step 1: Update cluster.yaml with backup spec**

Replace the full contents of `clusters/vollminlab-cluster/authentik/cnpg/app/cluster.yaml`:

```yaml
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
  backup:
    barmanObjectStore:
      destinationPath: "s3://cnpg-backups/authentik-db"
      endpointURL: "http://minio.minio.svc.cluster.local:9000"
      s3Credentials:
        accessKeyId:
          name: cnpg-minio-credentials
          key: ACCESS_KEY_ID
        secretAccessKey:
          name: cnpg-minio-credentials
          key: ACCESS_SECRET_KEY
      wal:
        compression: gzip
        maxParallel: 2
    retentionPolicy: "30d"
```

- [ ] **Step 2: Create scheduled-backup.yaml**

Create `clusters/vollminlab-cluster/authentik/cnpg/app/scheduled-backup.yaml`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: authentik-db-backup
  namespace: authentik
  labels:
    app: authentik-db
    env: production
    category: security
spec:
  schedule: "0 1 * * *"
  backupOwnerReference: self
  cluster:
    name: authentik-db
```

- [ ] **Step 3: Update kustomization.yaml to include new files**

`clusters/vollminlab-cluster/authentik/cnpg/app/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: authentik-cnpg-app
resources:
  - cluster.yaml
  - authentik-db-credentials-sealedsecret.yaml
  - cnpg-minio-credentials-sealedsecret.yaml
  - scheduled-backup.yaml
```

- [ ] **Step 4: Commit**

```bash
git add \
  clusters/vollminlab-cluster/authentik/cnpg/app/cluster.yaml \
  clusters/vollminlab-cluster/authentik/cnpg/app/scheduled-backup.yaml \
  clusters/vollminlab-cluster/authentik/cnpg/app/cnpg-minio-credentials-sealedsecret.yaml \
  clusters/vollminlab-cluster/authentik/cnpg/app/kustomization.yaml
git commit -m "feat(authentik): add CNPG backup to MinIO with WAL archiving"
```

---

## Task 5: Add backup spec and ScheduledBackup for harbor-db

- [ ] **Step 1: Update cluster.yaml with backup spec**

Replace the full contents of `clusters/vollminlab-cluster/harbor/harbor-db/app/cluster.yaml`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: harbor-db
  namespace: harbor
  labels:
    app: harbor-db
    env: production
    category: storage
spec:
  instances: 2
  inheritedMetadata:
    labels:
      app: harbor-db
      env: production
      category: storage
  storage:
    size: 10Gi
    storageClass: longhorn
  bootstrap:
    initdb:
      database: registry
      owner: harbor
      secret:
        name: harbor-db-credentials
      postInitSQL:
        - GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO harbor
        - GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO harbor
        - ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO harbor
        - ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO harbor
  backup:
    barmanObjectStore:
      destinationPath: "s3://cnpg-backups/harbor-db"
      endpointURL: "http://minio.minio.svc.cluster.local:9000"
      s3Credentials:
        accessKeyId:
          name: cnpg-minio-credentials
          key: ACCESS_KEY_ID
        secretAccessKey:
          name: cnpg-minio-credentials
          key: ACCESS_SECRET_KEY
      wal:
        compression: gzip
        maxParallel: 2
    retentionPolicy: "30d"
```

- [ ] **Step 2: Create scheduled-backup.yaml**

Create `clusters/vollminlab-cluster/harbor/harbor-db/app/scheduled-backup.yaml`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: harbor-db-backup
  namespace: harbor
  labels:
    app: harbor-db
    env: production
    category: storage
spec:
  schedule: "15 1 * * *"
  backupOwnerReference: self
  cluster:
    name: harbor-db
```

- [ ] **Step 3: Update kustomization.yaml to include new files**

`clusters/vollminlab-cluster/harbor/harbor-db/app/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - cluster.yaml
  - harbor-db-credentials-sealedsecret.yaml
  - cnpg-minio-credentials-sealedsecret.yaml
  - scheduled-backup.yaml
```

- [ ] **Step 4: Commit**

```bash
git add \
  clusters/vollminlab-cluster/harbor/harbor-db/app/cluster.yaml \
  clusters/vollminlab-cluster/harbor/harbor-db/app/scheduled-backup.yaml \
  clusters/vollminlab-cluster/harbor/harbor-db/app/cnpg-minio-credentials-sealedsecret.yaml \
  clusters/vollminlab-cluster/harbor/harbor-db/app/kustomization.yaml
git commit -m "feat(harbor): add CNPG backup to MinIO with WAL archiving"
```

---

## Task 6: Add backup spec and ScheduledBackup for shlink-db

- [ ] **Step 1: Update cluster.yaml with backup spec**

Replace the full contents of `clusters/vollminlab-cluster/shlink/shlink-db/app/cluster.yaml`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: shlink-db
  namespace: shlink
  labels:
    app: shlink-db
    env: production
    category: apps
spec:
  instances: 1
  inheritedMetadata:
    labels:
      app: shlink-db
      env: production
      category: apps
  storage:
    size: 5Gi
    storageClass: longhorn
  bootstrap:
    initdb:
      database: shlink
      owner: shlink
      secret:
        name: shlink-db-credentials
      postInitSQL:
        - GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO shlink
        - GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO shlink
        - ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO shlink
        - ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO shlink
  backup:
    barmanObjectStore:
      destinationPath: "s3://cnpg-backups/shlink-db"
      endpointURL: "http://minio.minio.svc.cluster.local:9000"
      s3Credentials:
        accessKeyId:
          name: cnpg-minio-credentials
          key: ACCESS_KEY_ID
        secretAccessKey:
          name: cnpg-minio-credentials
          key: ACCESS_SECRET_KEY
      wal:
        compression: gzip
        maxParallel: 2
    retentionPolicy: "30d"
```

- [ ] **Step 2: Create scheduled-backup.yaml**

Create `clusters/vollminlab-cluster/shlink/shlink-db/app/scheduled-backup.yaml`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: shlink-db-backup
  namespace: shlink
  labels:
    app: shlink-db
    env: production
    category: apps
spec:
  schedule: "30 1 * * *"
  backupOwnerReference: self
  cluster:
    name: shlink-db
```

- [ ] **Step 3: Update kustomization.yaml to include new files**

`clusters/vollminlab-cluster/shlink/shlink-db/app/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - cluster.yaml
  - shlink-db-credentials-sealedsecret.yaml
  - cnpg-minio-credentials-sealedsecret.yaml
  - scheduled-backup.yaml
```

- [ ] **Step 4: Commit**

```bash
git add \
  clusters/vollminlab-cluster/shlink/shlink-db/app/cluster.yaml \
  clusters/vollminlab-cluster/shlink/shlink-db/app/scheduled-backup.yaml \
  clusters/vollminlab-cluster/shlink/shlink-db/app/cnpg-minio-credentials-sealedsecret.yaml \
  clusters/vollminlab-cluster/shlink/shlink-db/app/kustomization.yaml
git commit -m "feat(shlink): add CNPG backup to MinIO with WAL archiving"
```

---

## Task 7: Open PR and verify CI passes

- [ ] **Step 1: Push the branch and open a PR**

```bash
git push -u origin <branch-name>
gh pr create --title "feat: add native CNPG backups to MinIO for all Postgres clusters" --body "$(cat <<'EOF'
## Summary

- Adds WAL archiving and daily scheduled base backups for authentik-db, harbor-db, and shlink-db
- Provisions a scoped `cnpg-svc` MinIO user via Helm chart with access limited to the `cnpg-backups` bucket
- Each app namespace gets a namespace-scoped `cnpg-minio-credentials` SealedSecret
- Backups scheduled at 1:00, 1:15, and 1:30 AM UTC (before Velero's 2 AM run)
- 30-day retention policy on all clusters

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Verify CI passes**

All three CI checks must be green before proceeding:
- kubeconform schema validation
- gitleaks secret scanning
- kyverno policy validation

If kubeconform fails on `ScheduledBackup` kind, add it to the skip list in `.github/workflows/ci.yaml` alongside `HelmRelease|OCIRepository|...` — CNPG CRDs are not in the kubeconform default schema catalog.

---

## Task 8: Post-merge verification (run after Flux reconciles, ~10 min after merge)

- [ ] **Step 1: Check all CNPG clusters are still healthy**

```bash
kubectl get clusters.postgresql.cnpg.io -A
```

Expected output — all clusters show `READY: True` and `INSTANCES: 1` or `2` with no new conditions. If a cluster shows `Degraded`, check:

```bash
kubectl describe cluster authentik-db -n authentik | grep -A 10 "Conditions:"
kubectl logs -n authentik $(kubectl get pods -n authentik -l cnpg.io/cluster=authentik-db -o name | head -1) --tail=50
```

- [ ] **Step 2: Verify MinIO provisioning ran**

```bash
kubectl get jobs -n minio
```

Look for a provisioning Job that completed. If it failed, check its logs:

```bash
kubectl logs -n minio -l app.kubernetes.io/name=minio --container minio-make-user | tail -30
```

- [ ] **Step 3: Verify the cnpg-backups bucket and cnpg-svc user exist**

```bash
kubectl exec -n minio $(kubectl get pods -n minio -l app=minio -o jsonpath='{.items[0].metadata.name}') \
  -- mc ls local/cnpg-backups/ 2>/dev/null || echo "bucket empty (expected before first backup)"

kubectl exec -n minio $(kubectl get pods -n minio -l app=minio -o jsonpath='{.items[0].metadata.name}') \
  -- mc admin user info local cnpg-svc
```

Expected: user `cnpg-svc` exists with policy `cnpg-policy`.

- [ ] **Step 4: Trigger an immediate test backup on authentik-db to verify end-to-end**

```bash
kubectl cnpg backup authentik-db -n authentik
```

Wait ~30 seconds, then check:

```bash
kubectl get backups.postgresql.cnpg.io -n authentik
```

Expected: one Backup object showing `PHASE: completed`. If it shows `failed`, check:

```bash
kubectl describe backup -n authentik $(kubectl get backups.postgresql.cnpg.io -n authentik -o name | head -1)
```

Common failure causes:
- `AccessDenied` → cnpg-svc policy doesn't include the right S3 actions — verify policy in MinIO console
- `NoSuchBucket` → MinIO provisioning Job didn't run yet, or failed — check Job logs
- `connection refused` → endpointURL is wrong; should be `http://minio.minio.svc.cluster.local:9000`

- [ ] **Step 5: Verify WAL archiving is active on authentik-db**

```bash
kubectl get cluster authentik-db -n authentik \
  -o jsonpath='{.status.conditions}' | python3 -m json.tool
```

Look for a condition with `type: ContinuousArchiving` and `status: True`. This confirms WAL segments are being shipped to MinIO.

- [ ] **Step 6: Verify ScheduledBackups are present on all three clusters**

```bash
kubectl get scheduledbackups.postgresql.cnpg.io -A
```

Expected: three ScheduledBackups (authentik-db-backup, harbor-db-backup, shlink-db-backup), all with `SUSPENDED: false`.

- [ ] **Step 7: Confirm first scheduled backups run successfully**

Check after 1:30 AM UTC the next day:

```bash
kubectl get backups.postgresql.cnpg.io -A --sort-by=.metadata.creationTimestamp
```

Expected: three Backup objects, all `PHASE: completed`.
