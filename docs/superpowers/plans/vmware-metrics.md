# VMware Metrics Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a Prometheus exporter for VMware vCenter/ESXi that feeds into the existing kube-prometheus-stack, adds host-health alerting via Alertmanager, and surfaces a Grafana dashboard with ESXi host and datastore metrics.

**Architecture:** The `pryorda/vmware_exporter` image (via the `kremers/vmware-exporter` Helm chart) runs as a Deployment in the `monitoring` namespace. It authenticates to vCenter via a dedicated read-only service account and exposes Prometheus metrics on port 9272 for all ESXi hosts, datastores, and VMs managed by vCenter. A ServiceMonitor wires it into the existing Prometheus instance (`serviceMonitorSelector: {}` — no label filtering). PrometheusRules define alerts for host availability and resource saturation. A Grafana dashboard ConfigMap is provisioned via the existing Grafana sidecar.

**Tech Stack:** pryorda/vmware_exporter v0.18.4, kremers/vmware-exporter Helm chart 2.3.0, kube-prometheus-stack (already deployed in `monitoring` namespace), SealedSecrets, Flux CD

---

## File Map

**New files:**
- `clusters/vollminlab-cluster/flux-system/repositories/vmware-exporter-helmrepository.yaml`
- `clusters/vollminlab-cluster/flux-system/flux-kustomizations/vmware-exporter-kustomization.yaml`
- `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/kustomization.yaml`
- `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/helmrelease.yaml`
- `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/configmap.yaml`
- `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/vmware-exporter-credentials-sealedsecret.yaml`
- `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/servicemonitor.yaml`
- `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/prometheusrule.yaml`
- `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/grafana-dashboard-configmap.yaml`

**Modified files:**
- `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml` — add `vmware-exporter-helmrepository.yaml`
- `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml` — add `vmware-exporter-kustomization.yaml`

---

### Task 1: Create vCenter read-only service account and save to 1Password

**Files:** None — vCenter UI + 1Password only.

- [ ] **Step 1: Create the SSO user**

  vCenter → Menu → Administration → Single Sign On → Users and Groups → Users tab → Add.

  | Field | Value |
  |-------|-------|
  | Domain | `vsphere.local` |
  | Username | `prometheus-exporter` |
  | Password | Generate 20+ char password — do not write it down yet |
  | First / Last name | `Prometheus Exporter` |

- [ ] **Step 2: Assign ReadOnly role at vCenter root**

  vCenter → Menu → Administration → Access Control → Global Permissions → Add.

  | Field | Value |
  |-------|-------|
  | User | `prometheus-exporter@vsphere.local` |
  | Role | `Read-only` |
  | Propagate to children | checked |

- [ ] **Step 3: Save to 1Password before doing anything else**

  ```
  Vault: Homelab
  Title: VMware Exporter vCenter Account
  Username: prometheus-exporter@vsphere.local
  Password: <what you set above>
  Tags: Homelab
  ```

---

### Task 2: Seal vCenter credentials

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/vmware-exporter-credentials-sealedsecret.yaml`

- [ ] **Step 1: Retrieve password from 1Password**

  ```bash
  op item get "VMware Exporter vCenter Account" --vault Homelab --format json | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(next(f['value'] for f in d['fields'] if f.get('purpose') == 'PASSWORD'))"
  ```

- [ ] **Step 2: Fetch sealing certificate**

  ```bash
  kubeseal --fetch-cert \
    --controller-namespace sealed-secrets \
    --controller-name sealed-secrets-controller > /tmp/pub-cert.pem
  ```

- [ ] **Step 3: Seal the credentials**

  Replace `<PASSWORD>` with the value from Step 1 (no newlines, no quotes around it in the substitution):

  ```bash
  kubectl create secret generic vmware-exporter-credentials \
    -n monitoring \
    --from-literal=helm-values.yaml=$'vsphere:\n  user: prometheus-exporter@vsphere.local\n  password: "<PASSWORD>"' \
    --dry-run=client -o yaml | \
    kubeseal --cert /tmp/pub-cert.pem --format yaml \
    > clusters/vollminlab-cluster/monitoring/vmware-exporter/app/vmware-exporter-credentials-sealedsecret.yaml

  rm /tmp/pub-cert.pem
  ```

- [ ] **Step 4: Verify no plaintext credentials in the output**

  ```bash
  grep -i "password\|vsphere" clusters/vollminlab-cluster/monitoring/vmware-exporter/app/vmware-exporter-credentials-sealedsecret.yaml
  ```
  Expected: only `encryptedData` keys, no readable values. If the password appears in plaintext the sealing failed — delete the file and retry.

- [ ] **Step 5: Commit**

  ```bash
  git add clusters/vollminlab-cluster/monitoring/vmware-exporter/app/vmware-exporter-credentials-sealedsecret.yaml
  git commit -m "feat(vmware-exporter): add sealed vCenter credentials"
  ```

---

### Task 3: Add Helm repository to Flux

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/vmware-exporter-helmrepository.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`

