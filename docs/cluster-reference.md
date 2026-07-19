# Vollminlab Cluster Reference

Comprehensive configuration reference for the vollminlab Kubernetes cluster. This document tracks what is actually deployed and configured — versions, values, network rules, storage, resource limits, and security policies. Update this when making changes.

---

## Table of Contents

1. [Cluster Overview](#cluster-overview)
2. [Network Configuration](#network-configuration)
3. [Bootstrap Components](#bootstrap-components)
4. [Cluster-Wide Resources](#cluster-wide-resources)
5. [Security & Policy](#security--policy)
6. [GitOps — Flux CD](#gitops--flux-cd)
7. [Ingress & Certificates](#ingress--certificates)
8. [Storage](#storage)
9. [Backup](#backup)
10. [Infrastructure Services](#infrastructure-services)
11. [Media Stack](#media-stack)
12. [Applications](#applications)
13. [DMZ — Isolated Workloads](#dmz--isolated-workloads)
14. [CI/CD](#cicd)

---

## Cluster Overview

| Property | Value |
|---|---|
| Kubernetes distribution | kubeadm |
| CNI | Calico v3.29.1 (Tigera Operator v1.36.2) |
| GitOps | Flux CD |
| GitOps source | `main` branch, 1-minute pull interval |
| Pod CIDR | `172.18.0.0/16` |
| MetalLB IP pool | `192.168.152.244–192.168.152.254` |
| Control plane replicas | 2 |
| Node update strategy | RollingUpdate (maxUnavailable: 1) |

### Nodes

| Node | Role | Notes |
|---|---|---|
| (control plane nodes) | control-plane | kubeadm-managed |
| k8sworker05 | DMZ worker | Taint: `dmz=true:NoSchedule`, label: `role=dmz` |
| (other workers) | general workloads | Standard scheduling |

---

## Network Configuration

### Calico CNI

Managed manually via `bootstrap/calico/`. **Not Flux-managed.** See [bootstrap/calico/README.md](../bootstrap/calico/README.md).

| Parameter | Value |
|---|---|
| Variant | Calico |
| CNI type | Calico |
| IPAM | Calico |
| BGP | Enabled |
| Dataplane | iptables |
| IPv4 pool CIDR | `172.18.0.0/16` |
| Encapsulation | IPIP |
| NAT outgoing | Enabled |
| Block size | `/26` |
| Node selector | `all()` |
| Allowed uses | Workload, Tunnel |
| Host ports | Enabled |
| Windows dataplane | Disabled |
| Multi-interface mode | None |
| CNI log max size | 100Mi |
| CNI log max count | 10 |
| CNI log max age | 30 days |

### CoreDNS

Custom config applied via `bootstrap/coredns/coredns-configmap.yaml`. Not Flux-managed.

| Parameter | Value |
|---|---|
| Domain | `cluster.local` |
| Cache TTL | 30s |
| Max concurrent forwards | 1000 |
| Prometheus metrics | Port 9153 |
| Plugins | errors, health, ready, kubernetes, prometheus, forward, cache, loop, reload, loadbalance |

### MetalLB

| Parameter | Value |
|---|---|
| Chart version | v0.14.9 |
| Helm repo | https://metallb.universe.tf |
| IP pool name | `metallb-pool` |
| IP range | `192.168.152.244–192.168.152.254` |
| Mode | L2 (L2Advertisement) |
| Auto-assign | true |
| Speaker tolerations | `dmz=Exists:NoSchedule`, `dmz=Exists:NoExecute` |
| Controller resources | req: 50m/64Mi, limits: 200m/128Mi |
| Speaker resources | req: 50m/64Mi, limits: 200m/128Mi |

---

## Bootstrap Components

These are applied manually before Flux bootstraps. They are never reconciled by Flux.

### Sealed Secrets Sealing Key

The cluster sealing key is backed up in 1Password as **"Sealed Secrets Sealing Key"**.

Must be restored **before** Flux bootstraps on a new cluster. Full procedure: [bootstrap/sealed-secrets/README.md](../bootstrap/sealed-secrets/README.md).

```bash
# Restore key before Flux bootstrap
kubectl apply -f <exported-yaml-from-1password>
```

### Bootstrap Order

```
1. Install Kubernetes control plane (kubeadm)
2. Install Calico CNI              → bootstrap/calico/README.md
3. Apply CoreDNS custom config     → bootstrap/coredns/coredns-configmap.yaml
4. Restore sealed-secrets key      → bootstrap/sealed-secrets/README.md
5. Bootstrap Flux CD               → flux bootstrap github ...
6. All apps                        → Flux reconciles automatically
```

---

## Cluster-Wide Resources

Located in `clusters/vollminlab-cluster/clusterwide/`.

### PersistentVolumes (SMB-backed)

All volumes are `ReadWriteMany`, `100Gi`, backed by SMB shares at `192.168.150.2`. UID/GID: `568`.

| PV | SMB Share | Used By |
|---|---|---|
| `pv-movies` | `//192.168.150.2/movies` | mediastack/radarr |
| `pv-tv` | `//192.168.150.2/tv` | mediastack/sonarr |
| `pv-completed-downloads` | `//192.168.150.2/completed-downloads` | mediastack/sabnzbd |
| `pv-incomplete-downloads` | `//192.168.150.2/incomplete-downloads` | mediastack/sabnzbd |

### StorageClasses

**`longhorn-dmz`** — Longhorn storage scoped to DMZ node only:

| Parameter | Value |
|---|---|
| Provisioner | `driver.longhorn.io` |
| Replicas | 2 |
| Node selector | `dmz` |
| Data locality | `best-effort` |
| Stale replica timeout | 30s |
| fsType | ext4 |
| Volume binding mode | `WaitForFirstConsumer` |

**`smb`** — SMB CSI driver for network share mounts:

| Parameter | Value |
|---|---|
| Provisioner | `smb.csi.k8s.io` |
| dir_mode | `0755` |
| file_mode | `0755` |
| uid/gid | `568` |
| Mount options | `mfsymlinks, cache=strict, noserverino` |
| Volume binding mode | `Immediate` |

### RBAC

**`disk-cleanup`** ClusterRole — grants the maintenance CronJob: read nodes/pods, delete pods, read deployments/daemonsets/replicasets.

**Kyverno webhook patch** ClusterRole — grants Kyverno permission to patch `mutatingwebhookconfigurations` and `validatingwebhookconfigurations`.

### Disk Cleanup CronJob

| Parameter | Value |
|---|---|
| Namespace | kube-system |
| Schedule | `0 2 * * *` (2 AM daily) |
| Image | `alpine/k8s:1.30.3` |
| Tasks | Delete evicted pods; delete completed/failed pods older than 1 hour |
| CPU | req: 50m, limits: 500m |
| Memory | req: 64Mi, limits: 128Mi |

---

## Security & Policy

### Kyverno

| Parameter | Value |
|---|---|
| Chart version | 3.7.2 |
| Helm repo | https://kyverno.github.io/kyverno/ |
| Replicas | 3 |
| Admission controller replicas | 3 |
| Admission failure policy | Ignore (30s timeout) |
| Excluded namespaces | kyverno, kube-system, flux-system |

**Admission Controller** — req: 500m/512Mi, limits: 1000m/1Gi
**Background Controller** — req: 100m/128Mi, limits: 200m/256Mi
**Cleanup Controller** — req: 100m/128Mi, limits: 200m/256Mi
**Reports Controller** — req: 100m/128Mi, limits: 200m/256Mi

### ClusterPolicies

| Policy | Mode | Action | Rule |
|---|---|---|---|
| `restrict-default` | enforce | validate | Block all workloads in `default` namespace |
| `restrict-privileged` | enforce | validate | Block privileged containers; exempts: kube-system, calico-system, longhorn-system, metallb-system, csi-driver, tigera-operator, ingress-nginx |
| `restrict-hostpath-usage` | enforce | validate | Block hostPath volumes; exempts: kube-system, calico-system, longhorn-system, monitoring, tigera-operator |
| `restrict-latest-tag` | enforce | validate | Block `:latest` image tags on Deployment/StatefulSet/DaemonSet |
| `restrict-loadbalancer-services` | enforce | validate | LoadBalancer type only allowed in `ingress-nginx` and `dmz` namespaces |
| `require-standard-labels` | audit | validate | Require `app`, `env`, `category` labels on Deployments, StatefulSets, DaemonSets, Pods, Namespaces, Services; exempts: kube-system, default |
| `require-resources` | enforce | validate | Require CPU/memory requests and limits on all containers; exempts Flux deployments |
| `dmz-enforce-node-placement` | enforce | mutate | Auto-inject `nodeSelector: role=dmz` and toleration `dmz=Exists:NoSchedule` on all pods in `dmz` namespace |
| `dmz-restrict-external-access` | enforce | validate | Block `external-access=true` and `internet-egress=true` labels outside `dmz` namespace |
| `inject-namespace-labels` | — | mutate | Auto-copy `app`, `env`, `category` labels from namespace to workloads; exempts: longhorn-system, flux-system, monitoring |
| `inject-resource-requirements` | — | mutate | Auto-inject resource limits for Longhorn sidecar containers (CSI attacher, provisioner, resizer, snapshotter, UI, manager, driver) |

**PolicyException: `ignore-flux-core`** — Exempts all Flux controllers and the `kyverno` HelmRelease from all policies.

### Policy Reporter

| Parameter | Value |
|---|---|
| Chart version | v3.1.3 |
| Helm repo | https://kyverno.github.io/policy-reporter/ |
| Ingress | `policyreporter.vollminlab.com` |
| TLS | wildcard-tls |
| UI resources | req: 50m/64Mi, limits: 100m/128Mi |
| Reporter resources | req: 50m/64Mi, limits: 100m/128Mi |

### Sealed Secrets Controller

| Parameter | Value |
|---|---|
| Chart version | v2.17.1 |
| Release name | `sealed-secrets-controller` |
| Helm repo | https://sealed-secrets.dev |
| Image | `bitnami/sealed-secrets-controller:0.28.0` |
| CPU | req: 50m, limits: 100m |
| Memory | req: 32Mi, limits: 64Mi |
| readOnlyRootFilesystem | true |
| runAsNonRoot | true |
| runAsUser | 1001 |
| fsGroup | 65534 |
| Reconcile interval | 15m |

---

## GitOps — Flux CD

### Sync Configuration

| Parameter | Value |
|---|---|
| GitRepository | `https://github.com/svollmi1/k8s-vollminlab-cluster.git` |
| Branch | `main` |
| Pull interval | 1 minute |
| Auth | SSH key via `flux-system-sealedsecret` |
| Reconcile interval | 10 minutes (all Kustomizations) |
| Prune | enabled (all Kustomizations) |

### Flux Kustomizations

All Kustomizations use `interval: 10m`, `prune: true`, source `flux-system` GitRepository.

| Kustomization | Path | Notes |
|---|---|---|
| `actions-runner-system` | `./clusters/vollminlab-cluster/actions-runner-system` | ARC runner scale set workloads |
| `actions-runner-system-runners` | `./clusters/vollminlab-cluster/actions-runner-system` | |
| `arc-controller` | `./clusters/vollminlab-cluster/arc-controller` | ARC scale set controller |
| `cert-manager` | `./clusters/vollminlab-cluster/cert-manager` | |
| `clusterwide` | `./clusters/vollminlab-cluster/clusterwide` | |
| `cnpg-system` | `./clusters/vollminlab-cluster/cnpg-system` | |
| `dmz` | `./clusters/vollminlab-cluster/dmz` | |
| `external-dns` | `./clusters/vollminlab-cluster/external-dns` | |
| `harbor` | `./clusters/vollminlab-cluster/harbor` | |
| `headlamp` | `./clusters/vollminlab-cluster/flux-system/headlamp/app` | Kubernetes UI with Flux plugin |
| `homepage` | `./clusters/vollminlab-cluster/homepage` | |
| `ingress-nginx` | `./clusters/vollminlab-cluster/ingress-nginx` | |
| `kube-system` | `./clusters/vollminlab-cluster/kube-system` | |
| `kyverno` | `./clusters/vollminlab-cluster/kyverno` | Health checks on 4 deployments |
| `kyverno-policies` | `./clusters/vollminlab-cluster/kyverno/kyverno/policies` | dependsOn: kyverno |
| `kyverno-webhooks-patch` | patch only | |
| `local-path-storage` | `./clusters/vollminlab-cluster/local-path-storage` | |
| `longhorn-system` | `./clusters/vollminlab-cluster/longhorn-system` | |
| `mediastack` | `./clusters/vollminlab-cluster/mediastack` | |
| `metallb-system` | `./clusters/vollminlab-cluster/metallb-system` | |
| `minio` | `./clusters/vollminlab-cluster/minio` | |
| `monitoring` | `./clusters/vollminlab-cluster/monitoring` | kube-prometheus-stack, Loki, Promtail |
| `policy-reporter` | `./clusters/vollminlab-cluster/kyverno` | |
| `policy-reporter-patch` | patch only | |
| `portainer` | `./clusters/vollminlab-cluster/portainer` | |
| `renovate` | `./clusters/vollminlab-cluster/renovate` | |
| `sealed-secrets` | `./clusters/vollminlab-cluster/sealed-secrets` | |
| `shlink` | `./clusters/vollminlab-cluster/shlink` | |
| `velero` | `./clusters/vollminlab-cluster/velero` | |

### Headlamp (Kubernetes UI)

| Parameter | Value |
|---|---|
| Chart | headlamp v0.41.0 (kubernetes-sigs.github.io/headlamp) |
| Namespace | flux-system |
| Ingress | `headlamp.vollminlab.com` |
| TLS | wildcard-tls |
| Plugin | headlamp-plugin-flux v0.6.0 (init container) |
| CPU | req: 150m, limits: 500m |
| Memory | req: 256Mi, limits: 512Mi |

### Repository Sources

| Name | Type | URL / OCI ref |
|---|---|---|
| arc-controller-repo | OCIRepository | oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller (tag: 0.14.0) |
| arc-runners-repo | OCIRepository | oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set (tag: 0.14.0) |
| bazarr-repo | HelmRepository | https://k8s-home-lab.github.io/helm-charts/ |
| cert-manager-repo | HelmRepository | https://charts.jetstack.io |
| cnpg-repo | HelmRepository | https://cloudnative-pg.github.io/charts |
| external-dns-repo | HelmRepository | https://kubernetes-sigs.github.io/external-dns/ |
| grafana-repo | HelmRepository | https://grafana.github.io/helm-charts |
| harbor-repo | HelmRepository | https://helm.goharbor.io |
| headlamp-repo | HelmRepository | https://kubernetes-sigs.github.io/headlamp/ |
| homepage-repo | HelmRepository | https://jameswynn.github.io/helm-charts |
| ingress-nginx-repo | HelmRepository | https://kubernetes.github.io/ingress-nginx |
| jellyfin-repo | HelmRepository | https://jellyfin.github.io/jellyfin-helm/ |
| kyverno-repo | HelmRepository | https://kyverno.github.io/kyverno |
| kyverno-policyreporter-repo | HelmRepository | https://kyverno.github.io/policy-reporter |
| local-path-provisioner-repo | GitRepository | https://github.com/rancher/local-path-provisioner (tag: v0.0.35) |
| longhorn-repo | HelmRepository | https://charts.longhorn.io |
| metallb-repo | HelmRepository | https://metallb.github.io/metallb |
| metrics-server-repo | HelmRepository | https://kubernetes-sigs.github.io/metrics-server/ |
| minecraft-repo | HelmRepository | https://itzg.github.io/minecraft-server-charts/ |
| minio-repo | HelmRepository | https://charts.min.io/ |
| overseerr-repo | OCIRepository | oci://oci.trueforge.org/truecharts/overseerr |
| plex-repo | OCIRepository | oci://oci.trueforge.org/truecharts/plex |
| portainer-repo | HelmRepository | https://portainer.github.io/k8s |
| prometheus-community-repo | HelmRepository | https://prometheus-community.github.io/helm-charts |
| prowlarr-repo | OCIRepository | oci://oci.trueforge.org/truecharts/prowlarr |
| radarr-repo | OCIRepository | oci://oci.trueforge.org/truecharts/radarr |
| renovate-repo | OCIRepository | oci://ghcr.io/renovatebot/charts/renovate |
| sabnzbd-repo | OCIRepository | oci://oci.trueforge.org/truecharts/sabnzbd |
| sealed-secrets-repo | HelmRepository | https://bitnami-labs.github.io/sealed-secrets |
| shlink-repo | HelmRepository | https://charts.christianhuth.de |
| smb-csi-driver-repo | HelmRepository | https://raw.githubusercontent.com/kubernetes-csi/csi-driver-smb/master/charts |
| sonarr-repo | OCIRepository | oci://oci.trueforge.org/truecharts/sonarr |
| tautulli-repo | HelmRepository | https://k8s-at-home.com/charts/ |
| velero-repo | HelmRepository | https://vmware-tanzu.github.io/helm-charts |
| vollminlab-repo | OCIRepository | oci://harbor.vollminlab.com/vollminlab/charts/shlink-ingress-controller |

---

## Ingress & Certificates

### ingress-nginx

| Parameter | Value |
|---|---|
| Chart version | 4.15.1 |
| Helm repo | https://kubernetes.github.io/ingress-nginx |
| Default SSL certificate | `cert-manager/wildcard-tls` |

### cert-manager

| Parameter | Value |
|---|---|
| Chart version | v1.20.2 |
| Helm repo | https://charts.jetstack.io |
| DNS01 recursive nameservers only | true |
| DNS01 recursive nameservers | `10.96.0.10:53` |

### ClusterIssuers

| Name | Type | Notes |
|---|---|---|
| `letsencrypt-cloudflare` | ACME (DNS-01) | Let's Encrypt production; Cloudflare API token from `cloudflare-api-token` SealedSecret |
| `selfsigned` | Self-signed | Bootstrap issuer used only to create the internal CA cert |
| `internal-ca` | CA | Signs certs for internal bare hostnames (e.g. `vl`); backed by `internal-ca-tls` secret in `cert-manager` namespace |

**Internal CA certificate** (`internal-ca-tls`):
- Validity: 10 years (`duration: 87600h`), renews 30 days before expiry (`renewBefore: 720h`)
- Signed by: `selfsigned` ClusterIssuer
- Used by: `internal-ca` ClusterIssuer to issue child certificates

### Ingress Hostnames

All ingresses use `ingressClassName: nginx`, TLS termination via `wildcard-tls`, ssl-redirect enabled.

| Hostname | Backend | Port | Namespace | TLS Secret |
|---|---|---|---|---|
| `homepage.vollminlab.com` | homepage | 3000 | homepage | wildcard-tls |
| `headlamp.vollminlab.com` | headlamp | 4466 | flux-system | wildcard-tls |
| `longhorn.vollminlab.com` | longhorn-frontend | 80 | longhorn-system | wildcard-tls |
| `policyreporter.vollminlab.com` | policy-reporter-ui | 8080 | kyverno | wildcard-tls |
| `radarr.vollminlab.com` | radarr | 7878 | mediastack | wildcard-tls |
| `sonarr.vollminlab.com` | sonarr | 8989 | mediastack | wildcard-tls |
| `sabnzbd.vollminlab.com` | sabnzbd | 10097 | mediastack | wildcard-tls |
| `prowlarr.vollminlab.com` | prowlarr | 9696 | mediastack | wildcard-tls |
| `bazarr.vollminlab.com` | bazarr | 6767 | mediastack | wildcard-tls |
| `overseerr.vollminlab.com` | overseerr | 5055 | mediastack | wildcard-tls |
| `jellyfin.vollminlab.com` | jellyfin | 8096 | mediastack | wildcard-tls |
| `plex.vollminlab.com` | plex | 32400 | mediastack | wildcard-tls |
| `tautulli.vollminlab.com` | tautulli | 8181 | mediastack | wildcard-tls |
| `go.vollminlab.com` | shlink-shlink-backend | 8080 | shlink | wildcard-tls |
| `vl.vollminlab.com` | shlink-shlink-backend | 8080 | shlink | wildcard-tls |
| `vollm.in` | shlink-shlink-backend | 8080 | shlink | vollm-in-tls (Let's Encrypt) |
| `minio.vollminlab.com` | minio | 9001 | minio | wildcard-tls |

---

## Storage

### Longhorn

| Parameter | Value |
|---|---|
| Chart version | v1.8.1 |
| Helm repo | https://charts.longhorn.io |
| Default replica count | 3 |
| Default data path | `/var/lib/longhorn` |
| Taint toleration | `dmz=true:NoSchedule;dmz=true:NoExecute` |
| Manager/driver tolerations | `dmz=true:NoSchedule`, `dmz=true:NoExecute` |
| Ingress | `longhorn.vollminlab.com` |

### SMB CSI Driver

| Parameter | Value |
|---|---|
| Chart version | 1.20.1 |
| Helm repo | https://raw.githubusercontent.com/kubernetes-csi/csi-driver-smb/master/charts |
| NAS address | `192.168.150.2` |
| SMB shares | movies, tv, completed-downloads, incomplete-downloads |
| Mount uid/gid | 568 |

### Local Path Provisioner

> **Note:** Uses `GitRepository` instead of `HelmRepository`. Rancher has never published an official Helm repository for this chart (upstream issue open since 2020). The chart lives only inside the GitHub repo at `deploy/chart/local-path-provisioner`. This is intentional and not an oversight — the `GitRepository` approach pins directly to an upstream release tag with no third-party intermediary.

| Parameter | Value |
|---|---|
| Chart source | GitRepository (rancher/local-path-provisioner tag v0.0.35) |
| Chart path | `deploy/chart/local-path-provisioner` |
| Values | defaults only |

### PVC Inventory

| PVC | Namespace | Size | StorageClass | Access |
|---|---|---|---|---|
| `pvc-movies` | mediastack | 100Gi | smb (bound to pv-movies) | RWX |
| `pvc-tv` | mediastack | 100Gi | smb (bound to pv-tv) | RWX |
| `pvc-completed-downloads` | mediastack | 100Gi | smb | RWX |
| `pvc-incomplete-downloads` | mediastack | 100Gi | smb | RWX |
| `pvc-radarr-config` | mediastack | 5Gi | longhorn | RWO |
| `pvc-sonarr-config` | mediastack | 5Gi | longhorn | RWO |
| `pvc-sabnzbd-config` | mediastack | 5Gi | longhorn | RWO |
| `pvc-prowlarr-config` | mediastack | 5Gi | longhorn | RWO |
| `pvc-bazarr-config` | mediastack | 5Gi | longhorn | RWO |
| `pvc-overseerr-config` | mediastack | 5Gi | longhorn | RWO |
| `pvc-jellyfin-config` | mediastack | 20Gi | longhorn | RWO |
| `pvc-plex-config` | mediastack | 20Gi | longhorn | RWO |
| `pvc-tautulli-config` | mediastack | 1Gi | longhorn | RWO |
| `pvc-minecraft-datadir` | dmz | 20Gi | longhorn-dmz | RWX |
| `portainer` | portainer | 10Gi | local-path | RWO |
| `minio` | minio | 75Gi | longhorn | RWO |

---

## Backup

### Architecture

```
Velero ──► MinIO (minio namespace, Longhorn PVC) ──► Backblaze B2 (off-site, manual replication)
              │
              └── secondary BSL: Velero can target B2 directly if MinIO is unavailable
```

Kubernetes manifests are **not** backed up by Velero — they are restored by Flux from Git. Only stateful PVC data is backed up.

### MinIO

| Parameter | Value |
|---|---|
| Chart | bitnami/minio 17.0.23 |
| Helm repo | https://charts.bitnami.com/bitnami |
| Namespace | `minio` |
| Mode | standalone |
| Storage | 75Gi Longhorn PVC |
| API endpoint (in-cluster) | `http://minio.minio.svc.cluster.local:9000` |
| Console | `minio.vollminlab.com` (port 9001) |
| Root user | `root` (password in 1Password: **MinIO**) |
| Bucket | `velero` (auto-provisioned on startup) |

### Velero

| Parameter | Value |
|---|---|
| Chart | vmware-tanzu/velero 12.0.0 (v1.18.0) |
| Helm repo | https://vmware-tanzu.github.io/helm-charts |
| Namespace | `velero` |
| Backup method | kopia file-system backup (node-agent DaemonSet) |
| Primary BSL | `minio` (default) |
| Secondary BSL | `b2` (Backblaze B2, DR use only) |
| B2 bucket | `vollminlab-k8s-backups` |
| B2 endpoint | `https://s3.us-west-000.backblazeb2.com` |
| B2 credentials | 1Password: **Backblaze B2 - vollminlab-k8s-velero** |
| Schedule: `daily-full` | 02:00 daily → MinIO, 14-day retention |
| Schedule: `daily-b2` | 04:00 daily → B2, 90-day retention |
| Schedule: `monthly-b2` | 06:00 on 1st of month → B2, 1-year retention |

### Checking backup status

```bash
# List all backups
velero backup get

# Check schedule status
velero schedule get

# Describe a specific backup
velero backup describe <backup-name> --details

# Check backup logs
velero backup logs <backup-name>

# Check node-agent (kopia) status
kubectl get pods -n velero -l app=velero-node-agent
```

### Triggering a manual backup

```bash
# Full cluster backup (all namespaces)
velero backup create manual-$(date +%Y%m%d) --storage-location minio

# Single namespace backup
velero backup create mediastack-$(date +%Y%m%d) --include-namespaces mediastack --storage-location minio
```

### Restore procedure

#### Normal restore (MinIO available)

```bash
# List available backups
velero backup get

# Restore a full backup
velero restore create --from-backup <backup-name>

# Restore a single namespace
velero restore create --from-backup <backup-name> --include-namespaces mediastack

# Check restore status
velero restore get
velero restore describe <restore-name>
```

#### DR restore (MinIO unavailable — use B2 directly)

```bash
# Switch Velero to use the B2 BSL as default
kubectl patch backupstoragelocation b2 -n velero \
  --type merge -p '{"spec":{"default":true}}'
kubectl patch backupstoragelocation minio -n velero \
  --type merge -p '{"spec":{"default":false}}'

# Velero will sync available backups from B2 within ~1 minute
velero backup get

# Restore as normal
velero restore create --from-backup <backup-name>
```

#### Full cluster rebuild restore

1. Bootstrap the cluster (CNI, Flux, Sealed Secrets key from 1Password)
2. Let Flux reconcile all namespaces from Git — this recreates all deployments
3. Deploy MinIO first: `flux reconcile kustomization minio --with-source`
4. Deploy Velero: `flux reconcile kustomization velero --with-source`
5. Point Velero at B2 (MinIO will be empty after rebuild — see DR restore above)
6. Restore stateful namespaces: `velero restore create --from-backup <backup-name>`
7. Scale down and back up affected deployments to remount restored PVCs

---

## Infrastructure Services

### cloudflared (Cloudflare Tunnel)

| Parameter | Value |
|---|---|
| Chart | TrueCharts cloudflared 16.1.1 (OCIRepository) |
| Namespace | `mediastack` |
| Tunnel token | SealedSecret `cloudflared-tunnel-credentials` (1Password: "Cloudflare Tunnel Token - vollminlab") |
| CPU | req: 50m, limits: 200m |
| Memory | req: 64Mi, limits: 128Mi |

Two separate tunnels are deployed — one per externally-accessible media service, for independent blast-radius and revocability.

#### cloudflared (Plex tunnel)

| Parameter | Value |
|---|---|
| Deployment | `cloudflared` in `mediastack` |
| Tunnel token | SealedSecret `cloudflared-tunnel-credentials` (1Password: "Cloudflare Tunnel Token - vollminlab") |

| Hostname | Internal target |
|---|---|
| `plex.vollminlab.com` | `http://plex.mediastack.svc.cluster.local:32400` |

#### cloudflared-jellyfin (Jellyfin tunnel)

| Parameter | Value |
|---|---|
| Deployment | `cloudflared-jellyfin` in `mediastack` |
| Tunnel token | SealedSecret `cloudflared-jellyfin-tunnel-credentials` (1Password: store after sealing) |
| CPU | req: 50m, limits: 200m |
| Memory | req: 64Mi, limits: 128Mi |

| Hostname | Internal target |
|---|---|
| `jellyfin.vollminlab.com` | `http://jellyfin.mediastack.svc.cluster.local:8096` |

**DNS split:** Internal requests resolve via Pi-hole to `192.168.152.244` (ingress VIP). External requests hit Cloudflare edge → tunnel → cluster service. No inbound ports on the router.

#### cloudflared-authentik (Authentik SSO tunnel)

| Parameter | Value |
|---|---|
| Deployment | `cloudflared-authentik` in `authentik` |
| Image | `cloudflare/cloudflared:2026.3.0` |
| Tunnel token | SealedSecret `cloudflared-authentik-tunnel-credentials` (1Password: "Cloudflare Authentik Tunnel") |
| Protocol | `--protocol http2` (pinned; keeps all edge connections on TCP) |
| CPU | req: 50m, limits: 200m |
| Memory | req: 64Mi, limits: 128Mi |

| Hostname | Internal target |
|---|---|
| `authentik.vollminlab.com` | nginx ingress VIP (forward-auth outpost) |

**Egress requirement — port 7844:** unlike the `mediastack` tunnels, the `authentik`
namespace runs a default-deny egress NetworkPolicy. cloudflared connects to the
Cloudflare edge on **port 7844** (QUIC over UDP *and* http2 over TCP), so the
`allow-external-egress` policy must permit **TCP + UDP 7844** — not just 443.
Forcing `--protocol http2` does **not** avoid this; http2 also dials 7844, just over
TCP. With 7844 blocked the tunnel degrades to a single TCP-443 fallback connection
(1/4 HA) and Cloudflare reports it degraded; pod logs show `dial tcp <edge-ip>:7844:
i/o timeout`. Fixed in PR #889. See `.claude/rules/networkpolicy.md` for the
per-namespace port table.

---

### metrics-server

| Parameter | Value |
|---|---|
| Chart version | 3.13.0 |
| Helm repo | https://kubernetes-sigs.github.io/metrics-server/ |
| kubelet-insecure-tls | true |
| kubelet preferred address types | InternalIP, Hostname, InternalDNS |
| Metric resolution | 15s |
| CPU | req: 50m, limits: 200m |
| Memory | req: 64Mi, limits: 128Mi |

### Shlink (URL Shortener)

| Parameter | Value |
|---|---|
| Namespace | shlink |
| Backend chart | shlink-backend v11.0.5 (christianhuth) |
| Backend app version | Shlink 5.0.1 |
| Web client chart | shlink-web v1.11.0 (christianhuth) |
| Web client app version | shlink-web-client 4.7.0 |
| Helm repo | https://charts.christianhuth.de |
| Short domains | `vollm.in` (primary), `go.vollminlab.com`, `vl.vollminlab.com` |
| Management UI | `shlink.vollminlab.com` |
| Database | PostgreSQL (Bitnami subchart, bundled in shlink-backend) |
| DB credentials | SealedSecret: `shlink-credentials` |
| Backend CPU | req: 100m, limits: 500m |
| Backend memory | req: 256Mi, limits: 512Mi |
| PostgreSQL CPU | req: 100m, limits: 500m |
| PostgreSQL memory | req: 256Mi, limits: 512Mi |
| Web client CPU | req: 10m, limits: 100m |
| Web client memory | req: 32Mi, limits: 64Mi |
| Redirect on 404 | `https://homepage.vollminlab.com` |
| Redirect status | 302 |

**Short links inventory** (`vollm.in/<slug>` → destination; also accessible via `go.vollminlab.com/<slug>`):

*Cluster apps:*

| Slug | Destination |
|---|---|
| homepage | https://homepage.vollminlab.com |
| headlamp | https://headlamp.vollminlab.com |
| longhorn | https://longhorn.vollminlab.com |
| policyreporter | https://policyreporter.vollminlab.com |
| radarr | https://radarr.vollminlab.com |
| sonarr | https://sonarr.vollminlab.com |
| sabnzbd | https://sabnzbd.vollminlab.com |
| prowlarr | https://prowlarr.vollminlab.com |
| bazarr | https://bazarr.vollminlab.com |
| overseerr | https://overseerr.vollminlab.com |
| tautulli | https://tautulli.vollminlab.com |
| portainer | https://portainer.vollminlab.com |
| shlink | https://shlink.vollminlab.com |

*Infrastructure services:*

| Slug | Destination |
|---|---|
| pihole | https://pihole.vollminlab.com |
| npm | https://npm.vollminlab.com |
| plex | https://plex.vollminlab.com |
| truenas | https://truenas.vollminlab.com |
| udm | https://udm.vollminlab.com |
| vcenter | https://vcenter.vollminlab.com |
| haproxy | https://haproxy.vollminlab.com |

*DMZ / Gaming:*

| Slug | Destination | Notes |
|---|---|---|
| bluemap | https://bluemap.vollminlab.com | Externally accessible via DDNS (`dynamic.vollminlab.com` → public WAN IP → haproxydmz) |

> Short links are configured via the Shlink web UI at `shlink.vollminlab.com` — they are not stored in Git.

---

### Actions Runner Controller (ARC v2)

**Controller** (`arc-controller` namespace):

| Parameter | Value |
|---|---|
| Chart | gha-runner-scale-set-controller v0.14.0 (OCIRepository) |
| Replicas | 2 |
| Watches namespace | actions-runner-system |
| Controller CPU | req: 50m, limits: 500m |
| Controller memory | req: 64Mi, limits: 256Mi |

**Runner scale set** (`actions-runner-system` namespace):

| Parameter | Value |
|---|---|
| Chart | gha-runner-scale-set v0.14.0 (OCIRepository) |
| Scale set name | vollminlab |
| GitHub scope | org (github.com/vollminlab) |
| Auth | GitHub App (sealed secret: `arc-githubapp-secret`) |
| Min runners | 4 |
| Max runners | 10 |
| Runner image | `ghcr.io/actions/actions-runner:2.332.0` |
| DinD sidecar | `docker:26-dind` (privileged, tcp://localhost:2375) |
| Runner CPU | req: 500m, limits: 2000m |
| Runner memory | req: 512Mi, limits: 2Gi |

---

## CNPG (CloudNative-PG)

Operator deployed in `cnpg-system`. Manages PostgreSQL clusters in other namespaces (authentik, harbor, mediastack/jellystat, shlink).

### Container ports — cnpg-system operator pod

| Port | Name | Purpose | NetworkPolicy rule |
|------|------|---------|-------------------|
| 9443 | `webhook-server` | Admission webhook — kube-apiserver calls this when validating CNPG CRs | `allow-webhook-ingress` ingress from CP node IPs |
| 8080 | `metrics` | Prometheus metrics scrape | `allow-monitoring-scrape` ingress from `monitoring` ns |

### Container ports — CNPG instance pods (all namespaces)

| Port | Name | Purpose | NetworkPolicy rule |
|------|------|---------|-------------------|
| 5432 | `postgresql` | Client connections | per-namespace allow rule |
| 9187 | `metrics` | Prometheus metrics | per-namespace allow rule |
| 8000 | `status` | Instance manager status API — polled by CNPG operator | `allow-instance-status-egress` egress from cnpg-system |

**Important**: `cnpg-webhook-service` exposes port `443` → `targetPort: 9443`. NetworkPolicies must use the container port `9443`, not the service port `443`. See `.claude/rules/networkpolicy.md` for the port-trap explanation.

---

## Media Stack

All apps in the `mediastack` namespace. Shared SMB storage mounted at the namespace level. App configs stored on Longhorn (5Gi RWO each, except Tautulli at 1Gi).

### Sonarr (TV automation)

| Parameter | Value |
|---|---|
| Source | OCIRepository |
| Ingress | `sonarr.vollminlab.com` |
| Port | 8989 |
| Config PVC | 5Gi Longhorn RWO |
| Volumes | pvc-tv (RWX), pvc-completed-downloads (RWX) |

### Radarr (Movie automation)

| Parameter | Value |
|---|---|
| Source | OCIRepository |
| Ingress | `radarr.vollminlab.com` |
| Port | 7878 |
| Config PVC | 5Gi Longhorn RWO |
| Volumes | pvc-movies (RWX), pvc-completed-downloads (RWX) |

### SABnzbd (Usenet downloader)

| Parameter | Value |
|---|---|
| Source | OCIRepository |
| Ingress | `sabnzbd.vollminlab.com` |
| Port | 10097 |
| Config PVC | 5Gi Longhorn RWO |
| Volumes | pvc-completed-downloads (RWX), pvc-incomplete-downloads (RWX) |

### Prowlarr (Indexer aggregation)

| Parameter | Value |
|---|---|
| Source | OCIRepository |
| Ingress | `prowlarr.vollminlab.com` |
| Port | 9696 |
| Config PVC | 5Gi Longhorn RWO |

### Bazarr (Subtitle management)

| Parameter | Value |
|---|---|
| Chart version | v11.1.1 |
| Ingress | `bazarr.vollminlab.com` |
| Port | 6767 |
| Config PVC | 5Gi Longhorn RWO |
| Volumes | pvc-movies (RWX), pvc-tv (RWX) |

### Overseerr (Media requests)

| Parameter | Value |
|---|---|
| Source | OCIRepository |
| Ingress | `overseerr.vollminlab.com` |
| Port | 5055 |
| Config PVC | 5Gi Longhorn RWO |

### Jellyfin (Media server)

| Parameter | Value |
|---|---|
| Chart | jellyfin/jellyfin 3.2.0 (HelmRepository: <https://jellyfin.github.io/jellyfin-helm/>) |
| App version | 10.11.8 |
| Ingress | `jellyfin.vollminlab.com` |
| Port | 8096 |
| Config PVC | `pvc-jellyfin-config` 20Gi Longhorn RWO |
| Volumes | `pvc-movies` at `/movies` (RWX), `pvc-tv` at `/tv` (RWX) |
| UID/GID | 568 |
| External access | Cloudflare Tunnel via `cloudflared-jellyfin` Deployment (separate from Plex tunnel) |
| Security gate | Jellyfin built-in auth only — no Cloudflare Access (native apps require no browser challenge) |
| Public signup | Disabled — accounts created manually by admin |
| Hardware transcoding | Deferred — CPU only for initial deployment |

### Plex (Media server)

| Parameter | Value |
|---|---|
| Chart | TrueCharts plex 22.1.2 (OCIRepository) |
| Ingress | `plex.vollminlab.com` |
| Port | 32400 |
| Config PVC | 20Gi Longhorn RWO |
| Volumes | pvc-movies (RWX), pvc-tv (RWX) |
| Allowed networks | `172.16.0.0/12, 10.0.0.0/8, 192.168.0.0/16` |
| External access | Cloudflare Tunnel via cloudflared (see Infrastructure Services) |
| Notes | Claim server via web UI on first launch from same-LAN browser |

### Tautulli (Plex monitoring)

| Parameter | Value |
|---|---|
| Chart version | v11.3.1 |
| Ingress | `tautulli.vollminlab.com` |
| Port | 8181 |
| Config PVC | 1Gi Longhorn RWO |

### Shared Secrets

| Secret | Contents |
|---|---|
| `smb-credentials` | SMB username/password for NAS mounts at `192.168.150.2` |

---

## Applications

### Homepage Dashboard

| Parameter | Value |
|---|---|
| Chart version | v2.1.0 |
| Helm repo | https://jameswynn.github.io/helm-charts/ |
| Ingress | `homepage.vollminlab.com` |
| Port | 3000 |
| Mode | cluster |
| Theme | dark |
| CPU | req: 100m, limits: 500m |
| Memory | req: 256Mi, limits: 512Mi |
| Allowed hosts | `homepage.vollminlab.com, localhost, 127.0.0.1` |

**Service Groups configured:**

| Group | Services |
|---|---|
| Media Stack | Plex, Overseerr, Tautulli, Sonarr, Radarr, Prowlarr, SABnzbd |
| Infrastructure | Pi-hole, TrueNAS, vCenter, Portainer, Nginx Proxy Manager, UDM, HAProxy stats |
| Monitoring | Grafana, Prometheus |
| Documentation | BookStack, Homepage, GitHub repo, ChatGPT, Reddit, Chocolatey |
| Personal | Yahoo Fantasy Football, ESPN Fantasy Football, D.E. Shaw Access, GroupMe, MakerWorld |

**Widgets:** Google search, resource usage (CPU/memory), datetime, greeting, OpenWeatherMap (imperial, 5-min cache).

**Secret:** `homepage-env-vars` (SealedSecret) — API keys, weather coordinates, service credentials.

### Portainer

| Parameter | Value |
|---|---|
| Chart version | v1.0.59 |
| Helm repo | https://portainer.io/helm |
| Service type | ClusterIP |
| Config PVC | 10Gi local-path RWO |
| Edge agent | enabled (tunnel port 30776) |
| Security context | runAsUser=0 (root — required by Portainer) |
| CPU | req: 100m, limits: 100m |
| Memory | req: 128Mi, limits: 128Mi |

---

## DMZ — Isolated Workloads

The `dmz` namespace is a security boundary for internet-exposed workloads. Full model documented in [clusters/vollminlab-cluster/dmz/README.md](../clusters/vollminlab-cluster/dmz/README.md).

### Security Layers

| Layer | Mechanism |
|---|---|
| Physical isolation | Dedicated node `k8sworker05` |
| Node isolation | Taint `dmz=true:NoSchedule`, label `role=dmz` |
| Admission control | Kyverno `dmz-enforce-node-placement` — auto-injects nodeSelector + toleration |
| Admission control | Kyverno `dmz-restrict-external-access` — blocks external-access labels outside dmz |
| Network | Default-deny NetworkPolicy; explicit allow rules only |
| Pod security | Namespace-level: `enforce=baseline, audit=restricted, warn=restricted` |
| Storage | Dedicated `longhorn-dmz` StorageClass — nodes with `dmz` selector only |

### Network Policies

| Policy | Rule |
|---|---|
| `default-deny-all` | Block all ingress and egress |
| `allow-dns` | Allow egress to `10.96.0.10:53` UDP/TCP |
| `allow-external-ingress` | Allow ingress from `0.0.0.0/0` for pods labeled `external-access=true` |
| `allow-internet-egress` | Allow egress to internet (non-RFC1918, non-link-local, non-loopback) for pods labeled `internet-egress=true` |

### Minecraft Server

| Parameter | Value |
|---|---|
| Chart version | v4.0.0 |
| Helm repo | https://itzg.github.io/minecraft-server-charts/ |
| Image | `itzg/minecraft-server:java21` |
| Server type | PAPER |
| JVM memory | 6G |
| CPU | req: 2000m, limits: 4000m |
| Memory | req: 6Gi, limits: 8Gi |
| Config PVC | 20Gi `longhorn-dmz` RWX |
| View distance | 8 |
| Simulation distance | 6 |
| Max players | 20 |
| Difficulty | normal |
| Max world size | 29,999,984 |
| RCON | enabled (sealed secret: `minecraft-rcon-secret`) |
| Plugins | BlueMap v5.13 (spigot) |
| Service type | NodePort |
| Minecraft port | NodePort `32565` |
| BlueMap port | NodePort `32566` (container port 8100) |

**Allowed ingress:** HAProxy nodes `192.168.160.2/32` and `192.168.160.3/32` on ports 25565 (game) and 8100 (BlueMap).

**Allowed egress:** `0.0.0.0/0` on ports 80, 443 (downloads/updates) + DNS to `10.96.0.10:53`.

**Probes:**
- Readiness: initialDelay=30s, period=10s, failureThreshold=10
- Liveness: initialDelay=30s, period=5s, failureThreshold=10

---

## CI/CD

### GitHub Actions Workflows

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yaml` | PR + push to main | kustomize build validation, Kyverno policy checks, Trivy security scan |
| `codeql.yml` | Schedule + push | CodeQL security analysis |

### Branch Protection

| Rule | Value |
|---|---|
| Required reviews | 1 |
| Dismiss stale reviews | true |
| CI required | yes (ci.yaml must pass) |
| Admin enforcement | enabled |
| Require conversation resolution | true |
| Force push | blocked |
| Branch deletion | blocked |
| Config source | GitHub repository settings |

### Self-Hosted Runners (ARC)

CI runs on self-hosted runners via ARC v2 (gha-runner-scale-set) in `actions-runner-system`. Scale set name: `vollminlab`. Min 4 / max 10 runners. Jobs target the `vollminlab` runner group label.
