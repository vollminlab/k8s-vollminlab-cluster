# 1Password ESO Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all SealedSecrets with ESO-managed ExternalSecrets backed by an in-cluster 1Password Connect Server, then remove the sealed-secrets controller entirely.

**Architecture:** 1Password Connect Server runs in the `1password` namespace as a Flux HelmRelease with a 1Gi Longhorn PVC cache. ESO runs in `external-secrets`. A single `ClusterSecretStore` (deployed via the `clusterwide` kustomization) bridges them.

**Tech Stack:** External Secrets Operator (external-secrets/external-secrets v2.5.0), 1Password Connect (1password/connect v2.4.1), 1Password CLI (`op`), Flux CD, Helm, Longhorn

---

## Status — 2026-05-31

### ✅ COMPLETE — Phase 0: Infrastructure

- 1Password Connect deployed (`1password` namespace, HelmRelease, Longhorn PVC)
- ESO deployed (`external-secrets` namespace, HelmRelease)
- `ClusterSecretStore` (`onepassword-cluster-store`) deployed and Ready
- NetworkPolicies for both namespaces
- PrometheusRule for ESO sync failure alerting
- ESO access token bootstrapped as a manually-applied Secret in `1password` namespace

### ✅ COMPLETE — Phases 1–7: All non-authentik namespaces migrated

All SealedSecrets migrated to ExternalSecrets. PRs merged:

| PR | Namespaces |
|----|-----------|
| #818 | monitoring |
| #819 | external-dns, renovate, kube-system |
| #821 | mediastack (non-Volsync) |
| #824 | mediastack (Volsync restic secrets) |
| #825 | minio, harbor, shlink, velero |
| #826 | tofu (all 10 workspaces), homepage |
| #827 | ingress-nginx, tailscale, dmz, actions-runner-system, flux-system |

**Current cluster state:** 67 ExternalSecrets syncing across 16 namespaces. Zero SealedSecrets remaining outside `authentik`.

### ✅ COMPLETE — Phase 8: Migrate `authentik` namespace

5 ExternalSecrets. PRs #828 + #829 (creationPolicy fix for authentik-db-credentials).

### ✅ COMPLETE — Phase 9: Remove sealed-secrets controller

PR #830. sealed-secrets namespace pruned by Flux. All kustomization dependsOn references removed.
"Sealed Secrets Sealing Key" marked DEPRECATED in 1Password (retained for git history only).

---

## Phase 8: Migrate `authentik` namespace

**Vault items required:**

| Secret | Vault item | Interval | Notes |
|--------|-----------|----------|-------|
| `authentik-credentials` | "Authentik Credentials" | `1h` | admin credentials |
| `authentik-db-credentials` | "Authentik DB Password" | `"0"` + `Merge` | CNPG — create-once |
| `authentik-proxy-token` | "Authentik Proxy Token" | `24h` | outpost token |
| `cloudflared-authentik-tunnel-credentials` | "Cloudflare Authentik Tunnel" | `24h` | |
| `cnpg-minio-credentials` | "CNPG MinIO Credentials" | `1h` | shared vault item |

**`authentik-db-credentials` is CNPG-managed:** use `creationPolicy: Merge`, `refreshInterval: "0"`. The SealedSecret for this one already uses `creationPolicy: Owner` — confirm before migrating.

- [ ] **Step 1: Audit vault items**

```bash
export OP_SERVICE_ACCOUNT_TOKEN=$(cat ~/.config/op/service_account.token)
for item in "Authentik Credentials" "Authentik DB Password" "Authentik Proxy Token" \
            "Cloudflare Authentik Tunnel" "CNPG MinIO Credentials"; do
  op item get "$item" --vault Homelab --format json < /dev/null | python3 -c \
    "import json,sys; d=json.load(sys.stdin); print(d['title'], ':', [f['label'] for f in d['fields'] if f.get('value')])"
done
```

