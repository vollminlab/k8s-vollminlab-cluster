# Flux Resource Templates

Copy-paste starting points for new apps. See `.claude/rules/flux.md` for conventions.

## HelmRelease — standard HTTP registry

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: [app-name]
  namespace: [namespace]
  labels:
    app: [app-name]
    env: production
    category: [category]
spec:
  interval: 10m
  chart:
    spec:
      chart: [chart-name]
      version: [pinned-semver]    # never * or floating ranges
      sourceRef:
        kind: HelmRepository
        name: [app-name]-repo
        namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: [app-name]-values
```

## HelmRelease — OCI registry

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: [app-name]
  namespace: [namespace]
  labels:
    app: [app-name]
    env: production
    category: [category]
spec:
  interval: 10m
  chartRef:
    kind: OCIRepository
    name: [app-name]-repo
    namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: [app-name]-values
```

## HelmRepository (HTTP)

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: [app-name]-repo
  namespace: flux-system
  labels:
    app: [app-name]
    env: production
    category: [category]
spec:
  interval: 1h
  url: https://[chart-repo-url]
```

## OCIRepository

**Do not use `HelmRepository type: oci` — it is in maintenance mode.**

```yaml
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: OCIRepository
metadata:
  name: [app-name]-repo
  namespace: flux-system
  labels:
    app: [app-name]
    env: production
    category: [category]
spec:
  interval: 1h
  url: oci://[registry]/[chart-name]
  ref:
    tag: "[pinned-semver]"
  layerSelector:
    mediaType: "application/vnd.cncf.helm.chart.content.v1.tar+gzip"
    operation: copy
```

## Flux Kustomization CR

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: [app-name]
  namespace: flux-system
  labels:
    app: [app-name]
    env: production
    category: [category]
spec:
  interval: 10m
  path: ./clusters/vollminlab-cluster/[namespace]
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
  timeout: 10m
  dependsOn:
    - name: [dependency]
```

## Namespace

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: [namespace]
  labels:
    app: [app-name]
    env: production
    category: [category]
```

## App kustomization.yaml

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - helmrelease.yaml
  - configmap.yaml
  # - ingress.yaml
  # - *-externalsecret.yaml
```
