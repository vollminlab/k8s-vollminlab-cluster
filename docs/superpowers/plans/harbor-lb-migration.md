# Harbor LoadBalancer Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Harbor from ClusterIP + nginx Ingress to a dedicated MetalLB LoadBalancer VIP (`192.168.152.245`) so CI runners can be given Harbor-specific NetworkPolicy access.

**Architecture:** Harbor currently terminates TLS at nginx ingress (VIP `192.168.152.244`). All cluster services share this VIP, making Harbor-specific NetworkPolicy rules impossible at L3/L4. The fix gives Harbor its own VIP (`192.168.152.245`) via MetalLB, removes the nginx Ingress, and moves TLS termination into Harbor itself using a cert-manager Certificate. The ARC runners NetworkPolicy gains a new ipBlock rule limited to that single VIP on port 443.

**Tech Stack:** Helm/goharbor chart v1.19.0, cert-manager v1 Certificate CRs, MetalLB v0.13+ LoadBalancer annotation, Flux CD GitOps, Kubernetes NetworkPolicy.

---

## File Map

| File | Action | What changes |
|------|--------|--------------|
| `clusters/vollminlab-cluster/harbor/harbor/app/harbor-tls-certificate.yaml` | **Create** | cert-manager Certificate for `harbor.vollminlab.com` using `letsencrypt-cloudflare` ClusterIssuer |
| `clusters/vollminlab-cluster/harbor/harbor/app/configmap.yaml` | **Modify** | `expose.type` → `loadBalancer`; `expose.tls.enabled` → `true`; add `expose.tls.certSource: secret` and `expose.tls.secret.secretName: harbor-tls`; add `expose.loadBalancer.annotations` with MetalLB IP annotation |
| `clusters/vollminlab-cluster/harbor/harbor/app/kustomization.yaml` | **Modify** | Remove `- ingress.yaml`; add `- harbor-tls-certificate.yaml` |
| `clusters/vollminlab-cluster/harbor/harbor/app/ingress.yaml` | **Delete** | No longer needed — TLS now terminates at Harbor's own LoadBalancer service |
| `clusters/vollminlab-cluster/actions-runner-system/arc-runners/app/networkpolicy.yaml` | **Modify** | Add `ipBlock: 192.168.152.245/32` egress rule on port 443 for Harbor registry access |

---

## Task 1: Create cert-manager Certificate for harbor-tls

**Files:**
- Create: `clusters/vollminlab-cluster/harbor/harbor/app/harbor-tls-certificate.yaml`

This Certificate asks cert-manager to issue a Let's Encrypt cert for `harbor.vollminlab.com` via the existing `letsencrypt-cloudflare` DNS-01 ClusterIssuer. The resulting TLS secret (`harbor-tls`) will be used by Harbor's LoadBalancer service.

- [ ] **Step 1: Create the Certificate manifest**

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: harbor-tls
  namespace: harbor
spec:
  secretName: harbor-tls
  commonName: harbor.vollminlab.com
  dnsNames:
    - harbor.vollminlab.com
  issuerRef:
    name: letsencrypt-cloudflare
    kind: ClusterIssuer
    group: cert-manager.io
```

Write this to `clusters/vollminlab-cluster/harbor/harbor/app/harbor-tls-certificate.yaml`.

- [ ] **Step 2: Verify YAML is valid**

```bash
yamllint clusters/vollminlab-cluster/harbor/harbor/app/harbor-tls-certificate.yaml
```

Expected: no errors (yamllint may warn on missing newline at end — that's a warning, not an error).

---

## Task 2: Update configmap.yaml — expose.type → loadBalancer

**Files:**
- Modify: `clusters/vollminlab-cluster/harbor/harbor/app/configmap.yaml`

Replace the `expose` block. The goharbor chart expects `expose.loadBalancer.annotations` for MetalLB IP assignment (MetalLB v0.13+ prefers the annotation over deprecated `spec.loadBalancerIP`). Harbor performs TLS termination itself when `expose.tls.certSource: secret` — it mounts the `harbor-tls` secret directly.

- [ ] **Step 1: Replace the expose block in configmap.yaml**

The current block in `data.values.yaml`:

```yaml
    expose:
      type: clusterIP
      tls:
        enabled: false
