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

## ipBlock is unsafe for cross-node host→pod traffic in Calico IPIP mode

**Never use `ipBlock` to restrict which CP nodes can call a webhook.**

On this cluster (Calico IPIP, Ubuntu 24.04), the kernel evaluates `rp_filter` on `tunl0` **before** nftables/Calico policy chains run. With `rp_filter=2` (strict mode), the kernel drops IPIP-decapsulated packets whose source IP's expected return route is the physical NIC rather than `tunl0`. The packet is silently discarded before Calico ever sees it — the nftables ipBlock rule sits at 0 counter matches, traffic appears blocked at the NetworkPolicy, but the real cause is the kernel RPF.

**Symptoms:** `context deadline exceeded` on the caller; nftables counters for the ipBlock rule show 0 packets; `namespaceSelector`-based rules to the same pod work fine.

**The fix:** For any ingress policy where the source is a CP/host IP (webhook callbacks, kubelet, etc.), use `from: []` (open to all sources) restricted only by `ports:`. The admission webhook itself provides TLS authentication; the port restriction alone is sufficient.

```yaml
# CORRECT — open source, port-restricted
ingress:
  - ports:
      - protocol: TCP
        port: 9443

# BROKEN in Calico IPIP — ipBlock rules for CP host IPs are silently dropped
ingress:
  - from:
      - ipBlock:
          cidr: 192.168.152.8/32
    ports:
      - protocol: TCP
        port: 9443
```

**This bug hit this cluster twice** — CNPG webhook (PRs #875/876/878) and prometheus-operator webhook (PR #878, same root cause). Both were fixed by removing ipBlock.

## Namespace container port reference

Keep this table current whenever a new NetworkPolicy namespace is added.

| Namespace | Container | Container port | Purpose | NetworkPolicy rule |
|-----------|-----------|---------------|---------|-------------------|
| `cnpg-system` | `manager` (cnpg-cloudnative-pg) | 9443 | webhook-server | allow-webhook-ingress ingress (open source) |
| `cnpg-system` | `manager` (cnpg-cloudnative-pg) | 8080 | metrics | allow-monitoring-scrape ingress |
| `cnpg-system` | n/a (egress target) | 8000 | CNPG instance status API (all namespaces) | allow-instance-status-egress egress |
| `cnpg-system` | n/a (egress target) | 5432 | PostgreSQL (all namespaces) | *(not needed — operator doesn't connect directly)* |
| `flux-system` | `source-controller` | 9090 | artifact serving | allow-source-controller-ingress ingress |
| `monitoring` | `kube-prometheus-stack-operator` | 10250 | prometheus-operator admission webhook | allow-webhook-ingress ingress (open source) |
| `authentik` | n/a (ingress target) | 8000 | CNPG instance status API | allow-cnpg-operator ingress |
| `authentik` | n/a (egress target) | 7844 | Cloudflare tunnel edge (QUIC UDP + http2 TCP) | allow-external-egress egress |
| `harbor` | n/a (ingress target) | 8000 | CNPG instance status API | allow-cnpg-operator ingress |

Add a row here when writing a new NetworkPolicy with a port restriction. This is the source of
truth — do not rely on service port numbers.

## Checklist — before opening any NetworkPolicy PR

- [ ] For every `port:` field, verify containerPort with `kubectl get pod ... ports` — not guessed, not from service YAML alone
- [ ] For ingress rules: the port is reachable from the allowed source (test with `kubectl exec ... nc -zv <pod-ip> <port>` if uncertain)
- [ ] For egress rules: the destination pod's containerPort is confirmed, not the service port
- [ ] If source is a CP/host IP (webhook, kubelet): use `from: []` not `ipBlock` — see ipBlock section above
- [ ] Default-deny policy exists in the namespace before allow rules are added (never add only allow rules without a deny)
- [ ] DNS egress (UDP/TCP 53 to kube-dns) included if pods do any DNS lookups
- [ ] Kube-apiserver egress (TCP 6443) included if the workload is a controller/operator watching CRDs
- [ ] Monitoring ingress (from `monitoring` namespace) included for any pods with metrics endpoints
- [ ] Any namespace with CNPG databases: `allow-cnpg-operator` must include both port 5432 AND port 8000

## When adding a new namespace with default-deny

Use the established template order:
1. `default-deny-all` (podSelector: {}, both Ingress + Egress)
2. `allow-dns` (UDP+TCP 53 to kube-dns)
3. Ingress allows (webhook, monitoring scrape, etc.)
4. Egress allows (kube-api, HTTPS, any cross-namespace ports)

Always verify every port in the new namespace before committing — not after merge.
