# Add New Application

Guide for adding a new Helm-based application to the cluster following the established patterns.

## Usage

`/project:add-app [app-name] [namespace] [chart] [helm-repo-url]`

## Steps to complete

1. **Check if a HelmRepository already exists** for this chart's repo in `clusters/vollminlab-cluster/flux-system/repositories/`. If not, create `[repo-name]-helmrepository.yaml` following the existing pattern.

2. **Create the namespace directory** if it doesn't exist:
   - `clusters/vollminlab-cluster/[namespace]/namespace.yaml`
   - `clusters/vollminlab-cluster/[namespace]/kustomization.yaml`

3. **Create the app directory**: `clusters/vollminlab-cluster/[namespace]/[app-name]/app/`

4. **Create these files in the app directory**:

   `kustomization.yaml` — list all resources:
   ```yaml
   apiVersion: kustomize.config.k8s.io/v1beta1
   kind: Kustomization
   resources:
     - helmrelease.yaml
     - configmap.yaml
   ```

   `helmrelease.yaml` — Flux HelmRelease with pinned version:
   ```yaml
   apiVersion: helm.toolkit.fluxcd.io/v2
   kind: HelmRelease
   metadata:
     name: [app-name]
     namespace: [namespace]
     labels:
       app: [app-name]
       env: production
       category: [core|security|storage|networking|observability|apps|gaming]
   spec:
     interval: 10m
     chart:
       spec:
         chart: [chart-name]
         version: [pinned-version]
         sourceRef:
           kind: HelmRepository
           name: [repo-name]
           namespace: flux-system
     valuesFrom:
       - kind: ConfigMap
         name: [app-name]-values
   ```

   `configmap.yaml` — Helm values:
   ```yaml
   apiVersion: v1
   kind: ConfigMap
   metadata:
     name: [app-name]-values
     namespace: [namespace]
   data:
     values.yaml: |
       # helm values here
   ```

5. **Create a Flux Kustomization CR** in `clusters/vollminlab-cluster/flux-system/flux-kustomizations/[namespace]-kustomization.yaml`:
   ```yaml
   apiVersion: kustomize.toolkit.fluxcd.io/v1
   kind: Kustomization
   metadata:
     name: [namespace]
     namespace: flux-system
   spec:
     interval: 10m
     path: ./clusters/vollminlab-cluster/[namespace]
     prune: true
     sourceRef:
       kind: GitRepository
       name: flux-system
     timeout: 5m
   ```

6. **Verify labels**: All pod templates must have `app`, `env: production`, and `category` labels — Kyverno enforces this in enforce mode.

7. **No plain Secrets**: If the app needs secrets, create an `ExternalSecret` sourced from 1Password via ESO.

After creating files, remind the user to:
- Open a PR (direct push to main is blocked)
- Watch `flux get helmreleases -n [namespace]` after merge to confirm reconciliation