- [ ] **Step 2: Cross-check field lengths against live K8s secrets**

```bash
python3 << 'EOF'
import subprocess, json, base64
def k8s_keys(ns, secret):
    r = subprocess.run(['kubectl', 'get', 'secret', secret, '-n', ns, '-o', 'json'],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return {k: len(base64.b64decode(v)) for k,v in json.loads(r.stdout).get('data',{}).items()}
for ns, secret in [
    ('authentik', 'authentik-credentials'),
    ('authentik', 'authentik-db-credentials'),
    ('authentik', 'authentik-proxy-token'),
    ('authentik', 'cloudflared-authentik-tunnel-credentials'),
    ('authentik', 'cnpg-minio-credentials'),
]:
    print(f'{secret}:', k8s_keys(ns, secret))
EOF
```

- [ ] **Step 3: Create ExternalSecret files** (one per secret, in a new worktree/branch)

- [ ] **Step 4: Cross-check all field lengths match vault before committing**

- [ ] **Step 5: Update `authentik/authentik/app/kustomization.yaml`** — swap sealedsecret → externalsecret entries

- [ ] **Step 6: Update `authentik-kustomization.yaml`** — `dependsOn: sealed-secrets` → `external-secrets`

- [ ] **Step 7: Delete all 5 sealedsecret files**

- [ ] **Step 8: PR, merge, verify all 5 ExternalSecrets show `SecretSynced: True`**

- [ ] **Step 9: Verify Authentik pod, CNPG cluster, and outpost are all healthy post-migration**

---

## Phase 9: Remove sealed-secrets controller

**Gate:** `kubectl get sealedsecrets -A` must return empty before starting.

- [ ] **Step 1: Confirm zero SealedSecrets**

```bash
kubectl get sealedsecrets -A
# Expected: No resources found.
```

- [ ] **Step 2: Check for any remaining `dependsOn: sealed-secrets` in Flux Kustomization CRs**

```bash
grep -rl "sealed-secrets" clusters/vollminlab-cluster/flux-system/flux-kustomizations/
# Should only match sealed-secrets-kustomization.yaml itself
```

- [ ] **Step 3: PR — Remove sealed-secrets HelmRelease and namespace**

Remove `clusters/vollminlab-cluster/sealed-secrets/` directory entirely.
Remove `sealed-secrets-kustomization.yaml` from `flux-kustomizations/kustomization.yaml`.
Remove `sealed-secrets-helmrepository.yaml` from `repositories/kustomization.yaml`.

- [ ] **Step 4: Merge, verify Flux prunes the sealed-secrets namespace**

```bash
kubectl get ns sealed-secrets
# Expected: NotFound
```

- [ ] **Step 5: Mark sealing key deprecated in 1Password**

```bash
export OP_SERVICE_ACCOUNT_TOKEN=$(cat ~/.config/op/service_account.token)
op item edit "Sealed Secrets Sealing Key" --vault Homelab \
  --notes "DEPRECATED 2026-05-31 — cluster fully migrated to ESO. Retained only for decrypting historical git content. Do not use for new secrets." < /dev/null
```

- [ ] **Step 6: Final verification**

```bash
kubectl get externalsecrets -A --no-headers | grep -v "True" | wc -l
# Expected: 0

flux get kustomizations -A | grep -v "True"
# Expected: no output
```

---

## Rollback Reference

If an ExternalSecret fails to sync after migration, the SealedSecret YAML files were deleted from git but can be recovered from git history:

```bash
# Find the last commit that had the sealedsecret
git log --all --oneline -- clusters/vollminlab-cluster/<path>/<name>-sealedsecret.yaml

# Re-apply it
git show <commit>:clusters/vollminlab-cluster/<path>/<name>-sealedsecret.yaml | kubectl apply -f -

# Delete the ExternalSecret to release ownership
kubectl delete externalsecret <name> -n <namespace>
```
