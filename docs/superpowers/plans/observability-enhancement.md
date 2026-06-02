# Observability Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose all control plane metrics to Prometheus, add targeted alert rules for Longhorn/Velero/cert-manager, and import Longhorn and Velero dashboards into Grafana.

**Architecture:** Three independent PRs. PR 1 requires manual SSH changes to control plane static pod manifests first (not GitOps), followed by a GitOps values update to re-enable scrapers. PRs 2 and 3 are pure GitOps changes — enable monitoring in existing charts and add a custom PrometheusRule and Grafana dashboards.

**Tech Stack:** kubeadm static pod manifests, kube-prometheus-stack v83.6.0, Flux CD, Longhorn Helm chart, Velero Helm chart, cert-manager Helm chart, PrometheusRule CRD.

---

## File Map

### PR 1 — Expose Control Plane Metrics
| Action | File |
|---|---|
| SSH edit (manual, k8scp01/02/03) | `/etc/kubernetes/manifests/etcd.yaml` |
| SSH edit (manual, k8scp01/02/03) | `/etc/kubernetes/manifests/kube-controller-manager.yaml` |
| SSH edit (manual, k8scp01/02/03) | `/etc/kubernetes/manifests/kube-scheduler.yaml` |
| kubectl patch (manual) | `kube-proxy` ConfigMap in `kube-system` |
| Modify | `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml` |

### PR 2 — Alert Rules + Monitoring Sources
| Action | File |
|---|---|
| Modify | `clusters/vollminlab-cluster/longhorn-system/longhorn/app/configmap.yaml` |
| Modify | `clusters/vollminlab-cluster/velero/velero/app/configmap.yaml` |
| Modify | `clusters/vollminlab-cluster/cert-manager/cert-manager/app/configmap.yaml` |
| Create | `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/prometheusrule-custom.yaml` |
| Modify | `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/kustomization.yaml` |

### PR 3 — Grafana Dashboards
| Action | File |
|---|---|
| Modify | `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml` |

---

## PR 1: Expose Control Plane Metrics

> **Pre-requisite:** All manual SSH steps must be verified complete before creating this PR. The Helm values change will cause Prometheus to immediately attempt scraping — endpoints must be reachable first.

**Branch:** `feat/expose-control-plane-metrics`

---

### Task 1.1: Collect Control Plane Node IPs

These IPs go into the `endpoints` lists in the kube-prometheus-stack values.

- [ ] **Step 1: Get CP node IPs**

```bash
kubectl get nodes -l node-role.kubernetes.io/control-plane \
  -o jsonpath='{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}'
```

Expected output: 3 IP addresses (one per CP node — k8scp01, k8scp02, k8scp03). Note them for use in later steps.

---

### Task 1.2: Patch etcd Static Pod Manifest (one node at a time)

**Critical:** Do k8scp01, verify etcd health, then k8scp02, verify, then k8scp03. Never patch two nodes simultaneously.

In kubeadm clusters, `--listen-metrics-urls` is usually already present as `http://127.0.0.1:2381`. Check first — if it exists, change `127.0.0.1` to `0.0.0.0`. If it's absent, add it.

- [ ] **Step 1: SSH into k8scp01 and check current etcd manifest**

```bash
ssh k8scp01
grep -n "listen-metrics-urls" /etc/kubernetes/manifests/etcd.yaml
```

Expected output:
- If found: `    - --listen-metrics-urls=http://127.0.0.1:2381` → change `127.0.0.1` to `0.0.0.0`
- If not found → add the flag

- [ ] **Step 2: Edit /etc/kubernetes/manifests/etcd.yaml on k8scp01**

```bash
# On k8scp01 — use sed to change in-place (do not write the whole file):
sudo sed -i 's|--listen-metrics-urls=http://127.0.0.1:2381|--listen-metrics-urls=http://0.0.0.0:2381|' \
  /etc/kubernetes/manifests/etcd.yaml

# If the flag did not exist, add it (insert after the `- etcd` line):
# sudo sed -i '/^    - etcd$/a\    - --listen-metrics-urls=http://0.0.0.0:2381' \
#   /etc/kubernetes/manifests/etcd.yaml

# Verify the change:
grep "listen-metrics-urls" /etc/kubernetes/manifests/etcd.yaml
```

Expected: `    - --listen-metrics-urls=http://0.0.0.0:2381`

