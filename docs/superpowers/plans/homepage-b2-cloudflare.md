# Homepage B2 + Cloudflare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Backblaze B2 bucket metrics Prometheus exporter and a Cloudflare widget to the Homepage dashboard, with B2 metrics also available in Grafana.

**Architecture:** A custom Python Deployment in the `monitoring` namespace queries the B2 API every 30 minutes and caches bucket size + file count as Prometheus metrics. Homepage reads these via its native `prometheus` widget type. A Cloudflare entry uses Homepage's native `cloudflare` widget. Both credentials come from SealedSecrets.

**Tech Stack:** Python 3.12, b2sdk, prometheus_client, Kubernetes, Flux CD, SealedSecrets, kube-prometheus-stack ServiceMonitor, Homepage v1.13.x

---

## File Map

**New files:**
```
build/b2-exporter/
  exporter.py          — Python exporter: B2 listing + Prometheus metrics server
  exporter_test.py     — Unit tests for bucket stats logic
  Dockerfile           — python:3.12-slim, pins b2sdk + prometheus_client
  requirements.txt     — pinned dependencies

clusters/vollminlab-cluster/monitoring/b2-exporter/app/
  kustomization.yaml   — lists all resources in this dir
  deployment.yaml      — single-replica Deployment, env from SealedSecret
  service.yaml         — ClusterIP, port 8080, named port "metrics"
  servicemonitor.yaml  — scrape /metrics every 60s
  b2-exporter-credentials-sealedsecret.yaml — B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET_NAME
```

**Modified files:**
```
clusters/vollminlab-cluster/monitoring/kustomization.yaml
  — add "- b2-exporter/app"

clusters/vollminlab-cluster/homepage/homepage/app/configmap.yaml
  — add Backblaze entry to Infrastructure section (prometheus widget)
  — add Cloudflare entry to Networking section
  — change Networking columns: 5 → 3
  — add HOMEPAGE_VAR_CLOUDFLARE_API_TOKEN env var

clusters/vollminlab-cluster/homepage/homepage/app/homepage-env-vars-sealedsecret.yaml
  — re-sealed with added CLOUDFLARE_API_TOKEN key
```

---

### Task 1: Write Python exporter and unit tests

**Files:**
- Create: `build/b2-exporter/exporter.py`
- Create: `build/b2-exporter/exporter_test.py`
- Create: `build/b2-exporter/requirements.txt`

- [ ] **Step 1: Create requirements.txt**

```
b2sdk==2.7.0
prometheus_client==0.21.1
```

Verify these are current: `pip index versions b2sdk` and `pip index versions prometheus_client`.
If newer patch versions exist, use them. Never use unpinned ranges here.

- [ ] **Step 2: Create exporter.py**

```python
#!/usr/bin/env python3
import os
import time
import logging
from prometheus_client import start_http_server, Gauge
import b2sdk.v2 as b2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BUCKET_BYTES = Gauge("b2_bucket_bytes_total", "Total bytes stored in B2 bucket", ["bucket"])
BUCKET_FILES = Gauge("b2_bucket_file_count_total", "Total file versions in B2 bucket", ["bucket"])

KEY_ID = os.environ["B2_APPLICATION_KEY_ID"]
APP_KEY = os.environ["B2_APPLICATION_KEY"]
BUCKET_NAME = os.environ["B2_BUCKET_NAME"]
INTERVAL = int(os.environ.get("B2_REFRESH_INTERVAL", "1800"))


def get_bucket_stats(bucket) -> tuple[int, int]:
    total_bytes = 0
    total_files = 0
    for file_version, _ in bucket.ls(recursive=True, latest_only=False):
        total_bytes += file_version.size
        total_files += 1
    return total_bytes, total_files


def run_loop():
    while True:
        try:
            info = b2.InMemoryAccountInfo()
            api = b2.B2Api(info)
            api.authorize_account("production", KEY_ID, APP_KEY)
            bucket = api.get_bucket_by_name(BUCKET_NAME)
            total_bytes, total_files = get_bucket_stats(bucket)
            BUCKET_BYTES.labels(bucket=BUCKET_NAME).set(total_bytes)
            BUCKET_FILES.labels(bucket=BUCKET_NAME).set(total_files)
            log.info("updated: %d bytes, %d files in %s", total_bytes, total_files, BUCKET_NAME)
        except Exception as e:
            log.error("failed to update metrics: %s", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    start_http_server(8080)
    log.info("metrics server listening on :8080")
    run_loop()
```

