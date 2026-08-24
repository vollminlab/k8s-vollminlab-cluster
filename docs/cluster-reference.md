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
11. [Infrastructure as Code — tofu-controller](#infrastructure-as-code--tofu-controller)
12. [Monitoring & Observability](#monitoring--observability)
13. [Maintenance CronJobs](#maintenance-cronjobs)
14. [Media Stack](#media-stack)
15. [Applications](#applications)
16. [DMZ — Isolated Workloads](#dmz--isolated-workloads)
17. [CI/CD](#cicd)

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

### 1Password Connect Credentials

The DR-critical root secret. Everything else is materialized from it, so it must exist before
anything that consumes a Secret can start.

| Parameter | Value |
|---|---|
| Secret name | `onepassword-connect` |
| Namespace | `1password` |
| Keys | `1password-credentials.json`, `token` |
| Backed up in | 1Password (Homelab vault) |
| Flux-managed | **No** — it would be a chicken-and-egg dependency |

Must be applied **before** Flux bootstraps on a new cluster, so 1Password Connect and ESO come up
able to sync every other Secret from the vault.

```bash
# Apply before Flux bootstrap
kubectl create namespace 1password
kubectl create secret generic onepassword-connect -n 1password \
  --from-file=1password-credentials.json=<from-1password> \
  --from-literal=token=<from-1password>
```

### Bootstrap Order

```
1. Install Kubernetes control plane (kubeadm)
2. Install Calico CNI              → bootstrap/calico/README.md
3. Apply CoreDNS custom config     → bootstrap/coredns/coredns-configmap.yaml
4. Apply the onepassword-connect Secret → .claude/rules/secrets.md (must precede Flux bootstrap)
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
| `restrict-default-namespace` | enforce | validate | Block all workloads in `default` namespace |
| `restrict-privileged` | enforce | validate | Block privileged containers; exempts: kube-system, calico-system, longhorn-system, metallb-system, csi-driver, tigera-operator, ingress-nginx |
| `restrict-hostpath-usage` | enforce | validate | Block hostPath volumes; exempts: kube-system, calico-system, longhorn-system, monitoring, tigera-operator, velero |
| `restrict-latest-tag` | enforce | validate | Block `:latest` image tags on Deployment/StatefulSet/DaemonSet |
| `restrict-loadbalancer-services` | enforce | validate | LoadBalancer type only allowed in `ingress-nginx`, `dmz`, `harbor` and `ci-test-*` namespaces |
| `require-standard-labels` | enforce | validate | Require `app`, `env`, `category` labels on Deployments, StatefulSets, DaemonSets, Pods, Namespaces, Services; exempts: kube-system, kyverno |
| `require-resources` | enforce | validate | Require CPU + memory **requests** and a **memory limit** on every container; exempts Flux deployments. A CPU limit is deliberately not required — see below |
| `restrict-image-registries` | enforce | validate | Images must come from: harbor.vollminlab.com, ghcr.io, quay.io, registry.k8s.io, docker.io, mirror.gcr.io, oci.trueforge.org, reg.kyverno.io, us-docker.pkg.dev (short names without a domain also allowed) |
| `dmz-restrict-external-access` | enforce | validate | Block `external-access=true` and `internet-egress=true` labels outside `dmz` namespace |
| `dmz-enforce-node-placement` | audit | mutate | Auto-inject `nodeSelector: role=dmz` and toleration `dmz=Exists:NoSchedule` on all pods in `dmz` namespace |
| `inject-namespace-labels` | audit | mutate | Auto-copy `app`, `env`, `category` labels from namespace to workloads (no namespace exclusions) |
| `inject-pod-labels` | audit | mutate | Auto-copy the same labels onto pods |
| `inject-resource-requirements` | audit | mutate | Inject resources for Longhorn workloads the chart cannot set: csi-attacher, csi-provisioner, csi-resizer, csi-snapshotter, longhorn-ui, longhorn-manager |
| `mutate-default-sa-automount` | audit | mutate | Disable token automount on the `default` ServiceAccount |
| `mutate-default-sa-pod-automount` | audit | mutate | Same, for pods that use the `default` ServiceAccount |

Every mutate policy is Audit — a mutation either applies or it does not, so there is
nothing for it to block.

**Why `require-resources` does not require a CPU limit.** CPU throttling degrades
latency-sensitive workloads and cannot protect a node from exhaustion the way a memory
limit does. `coredns` and `kyverno-admission-controller` both omit theirs upstream and
pass the policy unchanged. Set requests plus a memory limit; add a CPU limit only for a
specific reason.

### PolicyExceptions

| Name | Scope |
|---|---|
| `ignore-flux-core` | All Flux controllers and the `kyverno` HelmRelease, from all policies |
| `ignore-calico-cni` | Calico / tigera-operator workloads |
| `ignore-longhorn-dynamic-pods` | Longhorn objects created by the controller, not by GitOps |
| `ignore-tailscale-connector` | Tailscale Connector StatefulSets |
| `ignore-kyverno-hook-pods` | Kyverno's own Helm hook pods |
| `ignore-kubeadm-cert-renew` | kubeadm certificate-renewal pods |
| `ignore-ci-test-namespaces` | `ci-test-*` namespaces, for the label policy |
| `ignore-vendor-resource-requirements` | 17 name patterns covering 21 live vendor workloads, `require-resources` only — each entry carries its measured 30d memory peak. Also scoped to `ci-test-*`, because CI dry-runs charts into an ephemeral namespace where the production scope would not match |

**A policy with zero pass results is enforcing nothing.** `foreach.list` is a JMESPath
against the rule context and must be rooted at `request.object`; a bare
`spec.template.spec.containers` resolves to null, the loop iterates nothing, and the rule
emits no result at all — so Policy Reporter shows 0 fail because it also shows 0 pass.
`require-resources`, `restrict-image-registries` and `inject-resource-requirements` were
all silently inert this way until 2026-08-19 (#1104, #1107, #1109).

### Policy Reporter

| Parameter | Value |
|---|---|
| Chart version | v3.1.3 |
| Helm repo | https://kyverno.github.io/policy-reporter/ |
| Ingress | `policyreporter.vollminlab.com` |
| TLS | wildcard-tls |
| UI resources | req: 50m/64Mi, limits: 100m/128Mi |
| Reporter resources | req: 50m/64Mi, limits: 100m/128Mi |

### Secrets — External Secrets Operator + 1Password Connect

Every Secret in the cluster is materialized by ESO from 1Password. The repo holds only
`ExternalSecret` CRs that reference vault items by name — never the values.

```
1Password (Homelab vault)
  └─ 1Password Connect        (1password ns)
       └─ ClusterSecretStore  (onepassword-cluster-store)
            └─ ExternalSecret (per app, in the app's own namespace)
                 └─ Secret    (creationPolicy: Owner)
```

| Parameter | Value |
|---|---|
| ESO namespace | `external-secrets` |
| Connect namespace | `1password` |
| ClusterSecretStore | `onepassword-cluster-store` (cluster-scoped — usable from any namespace) |
| Default refresh interval | 1h |

> SealedSecrets was retired on 2026-05-31 and the controller removed. There are no SealedSecrets in
> the repo and none may be added — a committed SealedSecret would never reconcile.
> `bootstrap/sealed-secrets/` is kept as historical reference only.

---

## GitOps — Flux CD

### Sync Configuration

| Parameter | Value |
|---|---|
| GitRepository | `https://github.com/svollmi1/k8s-vollminlab-cluster.git` |
| Branch | `main` |
| Pull interval | 1 minute |
| Auth | SSH key in the `flux-system` Secret (created by `flux bootstrap`) |
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
| `longhorn-system` | `./clusters/vollminlab-cluster/longhorn-system` | |
| `mediastack` | `./clusters/vollminlab-cluster/mediastack` | |
| `metallb-system` | `./clusters/vollminlab-cluster/metallb-system` | |
| `minio` | `./clusters/vollminlab-cluster/minio` | |
| `monitoring` | `./clusters/vollminlab-cluster/monitoring` | kube-prometheus-stack, Loki, Promtail |
| `policy-reporter` | `./clusters/vollminlab-cluster/kyverno` | |
| `policy-reporter-patch` | patch only | |
| `portainer` | `./clusters/vollminlab-cluster/portainer` | |
| `renovate` | `./clusters/vollminlab-cluster/renovate` | |
| `shlink` | `./clusters/vollminlab-cluster/shlink` | |
| `velero` | `./clusters/vollminlab-cluster/velero` | |
| `vollmint` | `./clusters/vollminlab-cluster/vollmint` | |

### Headlamp (Kubernetes UI)

| Parameter | Value |
|---|---|
| Chart | headlamp v0.41.0 (kubernetes-sigs.github.io/headlamp) |
| Namespace | flux-system |
| Ingress | `headlamp.vollminlab.com` |
| TLS | wildcard-tls |
| Plugin | headlamp-plugin-flux v0.7.0 (init container) |
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
| longhorn-repo | HelmRepository | https://charts.longhorn.io |
| metallb-repo | HelmRepository | https://metallb.github.io/metallb |
| metrics-server-repo | HelmRepository | https://kubernetes-sigs.github.io/metrics-server/ |
| minecraft-repo | HelmRepository | https://itzg.github.io/minecraft-server-charts/ |
| minio-repo | HelmRepository | https://charts.min.io/ |
| portainer-repo | HelmRepository | https://portainer.github.io/k8s |
| prometheus-community-repo | HelmRepository | https://prometheus-community.github.io/helm-charts |
| prowlarr-repo | OCIRepository | oci://oci.trueforge.org/truecharts/prowlarr |
| radarr-repo | OCIRepository | oci://oci.trueforge.org/truecharts/radarr |
| renovate-repo | OCIRepository | oci://ghcr.io/renovatebot/charts/renovate |
| sabnzbd-repo | OCIRepository | oci://oci.trueforge.org/truecharts/sabnzbd |
| shlink-repo | HelmRepository | https://charts.christianhuth.de |
| smb-csi-driver-repo | HelmRepository | https://raw.githubusercontent.com/kubernetes-csi/csi-driver-smb/master/charts |
| sonarr-repo | OCIRepository | oci://oci.trueforge.org/truecharts/sonarr |
| velero-repo | HelmRepository | https://vmware-tanzu.github.io/helm-charts |
| vollminlab-repo | OCIRepository | oci://harbor.vollminlab.com/vollminlab/charts/shlink-ingress-controller |
| vollmint-repo | OCIRepository | oci://harbor.vollminlab.com/vollminlab/charts/vollmint (tag: 0.1.0) |

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
| `letsencrypt-cloudflare` | ACME (DNS-01) | Let's Encrypt production; Cloudflare API token from the `cloudflare-api-token` Secret (ESO) |
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
| `jellyfin.vollminlab.com` | jellyfin | 8096 | mediastack | wildcard-tls |
| `go.vollminlab.com` | shlink-shlink-backend | 8080 | shlink | wildcard-tls |
| `vl.vollminlab.com` | shlink-shlink-backend | 8080 | shlink | wildcard-tls |
| `vollm.in` | shlink-shlink-backend | 8080 | shlink | vollm-in-tls (Let's Encrypt) |
| `minio.vollminlab.com` | minio | 9001 | minio | wildcard-tls |
| `vollmint.vollminlab.com` | vollmint | 8080 | vollmint | wildcard-tls |

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
| `pvc-jellyfin-config` | mediastack | 20Gi | longhorn | RWO |
| `pvc-minecraft-datadir` | dmz | 20Gi | longhorn-dmz | RWX |
| `portainer` | portainer | 1Gi | longhorn | RWO |
| `minio` | minio | 75Gi | longhorn | RWO |

---

### Longhorn maintenance jobs

Longhorn never reclaims freed-but-untrimmed blocks on its own, and it prunes no snapshots by
default. Both of these are native `RecurringJob` CRs rather than CronJobs — deliberately, because a
`RecurringJob` is covered by Longhorn's own NetworkPolicy selectors by construction and therefore
cannot be cut off by a future chart change. An external CronJob calling `longhorn-backend:9500` can
be, and was: Longhorn 1.12.1 shipped policies that silently reduced the old trim job to trimming
zero volumes while still exiting 0.

| App dir | CR name | Task | Cron (UTC) | Notes |
|---|---|---|---|---|
| `longhorn-trim` | `filesystem-trim` | `filesystem-trim` | `0 6 * * *` | concurrency 2 |
| `longhorn-snapshot-retention` | `snapshot-retention` | `snapshot-delete` | `0 12 * * *` | retain 3, concurrency 2 |

### longhorn-mount-healer

| Parameter | Value |
|---|---|
| Namespace | `kube-system` |
| Kind | CronJob |
| Image | `docker.io/alpine/kubectl:1.33.4` |
| Schedule | `*/10 * * * *` |
| Purpose | Auto-clears Longhorn stale-mount crashloops (EIO on a detached volume) |
| RBAC | ClusterRole + ClusterRoleBinding + ServiceAccount |

### longhorn-rebalancing-controller

| Parameter | Value |
|---|---|
| Namespace | `longhorn-system` |
| Source | OCIRepository — `oci://harbor.vollminlab.com/vollminlab/charts/longhorn-rebalancing-controller` |
| Chart tag | `0.4.0` |
| Purpose | In-house Go controller that evens replica distribution across nodes |
| Verified | Convergence 2026-07-25 — peak node utilization 90.8% → 74.0% |

### VolSync — PVC replication to B2

| Parameter | Value |
|---|---|
| Namespace | `volsync-system` |
| Chart | volsync 0.16.0 (HelmRepository) |
| Sources | 13 `ReplicationSource` CRs, restic to Backblaze B2 |
| copyMethod | `Clone` — this cluster has no VolumeSnapshotClass |
| Clone StorageClass | `longhorn-r1` — creates and deletes ~70 GiB of clone PVCs nightly |
| Contract | A PVC labelled `backup.vollminlab.com/volsync: "true"` is skipped by Velero |

`longhorn-r1` **must** stay `reclaimPolicy: Delete`. It is not a user-facing class; blanket `Retain`
would orphan 13 Released PVs and ~70 GiB of Longhorn scheduling claims every night.

### csi-snapshot-crds

| Parameter | Value |
|---|---|
| Namespace | `volsync-system` |
| Kind | CustomResourceDefinition set only |
| Purpose | External-snapshotter CRDs required by VolSync; deployed separately from the chart |

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
| Buckets | `velero`, `loki`, `cnpg-backups`, `terraform-state` (auto-provisioned on startup) |
| Service accounts | `cnpg-svc` (cnpg-policy, `cnpg-backups` only), `tofu-svc` (tofu-state-policy, `terraform-state` only) |

### cnpg-b2-mirror (CNPG offsite copy)

CronJob in the `minio` namespace that mirrors the `cnpg-backups` bucket to Backblaze B2.

| Parameter | Value |
|---|---|
| Manifests | `./clusters/vollminlab-cluster/minio/cnpg-b2-mirror/app` |
| Image | `docker.io/minio/mc:RELEASE.2025-08-13T08-35-41Z` |
| Schedule | 09:00 UTC daily |
| Source | `s3://cnpg-backups` via the `cnpg-svc` principal (read only in practice) |
| Destination | `s3://vollminlab-k8s-cnpg` |
| B2 endpoint | `https://s3.us-west-000.backblazeb2.com` |
| B2 credentials | 1Password: **Backblaze B2 - vollminlab-k8s-cnpg** (fields `keyID`, `credential`) |
| Retention | none applied by the job; set a B2 lifecycle rule on the bucket |

**Why it exists.** CloudNativePG writes base backups and WAL to exactly one object store.
`spec.backup.barmanObjectStore` is a single object, and the newer barman-cloud plugin does not
fan out either: its second `ObjectStore` is a *recovery source* for a replica cluster, not a
second backup destination. All five clusters therefore archive only to MinIO, which puts every
restorable Postgres backup on one Longhorn PVC in one rack.

Velero's `daily-b2` does copy each `pgdata` volume to B2 nightly, but that is a filesystem walk
of a running PostgreSQL data directory with no `pg_backup_start()` / `pg_backup_stop()` bracket.
It is torn across files, carries no WAL continuity, supports no PITR, and CloudNativePG cannot
bootstrap a `Cluster` from it. It reports `Completed` every night regardless, so it reads as
protection without being any.

Mirroring the bucket sidesteps the single-destination limit without touching CNPG: what lands in
B2 is the real barman artifacts, and a recovery `Cluster` can be pointed straight at them.

**The job runs without `--remove`**, so B2 keeps objects after MinIO's retention (7d for
`authentik-db`, 14d for the other four) has expired them. The offsite copy therefore reaches
further back than the local one, and B2 growth is bounded by a bucket lifecycle rule rather than
by the mirror.

**It fails loudly rather than quietly.** It lists both endpoints before mirroring and counts
objects in the destination afterwards, exiting non-zero if the destination is empty. A mirror
that copies nothing into an empty bucket otherwise exits 0, which is indistinguishable from
"already up to date".

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

1. Bootstrap the cluster (CNI, `onepassword-connect` Secret from 1Password, then Flux)
2. Let Flux reconcile all namespaces from Git — this recreates all deployments
3. Deploy MinIO first: `flux reconcile kustomization minio --with-source`
4. Deploy Velero: `flux reconcile kustomization velero --with-source`
5. Point Velero at B2 (MinIO will be empty after rebuild — see DR restore above)
6. Restore stateful namespaces: `velero restore create --from-backup <backup-name>`
7. Scale down and back up affected deployments to remount restored PVCs

---

### velero-pvb-healer

| Parameter | Value |
|---|---|
| Namespace | `velero` |
| Kind | CronJob |
| Image | `docker.io/alpine/kubectl:1.33.4` |
| Schedule | `*/10 * * * *` |
| Tunables | `STALL_SECONDS` 900, `COOLDOWN_SECONDS` 3600, `NODE_AGENT_SELECTOR` `name=node-agent`, `DRY_RUN` false |

Heals the node-agent datapath-slot leak that freezes a PodVolumeBackup in `Prepared` with no error
anywhere, head-of-line blocking the whole backup until `itemOperationTimeout` expires 4h later. Each
run heals **at most one node**, and **skips any node with a PVB `InProgress`** — under
`loadConcurrency: 1` a node legitimately has one running while others wait, so without that guard
the healer would kill a healthy in-flight backup.

### velero-backup-content-guard

| Parameter | Value |
|---|---|
| Namespace | `velero` |
| Kind | CronJob |
| Image | `docker.io/alpine/kubectl:1.33.4` |
| Schedule | `0 9 * * *` |

Alerts when a schedule captures nothing or stops running. It exists because **an empty Velero backup
reports `Completed`, `0 errors`** — phase is the only signal Velero exposes, and a schedule whose
`labelSelector` matches zero volumes looks identical to a successful one. `velero-victoria-metrics-b2`
did exactly that for its entire life.

## Infrastructure Services

### cloudflared (Cloudflare Tunnel)

| Parameter | Value |
|---|---|
| Chart | TrueCharts cloudflared 16.1.1 (OCIRepository) |
| Namespace | `mediastack` |
| Tunnel token | ExternalSecret `cloudflared-tunnel-credentials` (1Password: "Cloudflare Tunnel Token - vollminlab") |
| CPU | req: 50m, limits: 200m |
| Memory | req: 64Mi, limits: 128Mi |

**Four independent tunnels are deployed** — one per externally-accessible service, for independent
blast-radius and revocability: `cloudflared-jellyfin` and `cloudflared-audiobookshelf` in
`mediastack`, `cloudflared-nginx` in `ingress-nginx`, and `cloudflared-authentik` in `authentik`.

A fifth, the original bare `cloudflared` Deployment, served the Plex tunnel and was removed with
Plex on 2026-05-09.

> **A cloudflared tunnel needs two egress rules, not one.** Reaching the Cloudflare edge (7844) and
> reaching the tunnel's *origin* are separate hops, and only the first fails loudly — the Cloudflare
> dashboard shows the tunnel healthy while every request 502s. LAN clients mask this completely,
> because Pi-hole's A-record override sends them straight to the ingress VIP and never through the
> tunnel, so the breakage is visible only from outside. This went unnoticed for ~68 days.

#### cloudflared-jellyfin (Jellyfin tunnel)

| Parameter | Value |
|---|---|
| Deployment | `cloudflared-jellyfin` in `mediastack` |
| Tunnel token | ExternalSecret `cloudflared-jellyfin-tunnel-credentials` (1Password: "Cloudflare Jellyfin Tunnel") |
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
| Tunnel token | ExternalSecret `cloudflared-authentik-tunnel-credentials` (1Password: "Cloudflare Authentik Tunnel") |
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
| DB credentials | ExternalSecret: `shlink-credentials` |
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
| portainer | https://portainer.vollminlab.com |
| shlink | https://shlink.vollminlab.com |

*Infrastructure services:*

| Slug | Destination |
|---|---|
| pihole | https://pihole.vollminlab.com |
| npm | https://npm.vollminlab.com |
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
| Auth | GitHub App (ExternalSecret: `arc-githubapp-secret`) |
| Min runners | 4 |
| Max runners | 10 |
| Runner image | `ghcr.io/actions/actions-runner:2.332.0` |
| DinD sidecar | `docker:29-dind` (privileged, tcp://localhost:2375) |
| Runner CPU | req: 500m, limits: 2000m |
| Runner memory | req: 512Mi, limits: 2Gi |

---

### 1Password Connect

| Parameter | Value |
|---|---|
| App dir | `1password/1password-connect` |
| Namespace | `1password` |
| Chart | connect 2.4.1 (HelmRepository) |
| Container port | 8080 — API and `/metrics`. The container declares **no** ports and the Service `targetPort` is numeric 8080, so 8080 is the container port |
| NetworkPolicy | ingress from `monitoring` and `external-secrets` |

The `onepassword-connect` Secret it runs on (`1password-credentials.json` + `token`) is the
**DR-critical root secret** — not Flux-managed, and must be applied *before* Flux bootstrap or ESO
can materialize nothing.

### authentik-proxy (forward-auth outpost)

| Parameter | Value |
|---|---|
| Namespace | `authentik` |
| Image | `ghcr.io/goauthentik/proxy:2026.2.2` |
| Container port | 9000 (Service 9000 → 9000) |
| Provider | Single `forward_domain` ProxyProvider `vollminlab-forward-auth`, covering all `*.vollminlab.com` |

Every protected Ingress **must** carry `nginx.ingress.kubernetes.io/auth-snippet` setting
`X-Forwarded-Host`. nginx always sends `Host: authentik-proxy.authentik.svc.cluster.local` in
`auth_request` sub-locations, so without it the outpost cannot match the request to a provider and
returns 400 → nginx 500. Domain matching uses `cookie_domain`, not `external_host`.

### Tailscale

| Component | Value |
|---|---|
| Operator | `tailscale/tailscale-operator`, chart 1.102.3 (HelmRepository) |
| Images | `ghcr.io/tailscale/k8s-operator`, `ghcr.io/tailscale/tailscale` |
| Subnet router | `tailscale-connector/app` — `connector.yaml` is the active path |
| Tailnet service | `ingress-nginx/tailscale-svc` — Service `ingress-nginx-tailscale`, `type: LoadBalancer`, `loadBalancerClass: tailscale`, hostname `vollminlab-ingress`, port 80 |
| IaC | `tofu/tailscale-config` → `./terraform/tailscale` |

### Reloader (Stakater)

| Parameter | Value |
|---|---|
| Namespace | `reloader` |
| Chart | reloader 2.2.16 (HelmRepository) |
| Scope | Watches all namespaces |
| Opt-in | `reloader.stakater.com/auto: "true"` on Deployments, StatefulSets, DaemonSets |

Triggers rolling restarts on ConfigMap or Secret change, removing the manual
`kubectl rollout restart` step.

### Goldilocks (VPA recommender)

| Parameter | Value |
|---|---|
| Namespace | `goldilocks` |
| Chart | goldilocks 11.0.0 (HelmRepository) |
| Ingress | `goldilocks.vollminlab.com` |

Recommendations only — it does not mutate workloads. Resource limits across the cluster were
right-sized from its data.

### Trivy Operator

| Parameter | Value |
|---|---|
| Namespace | `trivy-system` |
| Chart | trivy-operator 0.35.0 (HelmRepository) |
| Produces | `VulnerabilityReport` and `ConfigAuditReport` CRs |
| Tolerations | DMZ and control-plane nodes |

### Descheduler

| Parameter | Value |
|---|---|
| Namespace | `kube-system` |
| Chart | descheduler 0.36.0 (HelmRepository) |
| Schedule | `*/30 * * * *` |
| Policy | `LowNodeUtilization` |

**Classifies on resource _requests_, not actual usage** — a node with low requests and high real
usage is treated as underutilized. Short-lived CronJobs need `backoffLimit > 0` to survive eviction.

---

## Infrastructure as Code — tofu-controller

Terraform/OpenTofu modules reconciled in-cluster by tofu-controller, one `Terraform` CR per module.

| Parameter | Value |
|---|---|
| Namespace | `tofu` |
| Chart | tofu-controller 0.16.5 (HelmRepository) |
| State backend | MinIO S3 — bucket `terraform-state` |
| Interval | 10m |

| Module | CR | Path | approvePlan |
|---|---|---|---|
| Authentik | `authentik-config` | `./terraform/authentik` | auto |
| Backblaze B2 | `b2-config` | `./terraform/b2` | auto |
| Cloudflare | `cloudflare-config` | `./terraform/cloudflare` | auto |
| Grafana | `grafana-config` | `./terraform/grafana` | auto |
| Harbor | `harbor-config` | `./terraform/harbor` | auto |
| MinIO | `minio-config` | `./terraform/minio` | auto |
| Prowlarr | `prowlarr-config` | `./terraform/prowlarr` | auto |
| Radarr | `radarr-config` | `./terraform/radarr` | auto |
| Readarr | `readarr-config` | `./terraform/readarr` | auto |
| Sonarr | `sonarr-config` | `./terraform/sonarr` | auto |
| Tailscale | `tailscale-config` | `./terraform/tailscale` | auto |

**Every module is `approvePlan: auto`**, so a merged change applies within 10 minutes with no
further gate. Two consequences:

- A Renovate **provider** bump PR is blocked by CI on purpose — merging one would apply an
  unreviewed plan. See `docs/runbooks/tofu-provider-bumps.md`.
- **Plan approval cannot go through git.** The plan id is `plan-<branch>-<sha>` and is regenerated
  by *every* repo commit, so an approval commit invalidates the plan it approves. Approval is a
  `kubectl patch`.

Never use Terraform `import` blocks in these modules — see the Harbor robot-account incident.

## CNPG (CloudNative-PG)

Operator deployed in `cnpg-system`. Manages PostgreSQL clusters in other namespaces (authentik, harbor, mediastack/jellystat, shlink, vollmint — `vollmint-db`, 2 instances × 5Gi, barman backup to MinIO at 01:45).

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

### CNPG database clusters

All use the MinIO barman object store via the scoped `cnpg-svc` user, plus WAL archiving.

| Cluster | Namespace | Instances | Storage | Scheduled backup (UTC) |
|---|---|---|---|---|
| `authentik-db` | `authentik` | 1 | 10Gi | see Authentik |
| `harbor-db` | `harbor` | 2 | 10Gi | `0 15 1 * * *` |
| `shlink-db` | `shlink` | 1 | 5Gi | `0 30 1 * * *` |
| `jellystat-db` | `mediastack` | 1 | 5Gi | `0 0 3 * * *` |
| `vollmint-db` | `vollmint` | 2 | 5Gi | see Applications |

**All five are single-sited in MinIO** — there is no offsite copy of any database. Tracked as an
open issue.

Any namespace hosting a CNPG cluster needs an `allow-cnpg-operator` NetworkPolicy admitting **both**
port 5432 and port 8000 (the instance status API). Omitting the peer is silent: `vollmint-db` went
24 days with no backup because MinIO's `allow-cnpg-backups` policy did not list it.

## Monitoring & Observability

Metrics are two-tier: Prometheus scrapes and `remote_write`s to both VictoriaMetrics instances,
holding only 24h locally itself.

### VictoriaMetrics — hot tier

| Parameter | Value |
|---|---|
| Namespace | `monitoring` |
| Chart | victoria-metrics-single 0.45.0 (HelmRepository) |
| Retention | 30d |
| Grafana | Owns the `prometheus` datasource UID, so existing dashboards query it transparently |
| Backup | **None, deliberately** — Prometheus remote-writes the same stream to both tiers, so the hot tier is an exact subset of the cold tier |

### VictoriaMetrics — cold tier (`victoria-metrics-lt`)

| Parameter | Value |
|---|---|
| Chart | victoria-metrics-single 0.45.0 (HelmRepository) |
| Retention | 395d |
| Storage | 750Gi — a dedicated PV/StorageClass on `pool_0`, **off Longhorn** |
| Backup | Own `vmbackup` CronJob, `docker.io/victoriametrics/vmbackup:v1.149.0`, `30 7 * * *` |
| Backup target | `s3://vollminlab-k8s-metrics` — **its own bucket** |

The bucket separation is load-bearing. Velero's BSL sets no prefix, so it validates the bucket's
top-level directories and rejects any it does not own. Pointing vmbackup at Velero's bucket flipped
that BSL to `Unavailable` and killed all three B2 schedules on 2026-08-17. **Any new B2 workload
gets its own bucket.**

`vmbackup` asks VictoriaMetrics for a real snapshot first; Velero file-system backup would walk a
directory that background merges are actively rewriting and produce a torn copy. That is why
`monitoring` is in `excludedNamespaces` on the Velero schedules.

### karma (Alertmanager dashboard)

| Parameter | Value |
|---|---|
| Namespace | `monitoring` |
| Source | OCIRepository |
| Image | `ghcr.io/prymitive/karma:v0.131` |
| Ingress | `karma.vollminlab.com` |
| Container port | 8080 (Service 80 → 8080) |
| NetworkPolicy | `allow-ingress-nginx` ingress from `ingress-nginx` |

### vmware-exporter

| Parameter | Value |
|---|---|
| Namespace | `monitoring` |
| Chart | vmware-exporter 2.3.0 (HelmRepository) |
| Ingress | `vcenter.vollminlab.com` |
| Ships | ServiceMonitor, PrometheusRules for host/datastore alerts, Grafana dashboards for ESXi hosts and datastores/VMs |

Credentials are a vCenter SSO account subject to a **90-day global expiry policy with no per-user
opt-out**. It expired silently once, which is what `vcenter-credential-age` now guards.

### vcenter-credential-age

| Parameter | Value |
|---|---|
| Namespace | `monitoring` |
| Kind | CronJob |
| Image | `alpine:3.23.4` |
| Schedule | `17 8 * * 1` (weekly, Monday) |

Warns before the vCenter metrics password expires. Note this is one of three bespoke
expiry monitors; the credentials behind the 78 `ExternalSecret` CRs are not covered.

### b2-exporter

| Parameter | Value |
|---|---|
| Namespace | `monitoring` |
| Image | `harbor.vollminlab.com/vollminlab/b2-exporter:1.0.2` (in-house) |
| Exposes | Backblaze B2 bucket statistics as Prometheus metrics |
| Consumed by | Homepage prometheus widget |

### bazarr-exportarr

| Parameter | Value |
|---|---|
| Namespace | `mediastack` |
| Image | `ghcr.io/onedr0p/exportarr:v2.2.0` (digest-pinned) |
| Exposes | Bazarr metrics; ServiceMonitor + PodDisruptionBudget |

Exportarr instances for Radarr, Sonarr and SABnzbd are documented under Media Stack.

---

## Maintenance CronJobs

Cluster-level scheduled maintenance outside the storage and backup namespaces.

| Job | Namespace | Image | Schedule (UTC) | Purpose |
|---|---|---|---|---|
| `etcd-defrag` | `kube-system` | `alpine/kubectl:1.33.4` | `0 3 * * *` | Defragments etcd |
| `kubeadm-cert-monitor` | `kube-system` | `alpine/kubectl:1.33.4` | `0 9 1 * *` | Monthly control-plane certificate expiry check → Pushover |
| `kubeadm-cert-renew` | `kube-system` | `alpine/kubectl:1.33.4` | `0`/`15`/`30 2 14 4,10 *` | Renews control-plane certificates — three staggered CronJobs, semi-annual (14 April / 14 October) |
| `longhorn-mount-healer` | `kube-system` | `alpine/kubectl:1.33.4` | `*/10 * * * *` | See Storage |
| `velero-pvb-healer` | `velero` | `alpine/kubectl:1.33.4` | `*/10 * * * *` | See Backup |
| `velero-backup-content-guard` | `velero` | `alpine/kubectl:1.33.4` | `0 9 * * *` | See Backup |
| `vcenter-credential-age` | `monitoring` | `alpine:3.23.4` | `17 8 * * 1` | See Monitoring |

**A merged, Ready, reconciled CronJob is not a working CronJob.** `velero-pvb-healer` ran and exited
137 on *every* invocation for one PR cycle while Flux reported success throughout. After any CronJob
change, check the job pods' exit codes — not the CronJob's status.

```bash
kubectl get jobs -n <ns> --sort-by=.metadata.creationTimestamp | tail
kubectl logs -n <ns> -l job-name=<job> --tail=20
```

## Media Stack

All apps in the `mediastack` namespace. Shared SMB storage mounted at the namespace level. App configs stored on Longhorn.

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
| External access | Cloudflare Tunnel via `cloudflared-jellyfin` Deployment (one of four independent tunnels) |
| Security gate | Jellyfin built-in auth only — no Cloudflare Access (native apps require no browser challenge) |
| Public signup | Disabled — accounts created manually by admin |
| Hardware transcoding | Deferred — CPU only for initial deployment |

### FileBrowser (File drop)

| Parameter | Value |
|---|---|
| Image | `filebrowser/filebrowser:v2.63.3` |
| Ingress | `filebrowser.vollminlab.com` |
| Config PVC | 1Gi Longhorn |
| Storage | SMB-backed — audiobooks-incoming, misc-incoming |
| Auth | Authentik forward-auth; own Cloudflare tunnel |
| IaC | Group and policy management via tofu |

### FlareSolverr (Indexer proxy)

| Parameter | Value |
|---|---|
| Image | `ghcr.io/flaresolverr/flaresolverr:v3.4.6` (digest-pinned) |
| Purpose | Cloudflare challenge solver for Prowlarr Cardigann indexers |
| Ingress | None — internal Service only |

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
| Media Stack | Jellyfin, Jellystat, Seerr, Sonarr, Radarr, Readarr, Bazarr, Prowlarr, SABnzbd, qBittorrent, Audiobookshelf |
| Infrastructure | Pi-hole, TrueNAS, vCenter, Portainer, Nginx Proxy Manager, UDM, HAProxy stats |
| Monitoring | Grafana, Prometheus |
| Documentation | BookStack, Homepage, GitHub repo, ChatGPT, Reddit, Chocolatey |
| Personal | Yahoo Fantasy Football, ESPN Fantasy Football, D.E. Shaw Access, GroupMe, MakerWorld |

**Widgets:** Google search, resource usage (CPU/memory), datetime, greeting, OpenWeatherMap (imperial, 5-min cache).

**Secret:** `homepage-env-vars` (ExternalSecret) — API keys, weather coordinates, service credentials.

### Portainer

| Parameter | Value |
|---|---|
| Chart version | v1.0.59 |
| Helm repo | https://portainer.io/helm |
| Service type | ClusterIP |
| Config PVC | 1Gi longhorn RWO |
| Edge agent | enabled (tunnel port 30776) |
| Security context | runAsUser=0 (root — required by Portainer) |
| CPU | req: 100m, limits: 100m |
| Memory | req: 128Mi, limits: 128Mi |

---

### Foundry VTT

| Parameter | Value |
|---|---|
| Namespace | `foundry` |
| Category | `gaming` |
| Chart | foundryvtt 15.2.3 (HelmRepository — charts.derwitt.dev) |
| Image | `felddy/foundryvtt` |
| Ingress | `foundry.vollminlab.com` |
| PVC | 10Gi Longhorn |
| Auth | Authentik forward-auth (domain-wide provider); per-world Foundry passwords left blank |
| External | Shared `nginx` Cloudflare tunnel |
| Backup | VolSync clone-based restic to B2 |

Foundry has no native OIDC and never will, hence forward-auth plus blank internal passwords. Its
own snapshot export is UI-only, and this cluster has no VolumeSnapshotClass and no Velero CSI
plugin — so VolSync `copyMethod: Clone` is the only viable backup route.

### vollmint (Budgeting)

| Parameter | Value |
|---|---|
| Namespace | `vollmint` |
| Components | Go API + SPA, CNPG database, SimpleFIN sync CronJob |
| Container port | 8080 (API + SPA, via ingress-nginx) |
| Egress | 443 to SimpleFIN Bridge (sync CronJob) |
| Auth | Authentik SSO |
| NetworkPolicy | Default-deny with explicit allows |

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

### Minecraft Server

| Parameter | Value |
|---|---|
| Chart version | v4.0.0 |
| Helm repo | https://itzg.github.io/minecraft-server-charts/ |
| Image | `itzg/minecraft-server:java25` (26.x requires Java 25) |
| Server type | PAPER |
| Server version | `26.2` (Paper's current supported line; 1.21.x is EOL) |
| JVM memory | 6G |
| CPU | req: 2000m, limits: 4000m |
| Memory | req: 6Gi, limits: 8Gi |
| Data PVC | `pvc-minecraft-datadir`, 20Gi `longhorn-dmz`, RWX |
| View distance | 8 |
| Simulation distance | 6 |
| Max players | 20 |
| Difficulty | normal |
| Max world size | 29,999,984 |
| RCON | enabled (ExternalSecret: `minecraft-rcon-secret`) |
| Plugins | BlueMap v5.23 (**paper** build) — 5.23 supports 26.1–26.2 only; 5.13 topped out at 1.21.11, so plugin and server versions must move together |
| Service type | NodePort |
| Minecraft port | NodePort `32565` |
| BlueMap port | NodePort `32566` (container port 8100) |
| Public hostname | `minecraft.vollminlab.com` (CNAME → `dynamic.vollminlab.com` → WAN IP, unproxied) |
| Public port | **57913**, forwarded by the router to the HAProxy VIP `192.168.160.4:25565` |
| SRV record | `_minecraft._tcp.minecraft.vollminlab.com` → `0 5 57913 dynamic.vollminlab.com` |

**External access.** Players type the hostname with no port. A Java client resolves
`_minecraft._tcp.<host>` before connecting and uses the port that record names, falling back to
25565 only when no SRV exists. The record therefore lives on Cloudflare, the authoritative public
zone, and is read client-side: it routes no traffic. The port translation is the router's, on its
WAN interface.

Cloudflare proxying must stay **off** for both records. The orange-cloud proxy carries HTTP/HTTPS
only, not the Minecraft TCP protocol.

Bedrock clients ignore SRV entirely and would need `:57913` typed. Not applicable here, the server
is Paper (Java).

One SRV serves LAN and WAN alike **only because Pi-hole holds no local override for this
hostname** (verified 2026-08-23: `192.168.100.2` and `.3` both return the public address). LAN
clients hairpin through the router like anyone else. Add an override for this name and LAN play
breaks while external play keeps working, because the port translation lives on a WAN interface a
LAN client never crosses; that case needs a matching `srv-host=` in both Pi-holes on port 25565.

**Allowed ingress:** HAProxy nodes `192.168.160.2/32` and `192.168.160.3/32` on ports 25565 (game) and 8100 (BlueMap).

**BlueMap map configs live in the PVC, not in git.** `/data/plugins/BlueMap/maps/*.conf` are
generated by the plugin on first run and persist across rolls, so nothing in this repo can
correct them.

Paper 26.x moved dimensions to the vanilla unified layout, which invalidated two of them:

```
before (1.21.10)   /data/world_nether              /data/world_the_end
after  (26.2)      /data/world/dimensions/minecraft/the_nether , /the_end
```

The data migrated correctly, but `world_nether.conf` and `world_the_end.conf` still carried
`world: "world_nether"` / `world: "world_the_end"`, paths that no longer exist, so those two maps
stopped rendering while the overworld kept working. Fixed 2026-08-24 by setting `world: "world"`
in all three and leaving each `dimension:` key as-is, which is how BlueMap addresses a unified
layout. Originals kept beside them as `*.conf.pre26`.

**Any future world-layout change needs these re-checked by hand.** The symptom is a boxed
`There is a problem with your BlueMap setup!` in the server log naming a missing directory, and
it does not fail the pod or any probe.


**Allowed egress:** ports 80 and 443 to `0.0.0.0/0` **excluding RFC1918** (`10/8`, `172.16/12`,
`192.168/16`), which covers the LAN, the service CIDR and the pod CIDR. DNS is granted separately
by the namespace-wide `allow-dns` policy, not by this rule.

**Probes:**
- Startup: period=10s, failureThreshold=60 (600s budget)
- Readiness: initialDelay=30s, period=10s, failureThreshold=10
- Liveness: initialDelay=30s, period=5s, failureThreshold=10

While the startup probe is defined and not yet passing, the kubelet suspends **both** the
liveness and readiness probes, so a cold boot has the full 600s before anything can kill the
container. That matters because a kill during a world-format migration is how a save gets
corrupted. Measured cold start for the 1.21.10 → 26.2 upgrade was 40.9s including the 61 MB
server jar, the BlueMap jar and the migration itself — against the ~80s the liveness probe would
otherwise have allowed (`30 + 5 × 10`). Raise the threshold before any future major version hop
on a larger world rather than discovering the ceiling during one.

---

### masters-league (Fantasy golf dashboard)

| Parameter | Value |
|---|---|
| Namespace | `dmz` |
| Images | `harbor.vollminlab.com/vollminlab/masters-league:v1.1.1` (in-house), `redis:7.4.2-alpine` |
| NetworkPolicy | Default-deny; reaches `masters-redis` by `podSelector` |
| Node placement | Kyverno-injected `nodeSelector` + toleration — do not set manually |

## CI/CD

### GitHub Actions Workflows

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yaml` | PR + push to main | kustomize build validation, Kyverno policy checks, Trivy security scan |

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