- [ ] **Step 3: Wait for etcd on k8scp01 to restart and verify health**

Kubelet detects the manifest change and restarts the etcd pod automatically (within ~30s).

```bash
# From any machine with kubectl:
kubectl get pods -n kube-system -l component=etcd -w
# Wait until all 3 etcd pods show Running

# Then verify cluster health (run from k8scp01):
ETCD_POD=$(kubectl get pods -n kube-system -l component=etcd,tier=control-plane \
  --field-selector spec.nodeName=k8scp01 -o name | head -1)
kubectl exec -n kube-system $ETCD_POD -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  endpoint health --cluster
```

Expected: all 3 endpoints report `health: true`.

- [ ] **Step 4: Verify metrics endpoint is reachable on k8scp01**

```bash
# From k8scp01:
curl -s http://127.0.0.1:2381/metrics | head -5
```

Expected: Prometheus metrics output starting with `# HELP` or `go_gc_duration_seconds`.

- [ ] **Step 5: Repeat Task 1.2 Steps 1-4 for k8scp02**

SSH to k8scp02 and repeat the same sed/verify/health-check sequence. Do not proceed until etcd cluster shows 3/3 healthy.

- [ ] **Step 6: Repeat Task 1.2 Steps 1-4 for k8scp03**

SSH to k8scp03 and repeat. After this, all 3 etcd nodes expose metrics on port 2381.

---

### Task 1.3: Patch kube-controller-manager and kube-scheduler

These static pod manifests use `--bind-address=127.0.0.1` by default in kubeadm. Change to `0.0.0.0`. These can be patched on all nodes without the one-at-a-time caution needed for etcd (controller-manager and scheduler elect leaders, so a rolling restart is safe).

- [ ] **Step 1: Patch kube-controller-manager on k8scp01, k8scp02, k8scp03**

```bash
# Run on each CP node (k8scp01, k8scp02, k8scp03):
sudo sed -i 's|--bind-address=127.0.0.1|--bind-address=0.0.0.0|' \
  /etc/kubernetes/manifests/kube-controller-manager.yaml

# Verify:
grep "bind-address" /etc/kubernetes/manifests/kube-controller-manager.yaml
```

Expected: `    - --bind-address=0.0.0.0`

- [ ] **Step 2: Patch kube-scheduler on k8scp01, k8scp02, k8scp03**

```bash
# Run on each CP node:
sudo sed -i 's|--bind-address=127.0.0.1|--bind-address=0.0.0.0|' \
  /etc/kubernetes/manifests/kube-scheduler.yaml

# Verify:
grep "bind-address" /etc/kubernetes/manifests/kube-scheduler.yaml
```

Expected: `    - --bind-address=0.0.0.0`

- [ ] **Step 3: Verify pods restarted and are Running**

```bash
kubectl get pods -n kube-system -l 'component in (kube-controller-manager,kube-scheduler)'
```

Expected: all pods `Running`, restarts column incremented by 1.

- [ ] **Step 4: Verify metrics endpoints reachable (HTTPS)**

```bash
# Run from one CP node — these use HTTPS with the node's serving cert:
curl -sk https://127.0.0.1:10257/metrics | head -5  # controller-manager
curl -sk https://127.0.0.1:10259/metrics | head -5  # scheduler
```

Expected: Prometheus metrics output.

---

### Task 1.4: Patch kube-proxy ConfigMap

- [ ] **Step 1: Edit kube-proxy ConfigMap**

```bash
kubectl edit configmap kube-proxy -n kube-system
```

Find the line `metricsBindAddress: ""` or `metricsBindAddress: "127.0.0.1:10249"` inside `config.conf` and change it to:

```
metricsBindAddress: "0.0.0.0:10249"
```

Save and exit the editor.

- [ ] **Step 2: Rollout restart kube-proxy DaemonSet**

```bash
kubectl rollout restart daemonset kube-proxy -n kube-system
kubectl rollout status daemonset kube-proxy -n kube-system
```

Expected: `daemon set "kube-proxy" successfully rolled out`

- [ ] **Step 3: Verify kube-proxy metrics reachable from a worker node**

```bash
# SSH to any worker node:
curl -s http://127.0.0.1:10249/metrics | head -5
```

Expected: Prometheus metrics output.

---

### Task 1.5: Re-enable Scrapers in kube-prometheus-stack (GitOps)