- [ ] **Step 1: Verify Helm repo URL**

  ```bash
  curl -s https://kremers.github.io/charts-vmware-exporter/index.yaml | grep "vmware-exporter"
  ```
  Expected: output includes `vmware-exporter` and version `2.3.0`. If this 404s, visit https://artifacthub.io/packages/helm/kremers/vmware-exporter, copy the Install → `helm repo add` URL, and use that instead in Step 2.

- [ ] **Step 2: Write HelmRepository CR**

  ```yaml
  apiVersion: source.toolkit.fluxcd.io/v1
  kind: HelmRepository
  metadata:
    name: vmware-exporter-repo
    namespace: flux-system
    labels:
      app: vmware-exporter
      env: production
      category: observability
  spec:
    interval: 12h
    url: https://kremers.github.io/charts-vmware-exporter
  ```

  Save to `clusters/vollminlab-cluster/flux-system/repositories/vmware-exporter-helmrepository.yaml`.

- [ ] **Step 3: Add to repositories index (alphabetical order)**

  Open `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml` and add:
  ```yaml
  - vmware-exporter-helmrepository.yaml
  ```
  in alphabetical position in the `resources` list.

- [ ] **Step 4: Commit**

  ```bash
  git add clusters/vollminlab-cluster/flux-system/repositories/vmware-exporter-helmrepository.yaml \
          clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml
  git commit -m "feat(vmware-exporter): add Helm repository"
  ```

---

### Task 4: Scaffold Flux Kustomization CR and app directory

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/vmware-exporter-kustomization.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`

- [ ] **Step 1: Write Flux Kustomization CR**

  ```yaml
  apiVersion: kustomize.toolkit.fluxcd.io/v1
  kind: Kustomization
  metadata:
    name: monitoring-vmware-exporter
    namespace: flux-system
    labels:
      app: vmware-exporter
      env: production
      category: observability
  spec:
    interval: 10m
    path: ./clusters/vollminlab-cluster/monitoring/vmware-exporter/app
    prune: true
    sourceRef:
      kind: GitRepository
      name: flux-system
  ```

  Save to `clusters/vollminlab-cluster/flux-system/flux-kustomizations/vmware-exporter-kustomization.yaml`.

- [ ] **Step 2: Add to flux-kustomizations index (alphabetical order)**

  Open `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml` and add:
  ```yaml
  - vmware-exporter-kustomization.yaml
  ```
  in alphabetical position.

- [ ] **Step 3: Write app kustomization.yaml**

  ```yaml
  apiVersion: kustomize.config.k8s.io/v1beta1
  kind: Kustomization
  resources:
    - helmrelease.yaml
    - configmap.yaml
    - vmware-exporter-credentials-sealedsecret.yaml
    - servicemonitor.yaml
    - prometheusrule.yaml
    - grafana-dashboard-configmap.yaml
  ```

  Save to `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/kustomization.yaml`.

- [ ] **Step 4: Commit**

  ```bash
  git add clusters/vollminlab-cluster/flux-system/flux-kustomizations/vmware-exporter-kustomization.yaml \
          clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml \
          clusters/vollminlab-cluster/monitoring/vmware-exporter/app/kustomization.yaml
  git commit -m "feat(vmware-exporter): add Flux Kustomization CR and app scaffold"
  ```

---

### Task 5: Deploy vmware-exporter HelmRelease

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/helmrelease.yaml`