- [ ] **Step 3: Write failing tests for get_bucket_stats**

```python
# build/b2-exporter/exporter_test.py
from unittest.mock import MagicMock
import pytest
import exporter


def _make_file(size: int):
    f = MagicMock()
    f.size = size
    return f


def test_get_bucket_stats_sums_all_file_sizes():
    mock_bucket = MagicMock()
    mock_bucket.ls.return_value = [
        (_make_file(1000), None),
        (_make_file(2500), None),
        (_make_file(500), None),
    ]
    total_bytes, total_files = exporter.get_bucket_stats(mock_bucket)
    assert total_bytes == 4000
    assert total_files == 3
    mock_bucket.ls.assert_called_once_with(recursive=True, latest_only=False)


def test_get_bucket_stats_empty_bucket():
    mock_bucket = MagicMock()
    mock_bucket.ls.return_value = []
    total_bytes, total_files = exporter.get_bucket_stats(mock_bucket)
    assert total_bytes == 0
    assert total_files == 0


def test_get_bucket_stats_single_file():
    mock_bucket = MagicMock()
    mock_bucket.ls.return_value = [(_make_file(999999999), None)]
    total_bytes, total_files = exporter.get_bucket_stats(mock_bucket)
    assert total_bytes == 999999999
    assert total_files == 1
```

- [ ] **Step 4: Run tests (expect failure — exporter not importable yet)**

```bash
cd build/b2-exporter
pip install b2sdk==2.7.0 prometheus_client==0.21.1
python -m pytest exporter_test.py -v
```

Expected: tests pass immediately (mock-based, no I/O). If `ModuleNotFoundError`, ensure requirements are installed.

- [ ] **Step 5: Commit**

```bash
git add build/b2-exporter/
git commit -m "feat: add b2-exporter Python source and tests"
```

---

### Task 2: Build and push Docker image to Harbor

**Files:**
- Create: `build/b2-exporter/Dockerfile`

- [ ] **Step 1: Verify python:3.12-slim tag exists**

```bash
docker pull python:3.12-slim
```

Expected: image pulls successfully.

- [ ] **Step 2: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY exporter.py .

USER nobody

CMD ["python", "exporter.py"]
```

- [ ] **Step 3: Build the image**

```bash
docker build -t harbor.vollminlab.com/library/b2-exporter:1.0.0 build/b2-exporter/
```

Expected: build completes with no errors.

- [ ] **Step 4: Log in to Harbor and push**

```bash
docker login harbor.vollminlab.com
docker push harbor.vollminlab.com/library/b2-exporter:1.0.0
```

Expected: push completes, image appears in Harbor UI under `library/b2-exporter`.

- [ ] **Step 5: Commit Dockerfile**

```bash
git add build/b2-exporter/Dockerfile
git commit -m "feat: add b2-exporter Dockerfile"
```

---

### Task 3: Create B2 Application Key and seal credentials

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/b2-exporter/app/b2-exporter-credentials-sealedsecret.yaml`

- [ ] **Step 1: Create a scoped B2 Application Key**

In the Backblaze web console → App Keys → Add a New Application Key:
- Name: `vollminlab-b2-exporter`
- Bucket: restrict to the Velero bucket only (e.g. `vollminlab-velero`)
- Capabilities: `listFiles`, `readFiles`
- File name prefix: (leave blank)