- [ ] **Step 1: Create branch**

```bash
git checkout main && git pull
git checkout -b feat/expose-control-plane-metrics
```

- [ ] **Step 2: Get CP node IPs (if not already noted from Task 1.1)**

```bash
kubectl get nodes -l node-role.kubernetes.io/control-plane \
  -o jsonpath='{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}'
```

- [ ] **Step 3: Update configmap.yaml — replace the four disabled blocks**

In `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`, replace:

```yaml
    kubeEtcd:
      enabled: false

    kubeProxy:
      enabled: false

    kubeControllerManager:
      enabled: false

    kubeScheduler:
      enabled: false
```

With (substituting actual CP node IPs for `<IP1>`, `<IP2>`, `<IP3>`):

```yaml
    kubeEtcd:
      enabled: true
      endpoints:
        - <IP1>
        - <IP2>
        - <IP3>
      service:
        port: 2381
        targetPort: 2381

    kubeControllerManager:
      enabled: true
      endpoints:
        - <IP1>
        - <IP2>
        - <IP3>

    kubeScheduler:
      enabled: true
      endpoints:
        - <IP1>
        - <IP2>
        - <IP3>

    kubeProxy:
      enabled: true
```

Also add under `defaultRules`:
```yaml
    defaultRules:
      rules:
        kubeApiserver: true
        etcd: true
      disabled:
        KubeClientCertificateExpiration: true
```

- [ ] **Step 4: Commit and push**

```bash
git add clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml
git commit -m "feat: expose control plane metrics (etcd, controller-manager, scheduler, kube-proxy)"
git push -u origin feat/expose-control-plane-metrics
```

- [ ] **Step 5: Open PR and verify after merge**

After PR merges and Flux reconciles (~5 min), verify:

```bash
kubectl get servicemonitor -n monitoring | grep -E 'etcd|controller|scheduler|proxy'
# Check Prometheus targets in Grafana → Explore → Prometheus → Targets
# All 4 components should show UP
```

---

## PR 2: Alert Rules + Enable Monitoring Sources

**Branch:** `feat/observability-alert-rules`

---

### Task 2.1: Enable Longhorn ServiceMonitor

The Longhorn chart ships built-in PrometheusRules when its ServiceMonitor is enabled. Enabling this also activates Longhorn's own alerting rules (volume health, disk space, replica failures).

- [ ] **Step 1: Add metrics config to Longhorn values**

In `clusters/vollminlab-cluster/longhorn-system/longhorn/app/configmap.yaml`, add under `values.yaml:` (at top level, alongside existing `defaultSettings`, `longhornManager`, etc.):

```yaml
    metrics:
      serviceMonitor:
        enabled: true
```

- [ ] **Step 2: Commit change**

```bash
git add clusters/vollminlab-cluster/longhorn-system/longhorn/app/configmap.yaml
git commit -m "feat: enable Longhorn Prometheus ServiceMonitor"
```

---

### Task 2.2: Enable Velero Metrics ServiceMonitor

- [ ] **Step 1: Add metrics config to Velero values**

In `clusters/vollminlab-cluster/velero/velero/app/configmap.yaml`, add under `values.yaml:` (at top level, alongside existing `podLabels`, `resources`, etc.):

```yaml
    metrics:
      enabled: true
      serviceMonitor:
        enabled: true
      nodeAgentPodMonitor:
        enabled: true
```

- [ ] **Step 2: Commit change**

```bash
git add clusters/vollminlab-cluster/velero/velero/app/configmap.yaml
git commit -m "feat: enable Velero Prometheus metrics and ServiceMonitor"
```

---

### Task 2.3: Enable cert-manager ServiceMonitor

- [ ] **Step 1: Add prometheus config to cert-manager values**

In `clusters/vollminlab-cluster/cert-manager/cert-manager/app/configmap.yaml`, add under `values.yaml:` (alongside existing `extraArgs`):

```yaml
    prometheus:
      enabled: true
      servicemonitor:
        enabled: true
```

- [ ] **Step 2: Commit change**

```bash
git add clusters/vollminlab-cluster/cert-manager/cert-manager/app/configmap.yaml
git commit -m "feat: enable cert-manager Prometheus ServiceMonitor"
```

---

### Task 2.4: Create Custom PrometheusRule

- [ ] **Step 1: Create the PrometheusRule file**

