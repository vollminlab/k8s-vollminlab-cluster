# DMZ Namespace

Secure namespace for hosting externally-accessible services on dedicated DMZ node(s).

## Overview

The DMZ namespace is designed for services that need to be accessible from the internet (e.g., game servers, public APIs) while maintaining strong security isolation from the rest of the cluster.

## Node Configuration

**DMZ Nodes**: `k8sworker05`, `k8sworker06`
- **Label**: `role=dmz`
- **Taint**: `dmz=true:NoSchedule`
- **IPs**: see `homelab-infrastructure/hosts/k8s/` for node addresses

## Security Model

### 1. Automatic Enforcement (Kyverno)

#### Node Placement (Auto-Applied)
- **Policy**: `dmz-enforce-node-placement` (ClusterPolicy)
- **Action**: Automatically adds `nodeSelector` and `tolerations` to all DMZ pods
- **Benefit**: Developers don't need to remember node placement configuration
- **Result**: All DMZ pods automatically run **only** on the DMZ nodes (`k8sworker05`, `k8sworker06`)

#### External Access Restriction (Validation)
- **Policy**: `dmz-restrict-external-access` (ClusterPolicy)
- **Action**: Blocks `external-access: "true"` and `internet-egress: "true"` labels outside DMZ namespace
- **Benefit**: Prevents accidental or malicious external exposure of internal workloads
- **Result**: Only DMZ namespace can host externally-accessible services

### 2. Default Deny (Network Policies)
All traffic is denied by default via `networkpolicy-default-deny.yaml`

### 3. Selective Allow Policies (Network Policies)

#### DNS Resolution
- **Policy**: `networkpolicy-allow-dns.yaml`
- **Allows**: UDP/TCP port 53 to kube-dns in kube-system namespace
- **Required for**: Service discovery, external domain resolution

#### External Ingress
- **Policy**: `networkpolicy-allow-external-ingress.yaml`
- **Allows**: All ingress traffic from any source (0.0.0.0/0)
- **Applies to**: Pods with label `external-access: "true"`
- **Use for**: NodePort services, LoadBalancer services

#### Internet Egress
- **Policy**: `networkpolicy-allow-internet-egress.yaml`
- **Allows**: Egress to internet (excluding private networks)
- **Applies to**: Pods with label `internet-egress: "true"`
- **Blocks**: RFC 1918 private networks, link-local, loopback
- **Use for**: Downloading updates, external API calls

## Deployed Services

| Service | Deployed as | Exposure | Network policy |
|---------|-------------|----------|----------------|
| `minecraft` | HelmRelease (`minecraft/app/helmrelease.yaml`) | NodePort 30565 (+ BlueMap service) | `external-access` / `internet-egress` labels |
| `masters-league` | Raw Deployments + Services (`masters-league/app/`) | NodePort 32567, `externalTrafficPolicy: Local` | Dedicated `masters-league` + `masters-redis` policies |

Both are listed in `kustomization.yaml`; Flux will silently ignore anything not in that list.

### masters-league

A two-replica FastAPI app serving Masters golf leaderboard data, backed by a single-replica Redis
cache. It is the DMZ's second workload and follows a different network pattern from Minecraft.

| Component | Detail |
|-----------|--------|
| Image | `harbor.vollminlab.com/vollminlab/masters-league:v1.1.1` (private Harbor project) |
| Pull secret | `harbor-vollminlab-pull`, materialized by ESO from the `Harbor Cluster Pull Robot Account` 1Password item |
| Replicas | 2 (app), 1 (`masters-redis`, `redis:7.4.2-alpine`) |
| Container port | 8000 (app), 6379 (Redis, ClusterIP only) |
| Exposure | `NodePort` 32567 with `externalTrafficPolicy: Local` — reached by the HAProxy DMZ VMs (`192.168.160.2`, `192.168.160.3`) |
| Egress | ESPN API over TCP 443, `masters-redis` on 6379, kube-dns on 53 |
| Labels | `app: masters-league`, `env: production`, `category: apps` |

**It does not use the `external-access` / `internet-egress` label pattern.** Instead it ships a
dedicated NetworkPolicy pair in `masters-league/app/networkpolicy.yaml` that narrows ingress to the
two HAProxy source IPs and pins egress to exactly what the app needs. Redis is locked down further:
ingress only from `app: masters-league`, egress only to kube-dns.