Note the `keyID` and `applicationKey` — save both to 1Password as **"B2 Exporter App Key"** in the Homelab vault (fields: `keyID`, `applicationKey`).

- [ ] **Step 2: Note the Velero bucket name**

```bash
kubectl get backupstoragelocations.velero.io -n velero -o jsonpath='{.items[?(@.spec.provider=="aws")].spec.objectStorage.bucket}'
```

Expected output: the B2 bucket name (e.g. `vollminlab-velero`). If multiple BSLs exist, look for the one with `config.endpoint` matching Backblaze.

- [ ] **Step 3: Fetch the sealing certificate**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem
```

- [ ] **Step 4: Seal the credentials**

Replace `<KEY_ID>`, `<APP_KEY>`, and `<BUCKET>` with actual values from step 1 and 2:

```bash
kubectl create secret generic b2-exporter-credentials \
  -n monitoring \
  --from-literal=B2_APPLICATION_KEY_ID=<KEY_ID> \
  --from-literal=B2_APPLICATION_KEY=<APP_KEY> \
  --from-literal=B2_BUCKET_NAME=<BUCKET> \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/monitoring/b2-exporter/app/b2-exporter-credentials-sealedsecret.yaml
rm /tmp/pub-cert.pem
```

- [ ] **Step 5: Verify the SealedSecret filename matches metadata.name**

The file `b2-exporter-credentials-sealedsecret.yaml` must contain `metadata.name: b2-exporter-credentials`. Verify:

```bash
grep "name:" clusters/vollminlab-cluster/monitoring/b2-exporter/app/b2-exporter-credentials-sealedsecret.yaml | head -3
```

Expected: `name: b2-exporter-credentials` and `namespace: monitoring`.

- [ ] **Step 6: Commit**

```bash
git add clusters/vollminlab-cluster/monitoring/b2-exporter/app/b2-exporter-credentials-sealedsecret.yaml
git commit -m "feat: add b2-exporter SealedSecret for B2 credentials"
```

---

### Task 4: Create b2-exporter Kubernetes manifests

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/b2-exporter/app/deployment.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/b2-exporter/app/service.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/b2-exporter/app/servicemonitor.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/b2-exporter/app/kustomization.yaml`

- [ ] **Step 1: Create deployment.yaml**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: b2-exporter
  namespace: monitoring
  labels:
    app: b2-exporter
    env: production
    category: observability
spec:
  replicas: 1
  selector:
    matchLabels:
      app: b2-exporter
  template:
    metadata:
      labels:
        app: b2-exporter
        env: production
        category: observability
    spec:
      containers:
        - name: b2-exporter
          image: harbor.vollminlab.com/library/b2-exporter:1.0.0
          ports:
            - name: metrics
              containerPort: 8080
          env:
            - name: B2_APPLICATION_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: b2-exporter-credentials
                  key: B2_APPLICATION_KEY_ID
            - name: B2_APPLICATION_KEY
              valueFrom:
                secretKeyRef:
                  name: b2-exporter-credentials
                  key: B2_APPLICATION_KEY
            - name: B2_BUCKET_NAME
              valueFrom:
                secretKeyRef:
                  name: b2-exporter-credentials
                  key: B2_BUCKET_NAME
          resources:
            requests:
              cpu: 10m
              memory: 64Mi
            limits:
              cpu: 100m
              memory: 128Mi
```

- [ ] **Step 2: Create service.yaml**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: b2-exporter
  namespace: monitoring
  labels:
    app: b2-exporter
    env: production
    category: observability
spec:
  selector:
    app: b2-exporter
  ports:
    - name: metrics
      port: 8080
      targetPort: metrics
```

- [ ] **Step 3: Create servicemonitor.yaml**

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: b2-exporter
  namespace: monitoring
  labels:
    app: b2-exporter
    env: production
    category: observability
spec:
  selector:
    matchLabels:
      app: b2-exporter
  endpoints:
    - port: metrics
      path: /metrics
      interval: 60s
      scheme: http