```

Replace with:

```yaml
    expose:
      type: loadBalancer
      tls:
        enabled: true
        certSource: secret
        secret:
          secretName: harbor-tls
      loadBalancer:
        IP: ""
        ports:
          httpPort: 80
          httpsPort: 443
        annotations:
          metallb.universe.tf/loadBalancerIPs: "192.168.152.245"
        sourceRanges: []
```

- [ ] **Step 2: Verify YAML is valid**

```bash
yamllint clusters/vollminlab-cluster/harbor/harbor/app/configmap.yaml
```

Expected: no errors.

- [ ] **Step 3: Verify the full values block renders correctly**

```bash
grep -A 20 "expose:" clusters/vollminlab-cluster/harbor/harbor/app/configmap.yaml
```

Expected output (indented under `values.yaml: |`):
```
    expose:
      type: loadBalancer
      tls:
        enabled: true
        certSource: secret
        secret:
          secretName: harbor-tls
      loadBalancer:
        IP: ""
        ports:
          httpPort: 80
          httpsPort: 443
        annotations:
          metallb.universe.tf/loadBalancerIPs: "192.168.152.245"
        sourceRanges: []
```

---

## Task 3: Update kustomization.yaml

**Files:**
- Modify: `clusters/vollminlab-cluster/harbor/harbor/app/kustomization.yaml`

Remove the `ingress.yaml` reference (we're deleting that file) and add the new Certificate file.

- [ ] **Step 1: Update the resources list**

Current content:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - helmrelease.yaml
  - configmap.yaml
  - ingress.yaml
  - harbor-admin-credentials-sealedsecret.yaml
  - harbor-core-credentials-sealedsecret.yaml
```

Replace with:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - helmrelease.yaml
  - configmap.yaml
  - harbor-tls-certificate.yaml
  - harbor-admin-credentials-sealedsecret.yaml
  - harbor-core-credentials-sealedsecret.yaml
```

- [ ] **Step 2: Verify**

```bash
cat clusters/vollminlab-cluster/harbor/harbor/app/kustomization.yaml
```

Expected: `harbor-tls-certificate.yaml` present, `ingress.yaml` absent.

---

## Task 4: Delete ingress.yaml

**Files:**
- Delete: `clusters/vollminlab-cluster/harbor/harbor/app/ingress.yaml`

Harbor will no longer route through nginx ingress. Flux `prune: true` on the harbor Kustomization will delete the Ingress resource from the cluster when this file disappears.

> **Note on Shlink:** The deleted Ingress carried `shlink.vollminlab.com/slug: harbor`. If the shlink-ingress-controller handles delete events, `vollm.in/harbor` may be removed. Check and recreate it manually after the migration if needed (see post-merge steps).

- [ ] **Step 1: Delete the file**

```bash
git rm clusters/vollminlab-cluster/harbor/harbor/app/ingress.yaml
```

Expected: `deleted: clusters/vollminlab-cluster/harbor/harbor/app/ingress.yaml`

---

## Task 5: Update ARC runners NetworkPolicy

**Files:**
- Modify: `clusters/vollminlab-cluster/actions-runner-system/arc-runners/app/networkpolicy.yaml`

The current public-internet egress rule excludes all RFC 1918 ranges (`192.168.0.0/16` covers the MetalLB pool). Harbor's new VIP `192.168.152.245` is in that excluded range, so a specific rule is needed. Add it after the Kubernetes API rule and before the public internet rule.

- [ ] **Step 1: Add the Harbor-specific egress rule**

Current egress section ends with:

```yaml
    # Public internet — GitHub, OCI registries, apt mirrors, etc.
    # All RFC 1918 ranges blocked to prevent accessing internal cluster services.
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - port: 443
          protocol: TCP
        - port: 80
          protocol: TCP