Use the label pattern when a service genuinely needs open internet ingress; write a dedicated policy
when the set of callers is known and small, as it is here.

## Deploying Services

### Example: Minecraft Server

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: minecraft-server
  namespace: dmz
  labels:
    app: minecraft
    env: production
    category: gaming
    external-access: "true"    # Allow external ingress
    internet-egress: "true"    # Allow internet egress
spec:
  # Note: nodeSelector and tolerations are automatically added by Kyverno!
  # You don't need to specify them - the dmz-enforce-node-placement policy
  # will automatically add:
  #   nodeSelector:
  #     role: dmz
  #   tolerations:
  #     - key: dmz
  #       operator: Exists
  #       effect: NoSchedule
  containers:
    - name: minecraft
      image: itzg/minecraft-server:2025.3.0  # pin to a specific tag — :latest is blocked by Kyverno
      ports:
        - containerPort: 25565
          protocol: TCP
      env:
        - name: EULA
          value: "TRUE"
---
apiVersion: v1
kind: Service
metadata:
  name: minecraft
  namespace: dmz
  labels:
    app: minecraft
    env: production
    category: gaming
spec:
  type: NodePort
  selector:
    app: minecraft
  ports:
    - port: 25565
      targetPort: 25565
      nodePort: 30565        # Accessible on any DMZ node, e.g. k8sworker05:30565
      protocol: TCP
```

## Storage

Use the `longhorn-dmz` StorageClass for DMZ-specific persistent storage:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: minecraft-data
  namespace: dmz
spec:
  storageClassName: longhorn-dmz
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
```

## Required Labels

All pods must have the following labels (enforced by Kyverno):
- `app`: Application name
- `env`: Environment (e.g., production, staging)
- `category`: Category (e.g., gaming, api)

## Network Policy Labels

| Label | Purpose | Required For |
|-------|---------|--------------|
| `external-access: "true"` | Allow ingress from internet | NodePort/LoadBalancer services |
| `internet-egress: "true"` | Allow egress to internet | Services needing external API/updates |

## Defense in Depth Layers

The DMZ namespace implements multiple security layers:

1. **Physical Isolation**: `k8sworker05` and `k8sworker06` are network-isolated in the DMZ
2. **Node Taint**: `dmz=true:NoSchedule` prevents accidental scheduling
3. **Kyverno Mutation**: Auto-adds nodeSelector + tolerations (enforces placement)
4. **Kyverno Validation**: Blocks external access labels outside DMZ (prevents misuse)
5. **Network Policies**: Control ingress/egress at pod level (default deny)
6. **Namespace Labels**: Pod Security Standards enforcement (baseline+)
7. **Storage Isolation**: `longhorn-dmz` StorageClass keeps data on the DMZ nodes

## Best Practices

1. **Minimal Privileges**: Only add `external-access` or `internet-egress` labels when absolutely necessary
2. **Resource Limits**: Always set CPU/memory limits
3. **Health Checks**: Implement liveness and readiness probes
4. **Security Context**: Run as non-root user when possible
5. **Image Scanning**: Use trusted, scanned images
6. **Secrets Management**: Use an `ExternalSecret` — ESO materializes the Secret from 1Password. Never commit a plain `kind: Secret` (SealedSecrets are retired; the controller was removed 2026-05-31)
7. **Labels Required**: All pods must have `app`, `env`, and `category` labels (enforced by Kyverno)

## Monitoring

- Monitor pod status: `kubectl get pods -n dmz`
- Check network policies: `kubectl get networkpolicies -n dmz`
- View events: `kubectl get events -n dmz --sort-by='.lastTimestamp'`

## Troubleshooting

### Pod not scheduled
- Check node taints: `kubectl describe node k8sworker05 k8sworker06`
- Verify tolerations in pod spec

### No network connectivity
- Verify pod has correct labels (`external-access`, `internet-egress`)
- Check network policies: `kubectl describe networkpolicy -n dmz`

### DNS not resolving
- DNS is allowed by default via `allow-dns` policy
- Check kube-dns is running: `kubectl get pods -n kube-system -l k8s-app=kube-dns`