```

- [ ] **Step 4: Create kustomization.yaml**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: b2-exporter
  labels:
    app: b2-exporter
    env: production
    category: observability
resources:
  - deployment.yaml
  - service.yaml
  - servicemonitor.yaml
  - b2-exporter-credentials-sealedsecret.yaml
```

- [ ] **Step 5: Add b2-exporter to monitoring namespace kustomization**

Edit `clusters/vollminlab-cluster/monitoring/kustomization.yaml`. Add `- b2-exporter/app` to the `resources` list:

```yaml
resources:
  - namespace.yaml
  - b2-exporter/app
  - kube-prometheus-stack/app
  - loki/app
  - promtail/app
```

- [ ] **Step 6: Commit**

```bash
git add clusters/vollminlab-cluster/monitoring/b2-exporter/
git add clusters/vollminlab-cluster/monitoring/kustomization.yaml
git commit -m "feat: deploy b2-exporter to monitoring namespace"
```

---

### Task 5: Verify exporter in Prometheus

> Run these steps after the full PR (Tasks 1–7) merges and Flux reconciles. See Task 8 for push/PR/merge steps.

- [ ] **Step 1: Verify Pod is running**

```bash
kubectl get pods -n monitoring -l app=b2-exporter
```

Expected: `b2-exporter-<hash>   1/1   Running`

If `ImagePullBackOff`: confirm the image tag exists in Harbor at `harbor.vollminlab.com/library/b2-exporter:1.0.0`.

- [ ] **Step 2: Check exporter logs**

```bash
kubectl logs -n monitoring -l app=b2-exporter --tail=20
```

Expected: lines like `updated: 12345678 bytes, 1234 files in vollminlab-velero`. If errors, check credentials in the SealedSecret resolved correctly.

- [ ] **Step 3: Verify metrics endpoint**

```bash
kubectl exec -n monitoring deploy/b2-exporter -- wget -qO- http://localhost:8080/metrics | grep b2_bucket
```

Expected output:
```
b2_bucket_bytes_total{bucket="vollminlab-velero"} 1.23e+10
b2_bucket_file_count_total{bucket="vollminlab-velero"} 1234.0
```

- [ ] **Step 4: Verify Prometheus is scraping**

In the Prometheus UI at `https://prometheus.vollminlab.com`, go to Status → Targets and search for `b2-exporter`. Expected: state `UP`.

Then run the query `b2_bucket_bytes_total` — it should return a result.

---

### Task 6: Update Homepage configmap — Backblaze and Cloudflare entries

**Files:**
- Modify: `clusters/vollminlab-cluster/homepage/homepage/app/configmap.yaml`

- [ ] **Step 1: Add Backblaze entry to the Infrastructure section**

In `configmap.yaml`, find the `Infrastructure` services list. Add after `Longhorn` (so it becomes the 8th entry, filling row 2):

```yaml
            - Backblaze:
                description: B2 offsite backup storage
                href: https://secure.backblaze.com/b2_buckets.htm
                icon: backblaze.png
                widget:
                  type: prometheus
                  url: http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090
                  query: b2_bucket_bytes_total
                  format:
                    type: bytes
                    scale: 1
                  label: "B2 Storage"
```

- [ ] **Step 2: Add Cloudflare entry to the Networking section**

In the `Networking` services list, add after `HAProxy DMZ Stats`:

```yaml
            - Cloudflare:
                description: DNS and CDN
                href: https://dash.cloudflare.com
                icon: cloudflare.png
                widget:
                  type: cloudflare
                  key: "{{HOMEPAGE_VAR_CLOUDFLARE_API_TOKEN}}"
```

- [ ] **Step 3: Change Networking column count from 5 to 3**

In the `settings.layout` section, change:

```yaml
          Networking:
            style: row
            columns: 3
```

(was `columns: 5`)

- [ ] **Step 4: Add CLOUDFLARE_API_TOKEN env var**