Create `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/prometheusrule-custom.yaml`:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: vollminlab-custom-rules
  namespace: monitoring
  labels:
    app: kube-prometheus-stack
    env: production
    category: observability
    release: kube-prometheus-stack
spec:
  groups:
    - name: cert-manager
      rules:
        - alert: CertManagerCertificateExpiringSoon
          expr: |
            certmanager_certificate_expiration_timestamp_seconds - time() < 7 * 24 * 3600
          for: 1h
          labels:
            severity: warning
          annotations:
            summary: "Certificate {{ $labels.name }} in {{ $labels.namespace }} expires soon"
            description: "Certificate {{ $labels.name }} (namespace {{ $labels.namespace }}) expires in {{ $value | humanizeDuration }}. Renew now."

        - alert: CertManagerCertificateExpiringCritical
          expr: |
            certmanager_certificate_expiration_timestamp_seconds - time() < 24 * 3600
          for: 15m
          labels:
            severity: critical
          annotations:
            summary: "Certificate {{ $labels.name }} in {{ $labels.namespace }} expires in < 24 hours"
            description: "Certificate {{ $labels.name }} (namespace {{ $labels.namespace }}) expires in {{ $value | humanizeDuration }}. Immediate action required."

    - name: velero
      rules:
        - alert: VeleroScheduledBackupOverdue
          expr: |
            time() - velero_backup_last_successful_timestamp{schedule!=""} > 90000
          for: 15m
          labels:
            severity: warning
          annotations:
            summary: "Velero schedule {{ $labels.schedule }} last succeeded > 25 hours ago"
            description: "Velero backup schedule {{ $labels.schedule }} last succeeded at {{ $value | humanizeTimestamp }}. Check Velero logs."

        - alert: VeleroBackupMetricMissing
          expr: |
            absent(velero_backup_last_successful_timestamp{schedule="daily-full"}) == 1
          for: 30m
          labels:
            severity: critical
          annotations:
            summary: "Velero daily-full backup metric absent — Velero may not be running"
            description: "The velero_backup_last_successful_timestamp metric for schedule=daily-full is missing. Velero may be down or the backup has never run."
```

> **Note on `release: kube-prometheus-stack` label:** Prometheus Operator discovers PrometheusRules by matching `ruleSelector` from the Prometheus CR. kube-prometheus-stack sets `ruleSelector: {}` (match all) by default, so no special labels are required. The `release` label is added for consistency with kube-prometheus-stack's own rules.

- [ ] **Step 2: Register the file in kustomization.yaml**

In `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/kustomization.yaml`, add `prometheusrule-custom.yaml` to the resources list:

```yaml
resources:
  - helmrelease.yaml
  - configmap.yaml
  - ingress.yaml
  - alertmanager-sealedsecret.yaml
  - grafana-admin-sealedsecret.yaml
  - prometheusrule-custom.yaml
```

- [ ] **Step 3: Commit**

```bash
git add clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/prometheusrule-custom.yaml
git add clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/kustomization.yaml
git commit -m "feat: add custom PrometheusRules for cert-manager and Velero backup health"
```

---

### Task 2.5: Push and PR

- [ ] **Step 1: Push branch and open PR**

```bash
git push -u origin feat/observability-alert-rules
gh pr create \
  --title "feat: enable Longhorn/Velero/cert-manager monitoring and custom alert rules" \
  --body "$(cat <<'EOF'
## Summary
- Enable Longhorn ServiceMonitor (activates built-in Longhorn PrometheusRules)
- Enable Velero metrics ServiceMonitor and node-agent PodMonitor
- Enable cert-manager ServiceMonitor
- Add custom PrometheusRules: cert expiry (warning at 7d, critical at 24h) and Velero backup overdue (warning at 25h, critical if metric absent)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Verify after merge**

After Flux reconciles:

```bash
# Check ServiceMonitors exist:
kubectl get servicemonitor -A | grep -E 'longhorn|velero|cert-manager'

# Check PrometheusRule loaded:
kubectl get prometheusrule -n monitoring vollminlab-custom-rules

# In Grafana → Alerting → Alert rules, search "CertManager" and "Velero"
# Both rule groups should appear as Inactive (no cert expiring, backups healthy)

# Verify metrics are being scraped:
# Grafana → Explore → Prometheus → Metrics browser
# Search: certmanager_certificate_expiration_timestamp_seconds
# Search: velero_backup_last_successful_timestamp
# Both should return results
```