```

Insert a new rule block **before** the public-internet rule:

```yaml
    # Harbor registry — dedicated LoadBalancer VIP; excluded from internet rule (RFC 1918).
    - to:
        - ipBlock:
            cidr: 192.168.152.245/32
      ports:
        - port: 443
          protocol: TCP
```

The file should now look like this in full:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: arc-runners-egress
  namespace: actions-runner-system
  labels:
    app: arc-runners
    env: production
    category: ci
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    # DNS — target CoreDNS pods directly so the rule matches post-DNAT destinations.
    # ipBlock on the kube-dns service VIP (10.96.0.10) does not work: Calico evaluates
    # egress rules after kube-proxy rewrites the destination to the actual CoreDNS pod IP.
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
    # Kubernetes API server — use actual control plane node IPs on 6443.
    # In-cluster kubectl targets the kubernetes service VIP (10.96.0.1:443) which
    # kube-proxy DNATs to these node IPs. ipBlock on the real endpoints works.
    - to:
        - ipBlock:
            cidr: 192.168.152.8/32
        - ipBlock:
            cidr: 192.168.152.9/32
        - ipBlock:
            cidr: 192.168.152.10/32
      ports:
        - port: 6443
          protocol: TCP
    # Harbor registry — dedicated LoadBalancer VIP; excluded from internet rule (RFC 1918).
    - to:
        - ipBlock:
            cidr: 192.168.152.245/32
      ports:
        - port: 443
          protocol: TCP
    # Public internet — GitHub, OCI registries, apt mirrors, etc.
    # All RFC 1918 ranges blocked to prevent accessing internal cluster services.
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - port: 443
          protocol: TCP
        - port: 80
          protocol: TCP
```

- [ ] **Step 2: Verify YAML is valid**

```bash
yamllint clusters/vollminlab-cluster/actions-runner-system/arc-runners/app/networkpolicy.yaml
```

Expected: no errors.

---

## Task 6: Commit and open PR

- [ ] **Step 1: Stage the changed files (never `git add -A`)**

```bash
git add clusters/vollminlab-cluster/harbor/harbor/app/harbor-tls-certificate.yaml
git add clusters/vollminlab-cluster/harbor/harbor/app/configmap.yaml
git add clusters/vollminlab-cluster/harbor/harbor/app/kustomization.yaml
git add clusters/vollminlab-cluster/harbor/harbor/app/ingress.yaml
git add clusters/vollminlab-cluster/actions-runner-system/arc-runners/app/networkpolicy.yaml
```

- [ ] **Step 2: Confirm staged diff looks correct**

```bash
git diff --cached --stat
```

Expected:
```
 clusters/vollminlab-cluster/actions-runner-system/arc-runners/app/networkpolicy.yaml | 5 +++++
 clusters/vollminlab-cluster/harbor/harbor/app/configmap.yaml                         | 14 +++++++++-----
 clusters/vollminlab-cluster/harbor/harbor/app/harbor-tls-certificate.yaml            | 15 +++++++++++++++
 clusters/vollminlab-cluster/harbor/harbor/app/ingress.yaml                           | 33 ---------------------------------
 clusters/vollminlab-cluster/harbor/harbor/app/kustomization.yaml                     |  2 +-
 5 files changed, 36 insertions(+), 69 deletions(-)
```

(Exact numbers will differ; the key is 5 files, ingress.yaml shows only deletions.)

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(harbor): migrate to dedicated LoadBalancer VIP 192.168.152.245

Moves Harbor off the shared nginx ingress VIP so CI runners can be given
a Harbor-specific NetworkPolicy rule. TLS now terminates at Harbor via a
cert-manager Certificate (harbor-tls) rather than nginx wildcard-tls.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin HEAD
gh pr create \
  --title "feat(harbor): migrate to dedicated LoadBalancer VIP for NetworkPolicy isolation" \
  --body "$(cat <<'EOF'
## Summary