In the `env:` list at the bottom of `configmap.yaml`, add (keep list alphabetical by HOMEPAGE_VAR_ suffix):

```yaml
      - name: HOMEPAGE_VAR_CLOUDFLARE_API_TOKEN
        valueFrom:
          secretKeyRef:
            name: homepage-env-vars
            key: CLOUDFLARE_API_TOKEN
```

- [ ] **Step 5: Commit**

```bash
git add clusters/vollminlab-cluster/homepage/homepage/app/configmap.yaml
git commit -m "feat: add Backblaze B2 prometheus widget and Cloudflare widget to homepage"
```

---

### Task 7: Re-seal homepage-env-vars with Cloudflare token

**Files:**
- Modify: `clusters/vollminlab-cluster/homepage/homepage/app/homepage-env-vars-sealedsecret.yaml`

- [ ] **Step 1: Retrieve all existing homepage secret values from 1Password**

```bash
# List the fields available in the homepage-env-vars item (adjust item name as needed)
op item get "Homepage Env Vars" --vault Homelab --format json | jq '[.fields[] | {id, label, value}]'
```

Collect every key/value pair. The new key to add is `CLOUDFLARE_API_TOKEN`, retrieved from the item **"Cloudflare Homepage Token"** in the Homelab vault:

```bash
CF_TOKEN=$(op item get "Cloudflare Homepage Token" --vault Homelab --field token)
```

- [ ] **Step 2: Fetch the sealing certificate**

```bash
kubeseal --fetch-cert \
  --controller-namespace sealed-secrets \
  --controller-name sealed-secrets-controller > /tmp/pub-cert.pem
```

- [ ] **Step 3: Re-seal with all keys including the new one**

Build the `kubectl create secret` command with every key from step 1 plus `CLOUDFLARE_API_TOKEN=$CF_TOKEN`. Example shape (fill in all values from 1Password):

```bash
kubectl create secret generic homepage-env-vars \
  -n homepage \
  --from-literal=OPENWEATHER_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field OPENWEATHER_API_KEY) \
  --from-literal=WEATHER_LATITUDE=$(op item get "Homepage Env Vars" --vault Homelab --field WEATHER_LATITUDE) \
  --from-literal=WEATHER_LONGITUDE=$(op item get "Homepage Env Vars" --vault Homelab --field WEATHER_LONGITUDE) \
  --from-literal=TRUENAS_USERNAME=$(op item get "Homepage Env Vars" --vault Homelab --field TRUENAS_USERNAME) \
  --from-literal=TRUENAS_PASSWORD=$(op item get "Homepage Env Vars" --vault Homelab --field TRUENAS_PASSWORD) \
  --from-literal=SEERR_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field SEERR_API_KEY) \
  --from-literal=SONARR_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field SONARR_API_KEY) \
  --from-literal=RADARR_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field RADARR_API_KEY) \
  --from-literal=PROWLARR_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field PROWLARR_API_KEY) \
  --from-literal=SABNZBD_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field SABNZBD_API_KEY) \
  --from-literal=JELLYSTAT_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field JELLYSTAT_API_KEY) \
  --from-literal=PIHOLE_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field PIHOLE_API_KEY) \
  --from-literal=BAZARR_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field BAZARR_API_KEY) \
  --from-literal=JELLYFIN_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field JELLYFIN_API_KEY) \
  --from-literal=AUDIOBOOKSHELF_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field AUDIOBOOKSHELF_API_KEY) \
  --from-literal=GRAFANA_USERNAME=$(op item get "Homepage Env Vars" --vault Homelab --field GRAFANA_USERNAME) \
  --from-literal=GRAFANA_PASSWORD=$(op item get "Homepage Env Vars" --vault Homelab --field GRAFANA_PASSWORD) \
  --from-literal=PORTAINER_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field PORTAINER_API_KEY) \
  --from-literal=NPM_USERNAME=$(op item get "Homepage Env Vars" --vault Homelab --field NPM_USERNAME) \
  --from-literal=NPM_PASSWORD=$(op item get "Homepage Env Vars" --vault Homelab --field NPM_PASSWORD) \
  --from-literal=UDM_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field UDM_API_KEY) \
  --from-literal=SHLINK_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field SHLINK_API_KEY) \
  --from-literal=HARBOR_USERNAME=$(op item get "Homepage Env Vars" --vault Homelab --field HARBOR_USERNAME) \
  --from-literal=HARBOR_PASSWORD=$(op item get "Homepage Env Vars" --vault Homelab --field HARBOR_PASSWORD) \
  --from-literal=MINIO_ACCESS_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field MINIO_ACCESS_KEY) \
  --from-literal=MINIO_SECRET_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field MINIO_SECRET_KEY) \
  --from-literal=AUTHENTIK_API_KEY=$(op item get "Homepage Env Vars" --vault Homelab --field AUTHENTIK_API_KEY) \
  --from-literal=CLOUDFLARE_API_TOKEN=$CF_TOKEN \
  --dry-run=client -o yaml | \
  kubeseal --cert /tmp/pub-cert.pem --format yaml \
  > clusters/vollminlab-cluster/homepage/homepage/app/homepage-env-vars-sealedsecret.yaml
rm /tmp/pub-cert.pem
```