---

## PR 3: Grafana Dashboards

**Branch:** `feat/grafana-dashboards`

> **Pre-requisite:** PR 2 should be merged first (Longhorn and Velero metrics needed for dashboard data). The dashboards themselves can be added independently, but will show "No data" until scrapers are active.

---

### Task 3.1: Verify Dashboard IDs Before Writing Code

kube-prometheus-stack downloads dashboards from grafana.com using gnet IDs. Verify the IDs are still valid before writing them into the values.

- [ ] **Step 1: Confirm dashboard IDs**

Check these URLs in a browser (or via curl) to confirm they're still valid and note the latest revision number:

- Longhorn dashboard: `https://grafana.com/grafana/dashboards/13032`
- Velero dashboard: `https://grafana.com/grafana/dashboards/11055`

Note the **current revision number** shown on each dashboard page. You'll use this in the next step.

---

### Task 3.2: Add Dashboard Downloads to kube-prometheus-stack Values

kube-prometheus-stack's Grafana subchart supports `grafana.dashboards` for downloading dashboards by gnet ID. These are fetched at Helm render time via an init container.

- [ ] **Step 1: Add dashboard config to configmap.yaml**

In `clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml`, add inside the `grafana:` block (after the existing `plugins:` section):

```yaml
      dashboardProviders:
        dashboardproviders.yaml:
          apiVersion: 1
          providers:
            - name: imported
              orgId: 1
              folder: Imported
              type: file
              disableDeletion: false
              editable: true
              options:
                path: /var/lib/grafana/dashboards/imported

      dashboards:
        imported:
          longhorn:
            gnetId: 13032
            revision: 6        # <-- update to current revision from Step 1
            datasource: Prometheus
          velero:
            gnetId: 11055
            revision: 2        # <-- update to current revision from Step 1
            datasource: Prometheus
```

> **Note on `datasource`:** The default Prometheus datasource in kube-prometheus-stack is named `Prometheus`. If the dashboard uses a variable like `${DS_PROMETHEUS}`, kube-prometheus-stack's Grafana sidecar will substitute it automatically when the datasource name matches.

- [ ] **Step 2: Commit**

```bash
git add clusters/vollminlab-cluster/monitoring/kube-prometheus-stack/app/configmap.yaml
git commit -m "feat: add Longhorn and Velero Grafana dashboards via gnetId"
```

---

### Task 3.3: Push and PR

- [ ] **Step 1: Push and create PR**

```bash
git push -u origin feat/grafana-dashboards
gh pr create \
  --title "feat: add Longhorn and Velero Grafana dashboards" \
  --body "$(cat <<'EOF'
## Summary
- Add Longhorn storage dashboard (grafana.com/dashboards/13032) to Grafana under "Imported" folder
- Add Velero backup dashboard (grafana.com/dashboards/11055) to Grafana under "Imported" folder
- Creates new "imported" dashboard provider alongside kube-prometheus-stack's built-in provider

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Verify after merge**

After Flux reconciles and Grafana pod restarts:

```bash
# Check Grafana pod restarted with new values:
kubectl get pods -n monitoring -l app.kubernetes.io/name=grafana

# In Grafana → Dashboards → Browse → Imported folder:
# "Longhorn" and "Velero" dashboards should appear
# Longhorn dashboard should show volume health, replica status
# Velero dashboard should show backup success/failure history
```

---

## Post-Implementation Checklist

After all three PRs are merged and Flux has reconciled:

- [ ] Prometheus Targets page shows all 4 control plane components as UP (etcd ×3, controller-manager ×3, scheduler ×3, kube-proxy ×N)
- [ ] Prometheus Targets shows Longhorn, Velero, cert-manager scrapers as UP
- [ ] Alert rules: `CertManagerCertificateExpiringSoon`, `CertManagerCertificateExpiringCritical`, `VeleroScheduledBackupOverdue`, `VeleroBackupMetricMissing` all appear in Grafana Alerting → Alert rules as Inactive
- [ ] Alert rules from built-in etcd group (e.g. `etcdInsufficientMembers`) appear as Inactive (not firing)
- [ ] Grafana Dashboards → Imported folder contains Longhorn and Velero dashboards with data
- [ ] Update `docs/cluster-reference.md`: monitoring section to reflect all scrapers now enabled
