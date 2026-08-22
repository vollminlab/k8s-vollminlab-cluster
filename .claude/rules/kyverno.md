---
description: Kyverno policy rules, required labels, DMZ constraints, and enforcement modes for k8s-vollminlab-cluster
---

# Kyverno Rules

## Enforce-mode policies (will block pod creation)

| Policy                           | Rule                                                                          |
| -------------------------------- | ----------------------------------------------------------------------------- |
| `require-resources`              | Every container needs CPU + memory **requests** and a **memory limit**         |
| `restrict-latest-tag`            | Image tags must be pinned — `:latest` is blocked                              |
| `restrict-privileged`            | Privileged containers are blocked                                             |
| `restrict-hostpath-usage`        | `hostPath` volumes are blocked                                                |
| `restrict-default-namespace`     | Pods may not run in the `default` namespace                                   |
| `restrict-image-registries`      | Images must come from the approved registry list                              |
| `restrict-loadbalancer-services` | `type: LoadBalancer` only in `ingress-nginx` and `dmz`                        |
| `require-standard-labels`        | Every pod/controller/namespace must have `app`, `env`, `category` labels      |
| `dmz-restrict-external-access`   | `external-access` / `internet-egress` labels only inside `dmz`                |

**These five match `Deployment`, `StatefulSet` and `DaemonSet` — not `Pod`, and not `Job`
or `CronJob`.** Autogen does not close that gap: it derives *controller* rules from
*Pod*-matching policies, never the reverse. So a policy written against controller kinds
covers exactly the kinds it lists. Job and CronJob were unvalidated by anything until the
`-batch` policies below (#1164).

**A CPU limit is deliberately NOT required.** Throttling degrades latency-sensitive
workloads and cannot protect a node from exhaustion the way a memory limit does —
`coredns` and `kyverno-admission-controller` both omit theirs upstream. Set requests
plus a memory limit; add a CPU limit only when you have a specific reason.

## Audit-mode policies (violations logged, not blocked)

Every mutate policy runs in Audit mode — a mutation either applies or it doesn't,
there is nothing to block.

| Policy                            | What it does                                                    |
| --------------------------------- | --------------------------------------------------------------- |
| `dmz-enforce-node-placement`      | Injects `nodeSelector` + toleration on `dmz` pods                |
| `inject-namespace-labels`         | Copies `app`/`env`/`category` from namespace to workload         |
| `inject-pod-labels`               | Copies the same labels onto pods                                 |
| `inject-resource-requirements`    | Injects resources for Longhorn workloads the chart can't set     |
| `mutate-default-sa-automount`     | Disables token automount on the `default` ServiceAccount         |
| `mutate-default-sa-pod-automount` | Same, for pods using the `default` ServiceAccount                |

## The `-batch` policies — Job and CronJob coverage

`require-resources-batch`, `restrict-privileged-batch`, `restrict-latest-tag-batch`,
`restrict-hostpath-usage-batch` and `restrict-image-registries-batch` are one-for-one
counterparts of the Enforce five, matching `Job` and `CronJob`. They are **separate
policies, in Audit**, for two reasons:

1. **The container path differs per kind** — `spec.template.spec.containers` on a Job,
   `spec.jobTemplate.spec.template.spec.containers` on a CronJob. One rule matching both
   kinds resolves to null on whichever kind it wasn't written for, and a null `foreach.list`
   emits *no result at all* rather than a failure — the inert-policy mode of #1104/#1109.
   So each kind gets its own rule, and `pass` counts are the only proof they evaluate.
2. **Adding the kinds to the existing Enforce policies would have been a same-day
   Enforce.** These reach operator-created objects — velero's kopia maintenance Jobs,
   VolSync mover Jobs, trivy scan Jobs, Helm hook Jobs — that no manifest in this repo
   controls, on a fail-closed webhook. Audit first, then flip from a read report.

Measured 2026-08-22 with `kyverno apply` over every live object (18 CronJobs, 152 Jobs):
**pass 544, fail 133, error 0.** Every failure is `require-resources-batch`, and every one
comes from a workload not authored in this repo — 129 velero `*-kopia-maintain-job-*` Jobs,
plus the 2 CronJobs longhorn-manager generates from the `filesystem-trim` /
`snapshot-retention` `RecurringJob` CRs and the 2 Jobs those CronJobs had running. The
other four policies are clean cluster-wide.

**Before flipping any of these to Enforce**, read the live PolicyReport after at least one
full backup cycle, one VolSync cycle and one Helm reconcile — a snapshot of currently
*running* Jobs cannot see the transient ones, and VolSync's 13 ReplicationSources all have
`moverResources: null`, so their mover Jobs will fail `require-resources-batch` when they
next run.


## A policy with zero pass results is not enforcing

`foreach.list` is a JMESPath against the rule context, so it **must** be rooted at
`request.object` — a bare `spec.template.spec.containers` resolves to null, the loop
iterates nothing, and the rule emits **no result at all**. Policy Reporter then shows
0 fail because it also shows 0 pass, and admission lets everything through. Three
policies here were silently inert this way until 2026-08-19 (#1104, #1109).

```bash
# Any deployed policy absent from this list is evaluating nothing.
kubectl get polr -A -o json | python3 -c "
import json,sys,collections
c=collections.Counter()
for r in json.load(sys.stdin)['items']:
    for res in r.get('results',[]): c[(res['policy'],res['result'])]+=1
[print(k,v) for k,v in sorted(c.items())]"
```

Before enabling a dormant **mutate**, diff every value it will write against measured
usage — its numbers have never been tested. Fixing one here would have OOMKilled
`longhorn-manager` (512Mi limit vs a 563Mi p95), and re-nesting `trivy-operator`'s
misplaced block would have capped it at 768Mi against a 1117Mi peak.

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
