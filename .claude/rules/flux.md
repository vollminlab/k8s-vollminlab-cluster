---
description: Flux CD conventions, repo layout, HelmRelease patterns, and reconciliation commands for k8s-vollminlab-cluster
---

# Flux CD Rules

## Repo layout

```
bootstrap/                        # NOT Flux-managed — manual DR reference only
clusters/vollminlab-cluster/
  flux-system/
    repositories/                 # HelmRepository + GitRepository CRDs (one file each)
    flux-kustomizations/          # Flux Kustomization CRs (one per namespace/app group)
  [namespace]/
    namespace.yaml
    kustomization.yaml            # Aggregates all resources in this namespace
    [app]/app/                    # Per-app: helmrelease.yaml, configmap.yaml, ingress.yaml, etc.
```

## App file structure (every app)

```
helmrelease.yaml       # Required — HelmRelease CR
configmap.yaml         # Required — Helm values via valuesFrom ConfigMap
kustomization.yaml     # Required — lists all resources in this dir
ingress.yaml           # Optional
pvc-*.yaml             # Optional
*-externalsecret.yaml  # Optional (ESO + 1Password — never plain Secret)
networkpolicy.yaml     # Optional (required in dmz/)
```

## Adding a new app — two explicit indexes MUST be updated

Both of these files are **explicit lists**, not globs. Flux will silently ignore any file not listed. Missing either one means the app never deploys.

### 1. `flux-system/flux-kustomizations/kustomization.yaml`

Add `- [app]-kustomization.yaml` to the `resources` list.
This is what causes Flux to pick up and reconcile the new Kustomization CR.

### 2. `flux-system/repositories/kustomization.yaml`

Add `- [app]-helmrepository.yaml` (or `-gitrepository.yaml`) to the `resources` list.
This is what causes Flux to sync the chart source. Without it the HelmRelease can never pull the chart.

**Both must be in the same PR as the app files. Never add an app without updating both.**

## Source repository conventions

- File always named `[app-name]-helmrepository.yaml` regardless of kind
- `metadata.name: [app-name]-repo` — always suffix with `-repo`, no exceptions
- Named after the **app being deployed**, not the chart author
- HTTP registry → `kind: HelmRepository` (`source.toolkit.fluxcd.io/v1`)
- OCI registry → `kind: OCIRepository` (`source.toolkit.fluxcd.io/v1`) — **do not use `HelmRepository type: oci`, it is in maintenance mode**
- Version is pinned in the `OCIRepository` `spec.ref.tag`, not in the HelmRelease

Copy-paste templates: `docs/runbooks/flux-templates.md`

## Required labels — all resource kinds

Every resource in this repo must have these three labels. Kyverno enforces on pods; we apply them consistently to all kinds for uniformity.

| Kind                            | Required labels                          |
| ------------------------------- | ---------------------------------------- |
| HelmRelease                     | `app`, `env: production`, `category`     |
| HelmRepository / OCIRepository  | `app`, `env: production`, `category`     |
| Namespace                       | `app`, `env: production`, `category`     |
| Ingress                         | `app`, `env: production`, `category`     |
| Flux Kustomization CR           | `app`, `env: production`, `category`     |
| ConfigMap (values)              | `app`, `env: production`, `category`     |

See `kyverno.md` for valid `category` values.

## Naming conventions — `metadata.name` and filename

Every resource kind follows a predictable pattern. Use this table when creating any new resource.

| Kind | `metadata.name` | Filename |
|------|----------------|----------|
| HelmRelease | `{app-name}` | `helmrelease.yaml` |
| HelmRepository / OCIRepository | `{app-name}-repo` | `{app-name}-helmrepository.yaml` |
| Namespace | `{namespace-dir}` | `namespace.yaml` |
| ConfigMap (Helm values) | `{app-name}-values` | `configmap.yaml` |
| Ingress | `{app-name}-ingress` (add qualifier for split ingresses: `{app-name}-{qualifier}-ingress`) | `ingress.yaml` or `ingress-{qualifier}.yaml` |
| ExternalSecret | `{app-name}-{purpose}` — never use `-secret` as suffix (redundant) | `{metadata.name}-externalsecret.yaml` |
| Flux Kustomization CR | `{namespace}-{app}` | `{app}-kustomization.yaml` |

**ExternalSecret filename rule**: the filename base **must exactly equal** `metadata.name`. Stripping `-externalsecret.yaml` from the filename must give you the object name. See `secrets.md` for the full ESO + 1Password workflow.

## Critical rules

- **Never manually apply** manifests under `clusters/` — Flux reconciles from `main` within 10 minutes.
- **Never push directly to `main`** — branch protection enforced via GitHub repository settings, PR required.
- **Never use `:latest`** chart version ranges or image tags.
- **Never use inline `values:` in a HelmRelease.** All Helm values go in `configmap.yaml` and are referenced via `valuesFrom`. This keeps HelmRelease files minimal and diffs readable.
- `bootstrap/` is for DR reference only; changes there have no effect on the cluster.

## Emergency: HelmRelease stuck in failure loop

See `docs/runbooks/kyverno-recovery.md`. Short version: `helm rollback` first, then `flux reconcile`. Retrying reconcile before rollback replays the stale failure.

## Useful commands

```bash
# Check reconciliation state
flux get kustomizations -A
flux get helmreleases -A

# Force reconciliation
flux reconcile kustomization [name] --with-source

# Check a specific HelmRelease
flux get helmrelease [name] -n [namespace]

# Helm release history (useful for diagnosing stuck upgrades)
helm history [release-name] -n [namespace]

# Debug events
kubectl describe helmrelease [name] -n [namespace]
kubectl get events -n [namespace] --sort-by=.lastTimestamp
```