- Replaces Harbor's nginx Ingress + ClusterIP with a dedicated MetalLB LoadBalancer VIP (`192.168.152.245`)
- Adds cert-manager Certificate for `harbor.vollminlab.com` (letsencrypt-cloudflare DNS-01)
- Removes `ingress.yaml`; TLS now terminates at Harbor using the `harbor-tls` secret
- Adds Harbor-specific ipBlock egress rule to ARC runners NetworkPolicy so CI builds can push/pull images

Resolves the root cause of the b2-exporter ImagePullBackOff and unblocks shlink-ingress-controller and masters-league image builds.

## Post-merge manual steps

1. Wait ~10 min for Flux to reconcile
2. `kubectl get svc -n harbor harbor -o jsonpath='{.status.loadBalancer.ingress[0].ip}'` → confirm `192.168.152.245`
3. Update Pi-hole DNS: `harbor.vollminlab.com` A → `192.168.152.245` (**time-sensitive** — harbor.vollminlab.com breaks until this is done)
4. `curl -I https://harbor.vollminlab.com/v2/` → expect 200 or 401 (not connection error)
5. Re-trigger b2-exporter build: `gh workflow run build-b2-exporter.yaml -R vollminlab/k8s-vollminlab-cluster`
6. Verify `kubectl get pod -n monitoring -l app=b2-exporter` exits ImagePullBackOff

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed. Also merge PR #586 (roadmap doc) after this one is approved.

---

## Post-Merge Runbook

These steps happen **after the PR merges and Flux reconciles** (~10 min). They are manual because they touch external systems (Pi-hole DNS, GitHub Actions).

### 1. Confirm LoadBalancer IP assignment

```bash
kubectl get svc -n harbor harbor -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```

Expected: `192.168.152.245`

If blank, check MetalLB allocated the IP:
```bash
kubectl describe svc -n harbor harbor | grep -A5 "Events:"
kubectl get ipaddresspool -n metallb-system
```

### 2. Confirm cert-manager issued the certificate

```bash
kubectl get certificate -n harbor harbor-tls
kubectl describe certificate -n harbor harbor-tls | tail -20
```

Expected: `READY: True`. If still `False` after 5 minutes, check:
```bash
kubectl get certificaterequest -n harbor
kubectl describe certificaterequest -n harbor <name>
```

### 3. Update Pi-hole DNS (time-sensitive)

Harbor will return ECONNREFUSED from the old nginx VIP until this is done. In the Pi-hole admin UI:
- Navigate to Local DNS → DNS Records
- Find `harbor.vollminlab.com` → change the IP from `192.168.152.244` to `192.168.152.245`

### 4. End-to-end verify

```bash
curl -I https://harbor.vollminlab.com/v2/
```

Expected: `HTTP/2 200` or `HTTP/2 401` (Harbor's registry API returns 401 for unauthenticated requests — that means TLS and routing are working).

### 5. Re-trigger b2-exporter build

```bash
gh workflow run build-b2-exporter.yaml -R vollminlab/k8s-vollminlab-cluster
```

Watch the workflow: the build will push `harbor.vollminlab.com/vollminlab/b2-exporter:1.0.0` once the runner can reach the new Harbor VIP.

### 6. Verify b2-exporter pod recovers

```bash
kubectl get pod -n monitoring -l app=b2-exporter -w
```

Expected: pod transitions from `ImagePullBackOff` → `Running` after a successful image pull.

### 7. Check Shlink short URL (optional)

```bash
curl -I https://vollm.in/harbor
```

Expected: redirect to `https://harbor.vollminlab.com`. If the shlink controller removed the slug on ingress deletion, recreate it via the Shlink API:

```bash
SHLINK_API_KEY=$(kubectl get secret -n shlink shlink-credentials -o jsonpath='{.data.SHLINK_API_KEY}' | base64 -d)
curl -X POST https://shlink.vollminlab.com/rest/v3/short-urls \
  -H "X-Api-Key: $SHLINK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"longUrl":"https://harbor.vollminlab.com","customSlug":"harbor"}'
```

### 8. Merge roadmap PR #586

The roadmap doc PR was waiting on this implementation. Merge it once Harbor is confirmed healthy.