- [ ] **Step 1: Write ConfigMap (non-secret Helm values)**

  ```yaml
  apiVersion: v1
  kind: ConfigMap
  metadata:
    name: vmware-exporter-values
    namespace: monitoring
    labels:
      app: vmware-exporter
      env: production
      category: observability
  data:
    values.yaml: |
      image:
        tag: v0.18.4

      vsphere:
        host: vcenter.vollminlab.com
        ignoressl: true
        collectors:
          hosts: true
          datastores: true
          vms: true

      resources:
        requests:
          cpu: 50m
          memory: 128Mi
        limits:
          cpu: 200m
          memory: 256Mi

      service:
        type: ClusterIP
        port: 9272
        targetPort: 9272
  ```

  Save to `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/configmap.yaml`.

- [ ] **Step 2: Write HelmRelease**

  ```yaml
  apiVersion: helm.toolkit.fluxcd.io/v2
  kind: HelmRelease
  metadata:
    name: vmware-exporter
    namespace: monitoring
    labels:
      app: vmware-exporter
      env: production
      category: observability
  spec:
    interval: 1h
    chart:
      spec:
        chart: vmware-exporter
        version: "2.3.0"
        sourceRef:
          kind: HelmRepository
          name: vmware-exporter-repo
          namespace: flux-system
    valuesFrom:
      - kind: ConfigMap
        name: vmware-exporter-values
        valuesKey: values.yaml
      - kind: Secret
        name: vmware-exporter-credentials
        valuesKey: helm-values.yaml
  ```

  Save to `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/helmrelease.yaml`.

- [ ] **Step 3: Commit**

  ```bash
  git add clusters/vollminlab-cluster/monitoring/vmware-exporter/app/configmap.yaml \
          clusters/vollminlab-cluster/monitoring/vmware-exporter/app/helmrelease.yaml
  git commit -m "feat(vmware-exporter): add HelmRelease and ConfigMap"
  ```

- [ ] **Step 4: After PR merges, verify the pod starts**

  ```bash
  flux get helmrelease vmware-exporter -n monitoring
  ```
  Expected: `Ready  True`

  ```bash
  kubectl get pods -n monitoring -l app.kubernetes.io/name=vmware-exporter
  ```
  Expected: `Running`

  If `CrashLoopBackOff`:
  ```bash
  kubectl logs -n monitoring -l app.kubernetes.io/name=vmware-exporter --tail=50
  ```
  Common causes: wrong `vsphere.host`, bad credentials, SSL error. Re-seal corrected credentials and push a new commit.

- [ ] **Step 5: Spot-check metrics (one-time)**

  ```bash
  kubectl port-forward -n monitoring svc/vmware-exporter 9272:9272 &
  curl -s http://localhost:9272/metrics | grep "^vmware_host_power_state"
  kill %1
  ```
  Expected output like:
  ```
  vmware_host_power_state{host_name="esxi01.vollminlab.com",...} 1.0
  vmware_host_power_state{host_name="esxi02.vollminlab.com",...} 1.0
  vmware_host_power_state{host_name="esxi03.vollminlab.com",...} 1.0
  ```
  Also note the exact metric names for hosts and datastores — they must match what the PrometheusRules in Task 7 use.

---

### Task 6: Wire ServiceMonitor

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/servicemonitor.yaml`

- [ ] **Step 1: Confirm the Service port name**

  ```bash
  kubectl get svc -n monitoring -l app.kubernetes.io/name=vmware-exporter -o jsonpath='{.items[0].spec.ports[*].name}'
  ```
  Note the port name (likely `http` or `metrics`). Use it in Step 2.

- [ ] **Step 2: Write ServiceMonitor**

  Replace `<PORT-NAME>` with the value from Step 1.

  ```yaml
  apiVersion: monitoring.coreos.com/v1
  kind: ServiceMonitor
  metadata:
    name: vmware-exporter
    namespace: monitoring
    labels:
      app: vmware-exporter
      env: production
      category: observability
  spec:
    selector:
      matchLabels:
        app.kubernetes.io/name: vmware-exporter
    endpoints:
      - port: <PORT-NAME>
        path: /metrics
        interval: 60s
        scheme: http
  ```

  Save to `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/servicemonitor.yaml`.

- [ ] **Step 3: Commit**

  ```bash
  git add clusters/vollminlab-cluster/monitoring/vmware-exporter/app/servicemonitor.yaml
  git commit -m "feat(vmware-exporter): add ServiceMonitor"
  ```

- [ ] **Step 4: Verify Prometheus scrapes it**

  After Flux reconciles (~10 min after merge):
  - Visit `https://prometheus.vollminlab.com/targets` and filter by `vmware`
  - Expected: target with state `UP` and non-zero `Last Scrape` time

  If state is `DOWN`, check:
  ```bash
  kubectl describe servicemonitor vmware-exporter -n monitoring
  ```
  and confirm the `selector.matchLabels` values match the pod's actual labels.

