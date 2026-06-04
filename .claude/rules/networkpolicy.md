---
description: Mandatory port verification and checklist for writing or modifying NetworkPolicies
---

# NetworkPolicy Rules

## The port trap — container port ≠ service port

**NetworkPolicy is evaluated post-DNAT at the pod interface.** kube-proxy rewrites the destination
port before the packet reaches the pod — so the port in a NetworkPolicy rule must be the
**container port**, not the service port.

Example: `cnpg-webhook-service` exposes port `443`, but the container (`webhook-server`) listens
on `9443`. The NetworkPolicy must use `9443`. Using `443` silently allows nothing.

**This class of bug has hit this cluster twice** — source-controller (PRs #548-549) and
CNPG webhook (PR #875/876). It leaves no error on the NetworkPolicy itself; traffic is simply
blocked with `context deadline exceeded` on the caller side.

## Mandatory pre-write verification

Before adding or changing any port in a NetworkPolicy, run both commands:

```bash
# 1. Find the service → targetPort mapping
kubectl get svc -n <namespace> <service-name> -o yaml | grep -A5 "ports:"

# 2. Confirm the actual containerPort on the pod
kubectl get pod -n <namespace> -l <selector> \
  -o jsonpath='{.items[0].spec.containers[*].ports}' | python3 -m json.tool
```

Use the `containerPort` value in the NetworkPolicy. If `targetPort` is a named port, look it up
in the pod spec — named ports resolve to container ports.

## Namespace container port reference

Keep this table current whenever a new NetworkPolicy namespace is added.

| Namespace | Container | Container port | Purpose | NetworkPolicy rule |
|-----------|-----------|---------------|---------|-------------------|
| `cnpg-system` | `manager` (cnpg-cloudnative-pg) | 9443 | webhook-server | allow-webhook-ingress ingress |
| `cnpg-system` | `manager` (cnpg-cloudnative-pg) | 8080 | metrics | allow-monitoring-scrape ingress |
| `cnpg-system` | n/a (egress target) | 8000 | CNPG instance status API (all namespaces) | allow-instance-status-egress egress |
| `cnpg-system` | n/a (egress target) | 5432 | PostgreSQL (all namespaces) | *(not needed — operator doesn't connect directly)* |
| `flux-system` | `source-controller` | 9090 | artifact serving | allow-source-controller-ingress ingress |

Add a row here when writing a new NetworkPolicy with a port restriction. This is the source of
truth — do not rely on service port numbers.

## Checklist — before opening any NetworkPolicy PR

- [ ] For every `port:` field, verify containerPort with `kubectl get pod ... ports` — not guessed, not from service YAML alone
- [ ] For ingress rules: the port is reachable from the allowed source (test with `kubectl exec ... nc -zv <pod-ip> <port>` if uncertain)
- [ ] For egress rules: the destination pod's containerPort is confirmed, not the service port
- [ ] Default-deny policy exists in the namespace before allow rules are added (never add only allow rules without a deny)
- [ ] DNS egress (UDP/TCP 53 to kube-dns) included if pods do any DNS lookups
- [ ] Kube-apiserver egress (TCP 6443) included if the workload is a controller/operator watching CRDs
- [ ] Monitoring ingress (from `monitoring` namespace) included for any pods with metrics endpoints

## When adding a new namespace with default-deny

Use the established template order:
1. `default-deny-all` (podSelector: {}, both Ingress + Egress)
2. `allow-dns` (UDP+TCP 53 to kube-dns)
3. Ingress allows (webhook, monitoring scrape, etc.)
4. Egress allows (kube-api, HTTPS, any cross-namespace ports)

Always verify every port in the new namespace before committing — not after merge.