**Save `CLOUDFLARE_API_TOKEN` to the "Homepage Env Vars" 1Password item** so future re-seals include it:
```bash
op item edit "Homepage Env Vars" --vault Homelab "CLOUDFLARE_API_TOKEN=$CF_TOKEN"
```

- [ ] **Step 4: Verify the SealedSecret has the correct name and namespace**

```bash
grep -E "name:|namespace:" clusters/vollminlab-cluster/homepage/homepage/app/homepage-env-vars-sealedsecret.yaml | head -4
```

Expected:
```
  name: homepage-env-vars
  namespace: homepage
```

- [ ] **Step 5: Commit**

```bash
git add clusters/vollminlab-cluster/homepage/homepage/app/homepage-env-vars-sealedsecret.yaml
git commit -m "feat: add Cloudflare token to homepage-env-vars SealedSecret"
```

---

### Task 8: Push, open PR, and verify in browser

- [ ] **Step 1: Push branch and open PR**

```bash
git push -u origin <branch>
gh pr create --title "feat: add Backblaze B2 metrics exporter and Cloudflare widget" --body "$(cat <<'EOF'
## Summary
- Deploys b2-exporter to monitoring namespace: Python exporter that lists the Velero B2 bucket and exposes bucket bytes + file count as Prometheus metrics (refreshed every 30 min)
- Adds Backblaze entry to Infrastructure section in Homepage with prometheus widget
- Adds Cloudflare entry to Networking section in Homepage with native cloudflare widget
- Re-seals homepage-env-vars with CLOUDFLARE_API_TOKEN

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: After merge, force Flux reconcile**

```bash
flux reconcile kustomization monitoring --with-source
flux reconcile kustomization homepage --with-source
```

- [ ] **Step 3: Verify Homepage shows both new entries**

Open `https://homepage.vollminlab.com` and confirm:
- Infrastructure section: Backblaze card visible in the 4th slot of row 2. Widget shows a byte value (may show `0` until first 30-min refresh completes — check logs).
- Networking section: now 2 rows of 3. Cloudflare card visible showing requests/bandwidth/threats.

- [ ] **Step 4: Verify Cloudflare widget data**

The Cloudflare widget should show non-zero requests within a few seconds. If it shows an auth error, verify `CLOUDFLARE_API_TOKEN` was correctly sealed and the token has `Zone:Analytics:Read` permission for the zone.

- [ ] **Step 5: Verify B2 metrics in Prometheus**

```bash
# After the first 30-min refresh interval, or wait for startup update:
kubectl logs -n monitoring -l app=b2-exporter --tail=5
```

Then query in Prometheus UI: `b2_bucket_bytes_total` — should return a value.