---

### Task 7: PrometheusRules for alerting

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/prometheusrule.yaml`

- [ ] **Step 1: Verify exact metric names from live data**

  Before writing the rules, confirm names match what the exporter actually emits:
  ```bash
  kubectl port-forward -n monitoring svc/vmware-exporter 9272:9272 &
  curl -s http://localhost:9272/metrics | grep "^vmware_host" | awk '{print $1}' | sort -u
  curl -s http://localhost:9272/metrics | grep "^vmware_datastore" | awk '{print $1}' | sort -u
  kill %1
  ```
  The rules below use the names from pryorda/vmware_exporter v0.18.4 docs. If the live output shows different names, update the `expr` fields accordingly.

- [ ] **Step 2: Write PrometheusRule**

  ```yaml
  apiVersion: monitoring.coreos.com/v1
  kind: PrometheusRule
  metadata:
    name: vmware-exporter-rules
    namespace: monitoring
    labels:
      app: vmware-exporter
      env: production
      category: observability
  spec:
    groups:
      - name: vmware.hosts
        rules:
          - alert: ESXiHostPoweredOff
            expr: vmware_host_power_state == 0
            for: 2m
            labels:
              severity: critical
            annotations:
              summary: "ESXi host {{ $labels.host_name }} is powered off"
              description: "{{ $labels.host_name }} has reported power_state=0 for >2m. vCenter is reachable but host is off or unresponsive."

          - alert: ESXiHostHighCPU
            expr: (vmware_host_cpu_usage / vmware_host_cpu_max) * 100 > 85
            for: 15m
            labels:
              severity: warning
            annotations:
              summary: "ESXi host {{ $labels.host_name }} CPU > 85%"
              description: "{{ $labels.host_name }} CPU at {{ $value | printf \"%.0f\" }}% for 15m."

          - alert: ESXiHostHighMemory
            expr: (vmware_host_memory_usage / vmware_host_memory_max) * 100 > 90
            for: 15m
            labels:
              severity: warning
            annotations:
              summary: "ESXi host {{ $labels.host_name }} memory > 90%"
              description: "{{ $labels.host_name }} memory at {{ $value | printf \"%.0f\" }}% for 15m."

      - name: vmware.datastores
        rules:
          - alert: DatastoreLowFreeSpace
            expr: (vmware_datastore_freespace_size / vmware_datastore_capacity_size) * 100 < 20
            for: 5m
            labels:
              severity: warning
            annotations:
              summary: "Datastore {{ $labels.ds_name }} < 20% free"
              description: "{{ $labels.ds_name }} has {{ $value | printf \"%.0f\" }}% free space remaining."

          - alert: DatastoreCriticalFreeSpace
            expr: (vmware_datastore_freespace_size / vmware_datastore_capacity_size) * 100 < 10
            for: 5m
            labels:
              severity: critical
            annotations:
              summary: "Datastore {{ $labels.ds_name }} critically low — take action now"
              description: "{{ $labels.ds_name }} has {{ $value | printf \"%.0f\" }}% free space remaining."
  ```

  Save to `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/prometheusrule.yaml`.

- [ ] **Step 3: Commit**

  ```bash
  git add clusters/vollminlab-cluster/monitoring/vmware-exporter/app/prometheusrule.yaml
  git commit -m "feat(vmware-exporter): add PrometheusRules for host and datastore alerts"
  ```

- [ ] **Step 4: Verify rules are loaded**

  After Flux reconciles, visit `https://prometheus.vollminlab.com/rules` and filter by `vmware`. Expected: all 4 rules in `vmware.hosts` and `vmware.datastores` groups with state `inactive` (inactive = hosts are up, no threshold breached — correct).

