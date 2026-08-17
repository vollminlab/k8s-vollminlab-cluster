---
description: Plan and scaffold a new service deployment to the vollminlab cluster
user-invocable: true
---

# Plan New Rollout

You are helping plan a new service deployment to the vollminlab Kubernetes cluster. Read `CLAUDE.md`, `.claude/rules/flux.md`, `.claude/rules/kyverno.md`, `.claude/rules/secrets.md`, and `docs/cluster-reference.md` before proceeding.

Ask the user for any details not already provided, then produce a complete deployment plan.

## Information to gather

If not already stated, ask:

1. **Service name** — what is it, what does it do?
2. **Helm chart** — which chart and which Helm repository (OCI or HTTP)?
3. **Namespace** — existing namespace or new one? (check `clusters/vollminlab-cluster/` for existing namespaces)
4. **Ingress** — does it need a public subdomain? (e.g. `service.vollminlab.com`) Internal only or DMZ?
5. **Persistence** — does it need a PVC? What size? Longhorn (default) or SMB?
6. **Secrets** — does it need API keys, passwords, or credentials? (these become ExternalSecrets, sourced from 1Password via ESO)
7. **External integrations** — does it talk to other cluster services (e.g. ingress-nginx, Radarr, Prowlarr)?
8. **DNS** — does it need a Pi-hole DNS record? (A record → `192.168.152.244` for cluster apps, or `192.168.152.2` for NPM-proxied infra)

## Output: deployment plan

Produce a structured plan covering every file that needs to be created or modified:

### 1. Namespace (if new)
- `clusters/vollminlab-cluster/<namespace>/namespace.yaml`
- `clusters/vollminlab-cluster/<namespace>/kustomization.yaml`
- Flux Kustomization CR in `flux-system/flux-kustomizations/<namespace>.yaml`

### 2. App files
For each app under `clusters/vollminlab-cluster/<namespace>/<app>/app/`:

| File | Notes |
|------|-------|
| `helmrelease.yaml` | Chart version must be pinned. sourceRef to HelmRepository or OCIRepository. |
| `configmap.yaml` | Helm values. Include `podLabels` with `app`, `env: production`, `category`. |
| `kustomization.yaml` | Lists all resources in this dir. |
| `ingress.yaml` | If needed. TLS uses `secretName: wildcard-tls`, backend points to correct service name. |
| `pvc-*.yaml` | If persistence needed. StorageClass: `longhorn`. |
| `*-externalsecret.yaml` | If secrets needed. Never plain Secret, never SealedSecret. Filename base must equal `metadata.name`. |

### 3. HelmRepository or OCIRepository
- If this chart's repo isn't already in `flux-system/repositories/`, add it.
- Named after the **app**, not the chart author.

### 4. DNS records
- State exactly which Pi-hole API calls are needed (endpoint, domain, IP).
- Remember: call both `192.168.100.2:5001` and `192.168.100.3:5001`.

### 5. Kyverno requirements
- Confirm `category` label value (valid values: `core`, `security`, `storage`, `networking`, `observability`, `apps`, `gaming`)
- If DMZ: note that nodeSelector/tolerations are auto-injected, NetworkPolicy must be explicit

### 6. Secrets workflow
For each secret:
1. **Save the credential to 1Password first** (Homelab vault) — it is both the recovery copy and
   the live source ESO reads from. If it isn't there, the ExternalSecret has nothing to sync.
2. Write an `ExternalSecret` referencing the vault item by name:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: <app>-<purpose>          # never suffix with "-secret"
  namespace: <namespace>
  labels: { app: <app>, env: production, category: <category> }
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: onepassword-cluster-store
    kind: ClusterSecretStore
  target:
    name: <app>-<purpose>
    creationPolicy: Owner
  data:
    - secretKey: password
      remoteRef:
        key: "<1Password item title>"
        property: password
```

3. Add the file to the directory's `kustomization.yaml` (explicit lists, not globs).
4. Verify after reconcile: `kubectl get externalsecret <name> -n <ns>` → READY=True.

### 7. Post-deploy steps
List any manual steps after Flux reconciles:
- API key configuration in the UI
- Connecting to other services (e.g. adding to Prowlarr apps)
- Adding Shlink short link (`vl/<slug>`)
- Smoke test curl or browser check

### 8. Branch and PR
```bash
git checkout -b feat/<service-name>
# ... create files ...
git push -u origin feat/<service-name>
gh pr create --title "feat: add <service-name>" ...
```

## Checklist before declaring plan complete

- [ ] Chart version is pinned (no `*` or floating ranges)
- [ ] All pods have `app`, `env`, `category` labels
- [ ] No plain `kind: Secret` and no `SealedSecret` — only `ExternalSecret`
- [ ] HelmRepository named after the app, not the chart author
- [ ] Ingress backend service name matches what the chart actually creates (check chart README)
- [ ] DNS records planned for both Pi-holes
- [ ] Credentials saved to 1Password before sealing
- [ ] `docs/cluster-reference.md` updated with new service entry
