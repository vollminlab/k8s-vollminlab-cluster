---
description: Kyverno policy rules, required labels, DMZ constraints, and enforcement modes for k8s-vollminlab-cluster
---

# Kyverno Rules

## Enforce-mode policies (will block pod creation)

| Policy               | Rule                                                                      |
| -------------------- | ------------------------------------------------------------------------- |
| Resource limits      | Every pod must have CPU + memory requests and limits                      |
| No `:latest` tags    | Image tags must be pinned — `:latest` is blocked                          |
| No privileged        | Privileged containers are blocked                                         |
| No `hostPath`        | `hostPath` volumes are blocked                                            |
| No default namespace | Pods may not run in the `default` namespace                               |
| DMZ placement        | DMZ pods must run on `k8sworker05`/`k8sworker06` (injected automatically) |
| Required labels      | Every pod/controller/namespace must have `app`, `env`, `category` labels  |

## Audit-mode policies (violations logged, not blocked)

- None currently — all policies are in Enforce mode

## Valid `category` label values

Every HelmRelease and pod must use one of:

| Category        | Apps                                                           |
| --------------- | -------------------------------------------------------------- |
| `core`          | Flux, Headlamp, Kyverno                                        |
| `security`      | cert-manager, external-secrets, 1Password Connect, Kyverno policy-reporter |
| `storage`       | Longhorn, local-path-provisioner, smb-csi-driver               |
| `networking`    | ingress-nginx, MetalLB, external-dns                           |
| `observability` | metrics-server, kube-prometheus-stack, Grafana, Loki           |
| `apps`          | homepage, portainer, shlink, renovate                          |
| `media`         | Radarr, Sonarr, Bazarr, Overseerr, Prowlarr, SABnzbd, Tautulli |
| `gaming`        | Minecraft (dmz namespace only)                                 |
| `ci`            | actions-runner-system (GitHub ARC runners)                     |

## DMZ namespace rules

- Workloads live in `dmz/` namespace only
- Dedicated nodes: `k8sworker05`, `k8sworker06`, taint `dmz=true:NoSchedule`
- Kyverno auto-injects `nodeSelector` and `tolerations` — do not set manually
- Default-deny NetworkPolicy; all ingress/egress requires explicit allow rules
- Use `longhorn-dmz` StorageClass for persistent volumes (node-isolated)
- Full details: `clusters/vollminlab-cluster/dmz/README.md`

## Autogen rules — danger zone

Kyverno autogen automatically generates additional rules to cover pod controllers when a policy targets bare `Pod` objects. This can produce broken rules that block the entire cluster.

**Hard rules:**

1. **Never mix `Pod` and controller kinds (`Deployment`, `StatefulSet`, `DaemonSet`) in the same policy rule.** Pod rules and controller rules must be in separate ClusterPolicies. Mixing them causes autogen to generate a controller variant of the Pod rule with incorrect field paths.

2. **Any policy that uses an `apiCall` context with a namespace lookup must disable autogen.** Add this annotation:

   ```yaml
   annotations:
     pod-policies.kyverno.io/autogen-controllers: none
   ```

   Without this, autogen rewrites `request.object.metadata.namespace` to `request.object.spec.template.metadata.namespace` — a field that does not exist on Deployment objects. The fail-closed webhook then blocks all Deployment mutations cluster-wide.

3. **After applying any mutate policy, verify no autogen rules were generated:**

   ```bash
   kubectl get clusterpolicy <name> -o jsonpath='{.spec.rules[*].name}'
   # Should return only the hand-written rule name(s), no autogen-* variants
   ```

**Why this matters:** The `mutate.kyverno.svc-fail` webhook is fail-closed (`failurePolicy: Fail`). A single broken policy blocks every mutation in its match scope. On 2026-04-05, a broken autogen rule blocked all cluster mutations for ~2 hours.

## Emergency: webhook blocking all mutations

See `docs/runbooks/kyverno-recovery.md`. Short version: delete the broken ClusterPolicy, restart `kyverno-admission-controller`, verify unblocked.

## Checking violations

```bash
kubectl get policyreport -A
kubectl describe policyreport -n [namespace]
```

## CI enforcement

The same Kyverno policies run in CI (`kyverno-cli test`) before any PR can merge. A manifest that passes CI should pass in-cluster.