---

### Task 8: Grafana dashboard

**Files:**
- Create: `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/grafana-dashboard-configmap.yaml`

- [ ] **Step 1: Find a compatible community dashboard**

  Visit https://grafana.com/grafana/dashboards/?search=vmware+exporter and find a dashboard for `pryorda/vmware_exporter`. A good candidate is any dashboard that references `vmware_host_power_state` or `vmware_host_cpu_usage`. Note the dashboard ID (the number in the URL, e.g. `https://grafana.com/grafana/dashboards/8168` → ID `8168`).

  Also check the pryorda/vmware_exporter README on GitHub — it may link to a specific dashboard.

- [ ] **Step 2: Download dashboard JSON**

  ```bash
  DASHBOARD_ID=<id-from-step-1>
  curl -sL "https://grafana.com/api/dashboards/${DASHBOARD_ID}/revisions/latest/download" \
    > /tmp/vmware-exporter-dashboard.json
  ```

- [ ] **Step 3: Patch the JSON for this cluster**

  Set `"id": null` so Grafana assigns a fresh ID (prevents collision):
  ```bash
  python3 -c "
  import json, sys
  d = json.load(open('/tmp/vmware-exporter-dashboard.json'))
  d['id'] = None
  d.setdefault('uid', 'vmware-exporter')
  print(json.dumps(d, indent=2))
  " > /tmp/vmware-exporter-dashboard-patched.json
  ```

  Verify the datasource references look reasonable:
  ```bash
  grep -o '"uid": "[^"]*"' /tmp/vmware-exporter-dashboard-patched.json | sort -u
  ```
  If you see UIDs other than `prometheus` or `grafana`, check what datasource UIDs exist in this cluster:
  ```bash
  kubectl get configmap -n monitoring -l grafana_datasource=1 -o yaml | grep '"uid"'
  ```
  and do a find-and-replace in the JSON if needed.

- [ ] **Step 4: Write ConfigMap**

  ```bash
  DASHBOARD_JSON=$(cat /tmp/vmware-exporter-dashboard-patched.json)
  ```

  Create `clusters/vollminlab-cluster/monitoring/vmware-exporter/app/grafana-dashboard-configmap.yaml`:

  ```yaml
  apiVersion: v1
  kind: ConfigMap
  metadata:
    name: vmware-exporter-dashboard
    namespace: monitoring
    labels:
      app: kube-prometheus-stack
      env: production
      category: observability
      grafana_dashboard: "1"
  # yamllint disable rule:line-length
  data:
    vmware-exporter.json: |
      <paste full content of /tmp/vmware-exporter-dashboard-patched.json here>
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add clusters/vollminlab-cluster/monitoring/vmware-exporter/app/grafana-dashboard-configmap.yaml
  git commit -m "feat(vmware-exporter): add Grafana dashboard ConfigMap"
  ```

- [ ] **Step 6: Verify dashboard loads in Grafana**

  After Flux reconciles, visit `https://grafana.vollminlab.com` and search for the dashboard by name. Confirm panels populate with ESXi host CPU, memory, power state, and datastore free-space data.

---

## Implementation notes

**vCenter goes down:** If vCenter becomes unreachable, the exporter fails to scrape entirely. The Prometheus target shows `DOWN` rather than `ESXiHostPoweredOff` firing. The homepage ping dot for vCenter covers this gap — between the two you have full visibility.

**SSL:** `ignoressl: true` is correct here. The exporter communicates only with vCenter (not directly with ESXi), and vCenter uses a self-signed cert in this homelab.

**Image version:** The kremers chart defaults to `v0.10.4`. The ConfigMap pins it to `v0.18.4` (latest as of the plan date). Before deploying, verify `v0.18.4` still exists: `curl -s https://hub.docker.com/v2/repositories/pryorda/vmware_exporter/tags/?name=v0.18.4 | python3 -m json.tool | grep name`.

**Metric names:** The PrometheusRule expressions use names from v0.18.4 docs. Always verify against live output (Task 7 Step 1) before relying on the rules.
