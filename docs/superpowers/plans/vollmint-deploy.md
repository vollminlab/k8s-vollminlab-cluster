# Vollmint Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship vollmint to the cluster: containerize the app (multi-stage Dockerfile), add a Helm chart and tag-driven CI that publishes image + chart to Harbor, then add the cluster-side GitOps manifests (namespace, CNPG database, ExternalSecrets, HelmRelease via OCIRepository, ingress with Authentik forward-auth, NetworkPolicies, Flux wiring), register the Authentik application via tofu, add the homepage tile, and finish with rollout verification and the Venmo CSV backfill. End state: `https://vollmint.vollminlab.com` live behind Authentik, syncing SimpleFIN twice daily.

**Architecture:** One image, two entrypoints. `vollmint serve` runs as a stateless Deployment (1 replica, no PVC, `Recreate`); `vollmint sync` runs as a CronJob at 06:10/18:10 UTC. Postgres is a CNPG `Cluster` (`vollmint-db`, 2 instances × 5Gi Longhorn) with barman backups to MinIO. **CNPG generates its own app credentials** — the app consumes `DATABASE_URL` from the auto-generated `vollmint-db-app` Secret, key `uri` (no bootstrap secret, no new 1Password item for the DB). The SimpleFIN access URL is the only new secret: 1Password item `SimpleFIN Access URL` → ExternalSecret `vollmint-simplefin` → **CronJob only** (the serve Deployment never sees it). LAN/Tailscale only — never exposed through Cloudflare.

**Tech Stack:** Go 1.26 + React 18 SPA (embedded via `go:embed`), distroless runtime image, Helm chart in-repo, GitHub Actions on ARC (`runs-on: vollminlab`) publishing to Harbor OCI, Flux CD (OCIRepository + HelmRelease), CNPG 1.30, ESO + 1Password, Authentik forward-auth (tofu-managed), ingress-nginx + external-dns + shlink slug.

**Prerequisite:** vollmint plans 1 (backend) and 2 (API + SPA) are merged to `vollminlab/vollmint` `main`. This plan spans **two repos and two PRs** with a strict merge order:

1. `vollminlab/vollmint`, branch `feat/deploy` — Dockerfile, chart, CI. Merge → set repo secrets → tag `v0.1.0` → CI publishes to Harbor.
2. `k8s-vollminlab-cluster`, branch `feat/vollmint` — cluster manifests pinning tag `0.1.0`. **Must not merge until the Harbor artifacts exist**, or the OCIRepository/HelmRelease will fail to reconcile.

## Context for the implementer

Invariants — read once, apply throughout:

1. **NEVER merge any PR.** Open PRs, watch CI, report. Merging requires Scott's explicit word — both PRs, no exceptions.
2. **No plain secrets anywhere.** No `kind: Secret` manifests, no credential values in any file (even untracked). ExternalSecrets + 1Password only. Use `op` CLI to read credentials; never echo values.
3. **Local Go commands** (vollmint repo) need `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"` first. DB tests need `export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable'` (local dev Postgres on 5433). Always `go test -count=1`.
4. **Kyverno enforce mode:** every pod needs `app`, `env`, `category` labels, CPU+memory requests and limits, pinned image tags (no `:latest`), no privileged/hostPath, not in `default` namespace. The chart templates below satisfy all of these — don't strip anything.
5. **Flux indexes are explicit lists.** Both `flux-system/repositories/kustomization.yaml` and `flux-system/flux-kustomizations/kustomization.yaml` must gain entries, and every app-dir `kustomization.yaml` must list every file. A file not listed silently never deploys.
6. **NetworkPolicy ports are container ports** (post-DNAT). vollmint's container listens on 8080 (`LISTEN_ADDR` default `:8080`); the Service also uses 8080 so there's no remap — but verify at rollout, don't assume.
7. **Authentik is managed by tofu** (`terraform/authentik/` in the cluster repo), never akshell/UI for creation. akshell is for verification only. The design doc's "create Application via akshell" instruction is superseded by this rule.
8. Work in **git worktrees** (`.worktrees/<prefix>/<name>` in both repos), stage files **explicitly by name**, one concern per PR.

Deliberate deviations from `docs/superpowers/specs/vollmint-design.md` (all resolved during plan research — do not "fix" them back):

| Design doc says | This plan does | Why |
|---|---|---|
| CI includes a Trivy image scan | No Trivy step | Neither shlink-ingress-controller nor longhorn-rebalancing-controller CI has one; trivy-operator already scans runtime images in-cluster. Follow repo precedent. |
| Flux Kustomization named `vollmint-vollmint` | `metadata.name: vollmint` | Repo convention is the namespace name (`shlink`, `harbor`, `mediastack`). |
| Create Authentik Application via akshell | tofu resource in `terraform/authentik/applications.tf` | House rule: Authentik is always managed by tofu. |
| DB credentials via ExternalSecret | CNPG auto-generated `vollmint-db-app` Secret, key `uri` | CNPG creates app credentials when `bootstrap.initdb` has no `secret:` block; the `-app` Secret's documented keys include `uri` (full DSN). Eliminates a new 1P item entirely. |

---

## Part A — `vollminlab/vollmint`, branch `feat/deploy`

### Task 1: Worktree and branch

**Files:** none (setup)

- [ ] **Step 1:** Create the worktree off current main:
  ```bash
  cd ~/repos/vollminlab/vollmint && git checkout main && git pull
  git worktree add .worktrees/feat-deploy -b feat/deploy
  cd .worktrees/feat-deploy
  ```
- [ ] **Step 2:** Baseline: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"` then `go build ./... && go vet ./...`. Run: both → PASS (no output/errors). If FAIL, stop and report — main is broken.

---

### Task 2: Dockerfile + .dockerignore

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

The build order matters: `web/embed.go` has `//go:embed all:dist`, so `web/dist` must exist before `go build`. Stage 1 builds the SPA, stage 2 overlays `dist` into the source tree and compiles, stage 3 is distroless.

- [ ] **Step 1:** Create `Dockerfile`:
  ```dockerfile
  # Stage 1: build the React SPA
  FROM node:20-alpine AS web
  WORKDIR /src/web
  COPY web/package.json web/package-lock.json ./
  RUN npm ci
  COPY web/ ./
  RUN npm run build

  # Stage 2: compile the Go binary with the SPA embedded
  FROM golang:1.26-alpine AS build
  WORKDIR /src
  COPY go.mod go.sum ./
  RUN go mod download
  COPY . .
  COPY --from=web /src/web/dist ./web/dist
  RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /vollmint ./cmd/vollmint

  # Stage 3: runtime
  FROM gcr.io/distroless/static:nonroot
  COPY --from=build /vollmint /vollmint
  USER 65532:65532
  EXPOSE 8080
  ENTRYPOINT ["/vollmint"]
  CMD ["serve"]
  ```
- [ ] **Step 2:** Create `.dockerignore`:
  ```
  .git
  .worktrees
  bin/
  web/node_modules/
  web/dist/
  Dockerfile
  .dockerignore
  ```
  (`web/dist` excluded so a stale local build never leaks into the image — the embedded dist always comes from stage 1.)
- [ ] **Step 3:** Run: `docker build -t vollmint:dev .` → PASS (image builds). Then `docker run --rm vollmint:dev --help || true` → prints CLI usage listing the `serve` / `sync` / `import-venmo` subcommands (non-zero exit is fine).

---

### Task 3: CI workflow (tests on PR/push)

**Files:**
- Create: `.github/workflows/ci.yml`

ARC runners (`runs-on: vollminlab`) run a dind sidecar, so GitHub `services:` containers work; published ports are reachable on `localhost`. Action versions verified current: `actions/checkout@v6`, `actions/setup-go@v6`, `actions/setup-node@v7`.

- [ ] **Step 1:** Create `.github/workflows/ci.yml`:
  ```yaml
  name: CI
  on:
    push:
      branches: [main]
    pull_request:
      branches: [main]

  jobs:
    go-test:
      name: Go Tests
      runs-on: vollminlab
      services:
        postgres:
          image: postgres:16
          env:
            POSTGRES_PASSWORD: dev
          ports:
            - 5432:5432
          options: >-
            --health-cmd "pg_isready -U postgres"
            --health-interval 5s
            --health-timeout 5s
            --health-retries 10
      steps:
        - uses: actions/checkout@v6
        - uses: actions/setup-go@v6
          with:
            go-version-file: go.mod
        - name: Run tests
          env:
            TEST_DATABASE_URL: postgres://postgres:dev@localhost:5432/postgres?sslmode=disable
          run: go test -count=1 ./...

    web-test:
      name: Web Tests
      runs-on: vollminlab
      steps:
        - uses: actions/checkout@v6
        - uses: actions/setup-node@v7
          with:
            node-version: 20
        - name: Install dependencies
          working-directory: web
          run: npm ci
        - name: Run tests
          working-directory: web
          run: npm test
        - name: Build
          working-directory: web
          run: npm run build
  ```
- [ ] **Step 2:** Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"` → PASS (valid YAML). Real validation happens when the PR opens.

---

### Task 4: Helm chart scaffold

**Files:**
- Create: `charts/vollmint/Chart.yaml`
- Create: `charts/vollmint/values.yaml`
- Create: `charts/vollmint/templates/_helpers.tpl`

Chart lives in-repo and is published to `oci://harbor.vollminlab.com/vollminlab/charts` by the release workflow (Task 7), mirroring longhorn-rebalancing-controller. `version`/`appVersion` stay in lockstep with git tags — the workflow overrides both at package time, so the committed values are just the baseline.

- [ ] **Step 1:** Create `charts/vollmint/Chart.yaml`:
  ```yaml
  apiVersion: v2
  name: vollmint
  description: Household budget tracker — Go API + embedded React SPA (SimpleFIN + Venmo CSV)
  type: application
  version: 0.1.0
  appVersion: "0.1.0"
  ```
- [ ] **Step 2:** Create `charts/vollmint/values.yaml`:
  ```yaml
  image:
    repository: harbor.vollminlab.com/vollminlab/vollmint
    tag: ""            # defaults to Chart.AppVersion
    pullPolicy: IfNotPresent

  imagePullSecrets:
    - name: harbor-vollminlab-pull

  # CNPG auto-generated app credentials; key "uri" is the full DSN
  database:
    secretName: vollmint-db-app
    secretKey: uri

  sync:
    schedule: "10 6,18 * * *"   # UTC; SimpleFIN data refreshes ~daily
    suspend: false
    simplefinSecretName: vollmint-simplefin
    simplefinSecretKey: token
    resources:
      requests:
        cpu: 50m
        memory: 64Mi
      limits:
        cpu: 500m
        memory: 256Mi

  resources:
    requests:
      cpu: 50m
      memory: 64Mi
    limits:
      cpu: 500m
      memory: 256Mi

  nameOverride: ""
  fullnameOverride: ""
  ```
- [ ] **Step 3:** Create `charts/vollmint/templates/_helpers.tpl`. Serve pods and sync pods get **different** `app` / `app.kubernetes.io/name` values so the homepage k8s badge (which matches `app.kubernetes.io/name=vollmint`) never counts completed sync Job pods:
  ```
  {{- define "vollmint.name" -}}
  {{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
  {{- end }}

  {{- define "vollmint.fullname" -}}
  {{- if .Values.fullnameOverride }}
  {{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
  {{- else }}
  {{- $name := default .Chart.Name .Values.nameOverride }}
  {{- if contains $name .Release.Name }}
  {{- .Release.Name | trunc 63 | trimSuffix "-" }}
  {{- else }}
  {{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
  {{- end }}
  {{- end }}
  {{- end }}

  {{- define "vollmint.labels" -}}
  app: {{ include "vollmint.fullname" . }}
  env: production
  category: apps
  app.kubernetes.io/name: {{ include "vollmint.name" . }}
  app.kubernetes.io/instance: {{ .Release.Name }}
  app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
  {{- end }}

  {{- define "vollmint.selectorLabels" -}}
  app: {{ include "vollmint.fullname" . }}
  app.kubernetes.io/name: {{ include "vollmint.name" . }}
  app.kubernetes.io/instance: {{ .Release.Name }}
  {{- end }}

  {{- define "vollmint.syncLabels" -}}
  app: {{ include "vollmint.fullname" . }}-sync
  env: production
  category: apps
  app.kubernetes.io/name: {{ include "vollmint.name" . }}-sync
  app.kubernetes.io/instance: {{ .Release.Name }}
  app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
  {{- end }}
  ```

---

### Task 5: Deployment + Service templates

**Files:**
- Create: `charts/vollmint/templates/deployment.yaml`
- Create: `charts/vollmint/templates/service.yaml`

- [ ] **Step 1:** Create `charts/vollmint/templates/deployment.yaml`. Stateless + `Recreate` (matches the repo rule for single-replica apps; no PVC involved but Recreate keeps a single serve instance during rollouts):
  ```yaml
  apiVersion: apps/v1
  kind: Deployment
  metadata:
    name: {{ include "vollmint.fullname" . }}
    labels:
      {{- include "vollmint.labels" . | nindent 6 }}
  spec:
    replicas: 1
    strategy:
      type: Recreate
    selector:
      matchLabels:
        {{- include "vollmint.selectorLabels" . | nindent 8 }}
    template:
      metadata:
        labels:
          {{- include "vollmint.labels" . | nindent 10 }}
      spec:
        {{- with .Values.imagePullSecrets }}
        imagePullSecrets:
          {{- toYaml . | nindent 10 }}
        {{- end }}
        securityContext:
          runAsNonRoot: true
          runAsUser: 65532
          seccompProfile:
            type: RuntimeDefault
        containers:
          - name: vollmint
            image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
            imagePullPolicy: {{ .Values.image.pullPolicy }}
            args: ["serve"]
            env:
              - name: DATABASE_URL
                valueFrom:
                  secretKeyRef:
                    name: {{ .Values.database.secretName }}
                    key: {{ .Values.database.secretKey }}
            ports:
              - name: http
                containerPort: 8080
            readinessProbe:
              httpGet:
                path: /healthz
                port: http
              initialDelaySeconds: 3
              periodSeconds: 10
            livenessProbe:
              httpGet:
                path: /healthz
                port: http
              initialDelaySeconds: 10
              periodSeconds: 30
            securityContext:
              allowPrivilegeEscalation: false
              capabilities:
                drop: ["ALL"]
              readOnlyRootFilesystem: true
            resources:
              {{- toYaml .Values.resources | nindent 14 }}
  ```
- [ ] **Step 2:** Create `charts/vollmint/templates/service.yaml`:
  ```yaml
  apiVersion: v1
  kind: Service
  metadata:
    name: {{ include "vollmint.fullname" . }}
    labels:
      {{- include "vollmint.labels" . | nindent 6 }}
  spec:
    selector:
      {{- include "vollmint.selectorLabels" . | nindent 6 }}
    ports:
      - name: http
        port: 8080
        targetPort: http
  ```

---

### Task 6: Sync CronJob template

**Files:**
- Create: `charts/vollmint/templates/cronjob.yaml`

The sync entrypoint gets `DATABASE_URL` **and** `SIMPLEFIN_ACCESS_URL`; the serve Deployment deliberately never mounts the SimpleFIN secret.

- [ ] **Step 1:** Create `charts/vollmint/templates/cronjob.yaml`:
  ```yaml
  apiVersion: batch/v1
  kind: CronJob
  metadata:
    name: {{ include "vollmint.fullname" . }}-sync
    labels:
      {{- include "vollmint.syncLabels" . | nindent 6 }}
  spec:
    schedule: {{ .Values.sync.schedule | quote }}
    timeZone: Etc/UTC
    suspend: {{ .Values.sync.suspend }}
    concurrencyPolicy: Forbid
    startingDeadlineSeconds: 3600
    successfulJobsHistoryLimit: 3
    failedJobsHistoryLimit: 3
    jobTemplate:
      metadata:
        labels:
          {{- include "vollmint.syncLabels" . | nindent 10 }}
      spec:
        backoffLimit: 1
        activeDeadlineSeconds: 900
        template:
          metadata:
            labels:
              {{- include "vollmint.syncLabels" . | nindent 14 }}
          spec:
            restartPolicy: Never
            {{- with .Values.imagePullSecrets }}
            imagePullSecrets:
              {{- toYaml . | nindent 14 }}
            {{- end }}
            securityContext:
              runAsNonRoot: true
              runAsUser: 65532
              seccompProfile:
                type: RuntimeDefault
            containers:
              - name: sync
                image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
                imagePullPolicy: {{ .Values.image.pullPolicy }}
                args: ["sync"]
                env:
                  - name: DATABASE_URL
                    valueFrom:
                      secretKeyRef:
                        name: {{ .Values.database.secretName }}
                        key: {{ .Values.database.secretKey }}
                  - name: SIMPLEFIN_ACCESS_URL
                    valueFrom:
                      secretKeyRef:
                        name: {{ .Values.sync.simplefinSecretName }}
                        key: {{ .Values.sync.simplefinSecretKey }}
                securityContext:
                  allowPrivilegeEscalation: false
                  capabilities:
                    drop: ["ALL"]
                  readOnlyRootFilesystem: true
                resources:
                  {{- toYaml .Values.sync.resources | nindent 18 }}
  ```
- [ ] **Step 2:** Run: `helm lint charts/vollmint` → PASS (0 failures). Then `helm template vollmint charts/vollmint | grep -E "app:|app.kubernetes.io/name:"` → serve resources show `app: vollmint`, cronjob pods show `app: vollmint-sync`.

---

### Task 7: Release workflow (tag → Harbor)

**Files:**
- Create: `.github/workflows/build.yml`

Direct copy of longhorn-rebalancing-controller's `build.yml` with names swapped — same action versions (`docker/login-action@v4`, `docker/build-push-action@v7`, `azure/setup-helm@v5`), same two-tag image push (`v0.1.0` + `0.1.0`), same chart publish with `--version`/`--app-version` from the tag.

- [ ] **Step 1:** Create `.github/workflows/build.yml`:
  ```yaml
  name: Build and Publish
  on:
    push:
      tags: ['v*']

  jobs:
    build:
      name: Build Image
      runs-on: vollminlab
      steps:
        - uses: actions/checkout@v6
        - uses: docker/login-action@v4
          with:
            registry: harbor.vollminlab.com
            username: ${{ secrets.HARBOR_USERNAME }}
            password: ${{ secrets.HARBOR_PASSWORD }}
        - name: Compute semver
          run: |
            REF="${{ github.ref_name }}"
            echo "SEMVER=${REF#v}" >> "$GITHUB_ENV"
        - uses: docker/build-push-action@v7
          with:
            context: .
            push: true
            tags: |
              harbor.vollminlab.com/vollminlab/vollmint:${{ github.ref_name }}
              harbor.vollminlab.com/vollminlab/vollmint:${{ env.SEMVER }}

    publish-chart:
      name: Publish Helm Chart
      runs-on: vollminlab
      needs: build
      steps:
        - uses: actions/checkout@v6
        - uses: azure/setup-helm@v5
        - name: Compute semver
          run: |
            REF="${{ github.ref_name }}"
            echo "SEMVER=${REF#v}" >> "$GITHUB_ENV"
        - name: Login to Harbor OCI
          run: |
            echo "${{ secrets.HARBOR_PASSWORD }}" | helm registry login harbor.vollminlab.com \
              --username "${{ secrets.HARBOR_USERNAME }}" --password-stdin
        - name: Package and push chart
          run: |
            helm package charts/vollmint --version "${SEMVER}" --app-version "${SEMVER}"
            helm push "vollmint-${SEMVER}.tgz" oci://harbor.vollminlab.com/vollminlab/charts
  ```
- [ ] **Step 2:** Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/build.yml'))"` → PASS.

---

### Task 8: Commit, push, open PR — STOP

**Files:** none (git)

- [ ] **Step 1:** Stage explicitly and commit:
  ```bash
  git add Dockerfile .dockerignore .github/workflows/ci.yml .github/workflows/build.yml \
    charts/vollmint/Chart.yaml charts/vollmint/values.yaml \
    charts/vollmint/templates/_helpers.tpl charts/vollmint/templates/deployment.yaml \
    charts/vollmint/templates/service.yaml charts/vollmint/templates/cronjob.yaml
  git commit -m "feat: add Dockerfile, Helm chart, and Harbor release CI"
  ```
- [ ] **Step 2:** Push and open the PR: `git push -u origin feat/deploy && gh pr create --title "feat: Dockerfile, Helm chart, Harbor release CI" --body "<summary of Part A>"`. Watch CI (`gh pr checks --watch`). Note: if CI jobs sit queued forever, the ARC runner group may not cover the private vollmint repo — report that to Scott (org-settings fix, not a code fix).
- [ ] **Step 3:** **STOP — do not merge.** Merging requires Scott's explicit approval (house rule).

---

### Task 9: Post-merge — repo secrets, tag, verify Harbor (after Scott merges the vollmint PR)

**Files:** none (operations)

- [ ] **Step 1:** Set the Harbor robot credentials as repo secrets. Find the item first (`op item list --vault Homelab --format json | python3 -c "import json,sys; [print(i['title']) for i in json.load(sys.stdin) if 'harbor' in i['title'].lower()]"`) — expected: the github-actions robot item used by shlink-ingress-controller / longhorn-rebalancing-controller. Username is the robot account name (`robot$github-actions` form). Pipe values directly, never echo:
  ```bash
  op item get "<item>" --vault Homelab --format json | python3 -c "..." | gh secret set HARBOR_USERNAME --repo vollminlab/vollmint
  op item get "<item>" --vault Homelab --format json | python3 -c "..." | gh secret set HARBOR_PASSWORD --repo vollminlab/vollmint
  ```
- [ ] **Step 2:** Tag the release from updated main:
  ```bash
  git checkout main && git pull && git tag v0.1.0 && git push origin v0.1.0
  gh run watch
  ```
- [ ] **Step 3:** Verify both artifacts exist in Harbor. Run: `helm show chart oci://harbor.vollminlab.com/vollminlab/charts/vollmint --version 0.1.0` → PASS (chart metadata prints; login with the cluster-pull robot via `op` if anonymous pull is off). Image: `docker manifest inspect harbor.vollminlab.com/vollminlab/vollmint:0.1.0` → PASS.

---

## Part B — `k8s-vollminlab-cluster`, branch `feat/vollmint`

### Task 10: Worktree + 1Password prerequisites

**Files:** none (setup/verification)

- [ ] **Step 1:** `cd ~/repos/vollminlab/k8s-vollminlab-cluster && git checkout main && git pull && git worktree add .worktrees/feat/vollmint -b feat/vollmint && cd .worktrees/feat/vollmint`
- [ ] **Step 2:** Verify the SimpleFIN item exists with the exact field label the ExternalSecret will reference. Run: `op item get "SimpleFIN Access URL" --vault Homelab --format json | python3 -c "import json,sys; print([f['label'] for f in json.load(sys.stdin)['fields']])"` → must include `token`. If the item or field is missing/misnamed, fix in 1Password first (locked field-label list: `token` is allowed).
- [ ] **Step 3:** Confirm the shared items referenced by copied ExternalSecrets exist: `CNPG MinIO Credentials`-equivalent (whatever `shlink/shlink-db/app/cnpg-minio-credentials-externalsecret.yaml` references — read that file and use the same key) and `Harbor Cluster Pull Robot Account`.

---

### Task 11: Namespace + CNPG database

**Files:**
- Create: `clusters/vollminlab-cluster/vollmint/namespace.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/kustomization.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/vollmint-db/app/cluster.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/vollmint-db/app/scheduled-backup.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/vollmint-db/app/cnpg-minio-credentials-externalsecret.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/vollmint-db/app/kustomization.yaml`

- [ ] **Step 1:** `namespace.yaml` (shlink pattern):
  ```yaml
  apiVersion: v1
  kind: Namespace
  metadata:
    name: vollmint
    labels:
      app: vollmint
      env: production
      category: apps
      goldilocks.fairwinds.com/enabled: "true"
      pod-security.kubernetes.io/warn: restricted
      pod-security.kubernetes.io/audit: restricted
  ```
- [ ] **Step 2:** `vollmint-db/app/cluster.yaml` — copy `shlink/shlink-db/app/cluster.yaml` and adjust. Key differences from shlink: **no `secret:` under `initdb`** (CNPG then generates `vollmint-db-app` with keys incl. `uri`) and no `postInitSQL` (fresh DB owned by `vollmint`; the GRANT dance is only needed for imported DBs). Target content (preserve the exact field layout of the shlink file, in particular `retentionPolicy`'s position in the `backup` block):
  ```yaml
  apiVersion: postgresql.cnpg.io/v1
  kind: Cluster
  metadata:
    name: vollmint-db
    namespace: vollmint
    labels:
      app: vollmint-db
      env: production
      category: apps
  spec:
    instances: 2
    inheritedMetadata:
      labels:
        app: vollmint-db
        env: production
        category: apps
    storage:
      size: 5Gi
      storageClass: longhorn
    bootstrap:
      initdb:
        database: vollmint
        owner: vollmint
    backup:
      barmanObjectStore:
        destinationPath: s3://cnpg-backups/vollmint-db
        endpointURL: http://minio.minio.svc.cluster.local:9000
        s3Credentials:
          accessKeyId:
            name: cnpg-minio-credentials
            key: ACCESS_KEY_ID
          secretAccessKey:
            name: cnpg-minio-credentials
            key: ACCESS_SECRET_KEY
        wal:
          compression: gzip
          maxParallel: 2
      retentionPolicy: "14d"
  ```
  (Longhorn headroom verified 2026-07-25: 2 instances × 5Gi × 3 replicas = 30Gi; every node has ≥68Gi schedulable.)
- [ ] **Step 3:** `vollmint-db/app/scheduled-backup.yaml` — next free slot in the nightly CNPG sequence (authentik 01:00, harbor 01:15, shlink 01:30 → vollmint 01:45; 6-field cron):
  ```yaml
  apiVersion: postgresql.cnpg.io/v1
  kind: ScheduledBackup
  metadata:
    name: vollmint-db-backup
    namespace: vollmint
    labels:
      app: vollmint-db
      env: production
      category: apps
  spec:
    schedule: "0 45 1 * * *"
    backupOwnerReference: self
    cluster:
      name: vollmint-db
  ```
- [ ] **Step 4:** `vollmint-db/app/cnpg-minio-credentials-externalsecret.yaml` — copy the shlink-db file verbatim, changing only `namespace: vollmint` and the `app` label to `vollmint-db`.
- [ ] **Step 5:** `vollmint-db/app/kustomization.yaml`:
  ```yaml
  apiVersion: kustomize.config.k8s.io/v1beta1
  kind: Kustomization
  resources:
    - cluster.yaml
    - scheduled-backup.yaml
    - cnpg-minio-credentials-externalsecret.yaml
  ```
- [ ] **Step 6:** Top-level `vollmint/kustomization.yaml`:
  ```yaml
  apiVersion: kustomize.config.k8s.io/v1beta1
  kind: Kustomization
  resources:
    - namespace.yaml
    - vollmint/app
    - vollmint-db/app
    - networkpolicies
  ```
  (Check how `authentik/kustomization.yaml` lists its `networkpolicies` dir and mirror that exact form.)

---

### Task 12: App manifests (HelmRelease, values, ExternalSecrets, ingress)

**Files:**
- Create: `clusters/vollminlab-cluster/vollmint/vollmint/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/vollmint/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/vollmint/app/vollmint-simplefin-externalsecret.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/vollmint/app/harbor-vollminlab-pull-externalsecret.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/vollmint/app/ingress.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/vollmint/app/kustomization.yaml`

- [ ] **Step 1:** `helmrelease.yaml` (LRC pattern — `chartRef` to the flux-system OCIRepository):
  ```yaml
  apiVersion: helm.toolkit.fluxcd.io/v2
  kind: HelmRelease
  metadata:
    name: vollmint
    namespace: vollmint
    labels:
      app: vollmint
      env: production
      category: apps
  spec:
    interval: 10m
    timeout: 5m
    chartRef:
      kind: OCIRepository
      name: vollmint-repo
      namespace: flux-system
    valuesFrom:
      - kind: ConfigMap
        name: vollmint-values
  ```
- [ ] **Step 2:** `configmap.yaml` (all values here, never inline in the HelmRelease):
  ```yaml
  apiVersion: v1
  kind: ConfigMap
  metadata:
    name: vollmint-values
    namespace: vollmint
    labels:
      app: vollmint
      env: production
      category: apps
  data:
    values.yaml: |
      resources:
        requests:
          cpu: 50m
          memory: 64Mi
        limits:
          cpu: 500m
          memory: 256Mi
      sync:
        schedule: "10 6,18 * * *"
        suspend: false
  ```
- [ ] **Step 3:** `vollmint-simplefin-externalsecret.yaml`:
  ```yaml
  apiVersion: external-secrets.io/v1
  kind: ExternalSecret
  metadata:
    name: vollmint-simplefin
    namespace: vollmint
    labels:
      app: vollmint
      env: production
      category: apps
  spec:
    refreshInterval: 1h
    secretStoreRef:
      name: onepassword-cluster-store
      kind: ClusterSecretStore
    target:
      name: vollmint-simplefin
      creationPolicy: Owner
    data:
      - secretKey: token
        remoteRef:
          key: "SimpleFIN Access URL"
          property: token
  ```
- [ ] **Step 4:** `harbor-vollminlab-pull-externalsecret.yaml` — copy `shlink/shlink-ingress-controller/app/harbor-vollminlab-pull-externalsecret.yaml` verbatim, changing only `namespace: vollmint` and the `app` label to `vollmint`. (Target name stays `harbor-vollminlab-pull` — the chart's `imagePullSecrets` default references it.)
- [ ] **Step 5:** `ingress.yaml` — shlink-web pattern with vollmint values (LAN/Tailscale only; external-dns registers the A record automatically, and this host must never be added to the Cloudflare tunnel):
  ```yaml
  apiVersion: networking.k8s.io/v1
  kind: Ingress
  metadata:
    name: vollmint-ingress
    namespace: vollmint
    labels:
      app: vollmint
      env: production
      category: apps
    annotations:
      nginx.ingress.kubernetes.io/ssl-redirect: "true"
      nginx.ingress.kubernetes.io/auth-url: "http://authentik-proxy.authentik.svc.cluster.local:9000/outpost.goauthentik.io/auth/nginx"
      nginx.ingress.kubernetes.io/auth-signin: "https://authentik.vollminlab.com/outpost.goauthentik.io/start?rd=https://$http_host$escaped_request_uri"
      nginx.ingress.kubernetes.io/auth-response-headers: "Set-Cookie,X-authentik-username,X-authentik-groups,X-authentik-email,X-authentik-name,X-authentik-uid"
      nginx.ingress.kubernetes.io/proxy-buffer-size: "128k"
      nginx.ingress.kubernetes.io/auth-snippet: |
        proxy_set_header X-Forwarded-Host $http_host;
      shlink.vollminlab.com/slug: vollmint
  spec:
    ingressClassName: nginx
    tls:
      - hosts:
          - vollmint.vollminlab.com
        secretName: wildcard-tls
    rules:
      - host: vollmint.vollminlab.com
        http:
          paths:
            - path: /
              pathType: Prefix
              backend:
                service:
                  name: vollmint
                  port:
                    number: 8080
  ```
- [ ] **Step 6:** `vollmint/app/kustomization.yaml`:
  ```yaml
  apiVersion: kustomize.config.k8s.io/v1beta1
  kind: Kustomization
  resources:
    - helmrelease.yaml
    - configmap.yaml
    - vollmint-simplefin-externalsecret.yaml
    - harbor-vollminlab-pull-externalsecret.yaml
    - ingress.yaml
  ```

---

### Task 13: NetworkPolicies

**Files:**
- Create: `clusters/vollminlab-cluster/vollmint/networkpolicies/networkpolicy.yaml`
- Create: `clusters/vollminlab-cluster/vollmint/networkpolicies/kustomization.yaml` (if authentik's layout has one — mirror it)
- Modify: `.claude/rules/networkpolicy.md` (port table)

Mirror `authentik/networkpolicies/networkpolicy.yaml` shapes exactly (labels use `category: security`). Policies needed: `default-deny-all` (Ingress+Egress), `allow-dns`, `allow-intra-namespace` (covers serve/sync → vollmint-db:5432 and CNPG instance-to-instance replication), `allow-ingress-nginx` (ingress from `ingress-nginx` ns, TCP **8080** — the container port), `allow-cnpg-operator` (ingress from `cnpg-system`, TCP 5432 + 8000), `allow-monitoring-scrape` (ingress from `monitoring`, no port restriction — authentik shape), `allow-kube-api-egress` (TCP 6443 — CNPG instance manager needs the API server), `allow-minio-egress` (to `minio` ns, TCP 9000 — WAL archiving + base backups), `allow-external-egress` (TCP 443 — sync → SimpleFIN Bridge).

- [ ] **Step 1:** Write the nine policies by copying each corresponding authentik policy and adjusting namespace to `vollmint`, `app` label to `vollmint`, and the ingress-nginx port to 8080. Do not invent new shapes.
- [ ] **Step 2:** Pre-PR port verification (repo checklist): the port in `allow-ingress-nginx` must be the **container** port. vollmint listens on `:8080` (`LISTEN_ADDR` default) and the Service targets the named port `http` = 8080, so no remap exists — but confirm after rollout with `kubectl get pod -n vollmint -l app=vollmint -o jsonpath='{.items[0].spec.containers[*].ports}'`.
- [ ] **Step 3:** Add rows to the port table in `.claude/rules/networkpolicy.md`:
  ```
  | `vollmint` | `vollmint` | 8080 | API + SPA (ingress-nginx) | allow-ingress-nginx ingress |
  | `vollmint` | n/a (egress target) | 443 | SimpleFIN Bridge HTTPS (sync CronJob) | allow-external-egress egress |
  ```

---

### Task 14: Flux wiring + docs

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/vollmint-ocirepository.yaml`
- Create: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/vollmint-kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`
- Modify: `docs/cluster-reference.md`

- [ ] **Step 1:** `vollmint-ocirepository.yaml` (LRC pattern; tag pins the chart version published in Task 9):
  ```yaml
  apiVersion: source.toolkit.fluxcd.io/v1
  kind: OCIRepository
  metadata:
    name: vollmint-repo
    namespace: flux-system
    labels:
      app: vollmint
      env: production
      category: apps
  spec:
    interval: 1h
    url: oci://harbor.vollminlab.com/vollminlab/charts/vollmint
    ref:
      tag: "0.1.0"
    secretRef:
      name: harbor-vollminlab-pull
  ```
- [ ] **Step 2:** `vollmint-kustomization.yaml` (shlink pattern — same dependsOn):
  ```yaml
  apiVersion: kustomize.toolkit.fluxcd.io/v1
  kind: Kustomization
  metadata:
    name: vollmint
    namespace: flux-system
    labels:
      app: vollmint
      env: production
      category: apps
  spec:
    interval: 10m
    path: ./clusters/vollminlab-cluster/vollmint
    prune: true
    sourceRef:
      kind: GitRepository
      name: flux-system
    timeout: 10m
    dependsOn:
      - name: cnpg-system
      - name: external-secrets
  ```
- [ ] **Step 3:** Update **both** explicit indexes (missing either = app silently never deploys):
  - `repositories/kustomization.yaml`: add `- vollmint-ocirepository.yaml` in alphabetical position (between `vmware-exporter-*` and `volsync-*`; note `vm` < `voll` < `vols`).
  - `flux-kustomizations/kustomization.yaml`: add `- vollmint-kustomization.yaml` after `shlink-kustomization.yaml` (this index is dependency-grouped, not alphabetical — vollmint sits with the other CNPG+ESO-dependent apps).
  - Cross-check: `sourceRef`/`chartRef` names match (`vollmint-repo`), Kustomization `path` matches the directory.
- [ ] **Step 4:** `docs/cluster-reference.md` — add vollmint to: the Flux Kustomizations table, the Repository Sources table (OCIRepository `vollmint-repo` → Harbor charts), the ingress hostname list (`vollmint.vollminlab.com`), and the CNPG databases list (`vollmint-db`, 2×5Gi, backup 01:45). Search for the shlink rows in each section and mirror them.

---

### Task 15: Authentik application (tofu) + homepage tile

**Files:**
- Modify: `terraform/authentik/applications.tf`
- Modify: `clusters/vollminlab-cluster/homepage/homepage/app/configmap.yaml`

- [ ] **Step 1:** Add the Application resource in `applications.tf`, alphabetically after the `truenas` resource. No `protocol_provider` — forward-auth hosts rely on the domain-wide `vollminlab-forward-auth` provider and just need an Application entry (`provider_id=None`). No `import` block (resource is new):
  ```hcl
  resource "authentik_application" "vollmint" {
    name             = "Vollmint"
    slug             = "vollmint"
    meta_description = "Household budget tracker"
    meta_launch_url  = "https://vollmint.vollminlab.com"
    open_in_new_tab  = false
  }
  ```
  Run: `tofu -chdir=terraform/authentik fmt -check && tofu -chdir=terraform/authentik validate` (init first if needed) → PASS. Do **not** apply — the in-cluster tofu-controller applies on merge (`approvePlan: auto`).
- [ ] **Step 2:** Homepage tile in the **Tools** group (Personal is external-links-only), alphabetical slot after Shlink. The `app:` field becomes selector `app.kubernetes.io/name=vollmint`, which the chart emits on serve pods:
  ```yaml
              - Vollmint:
                  description: Household budget tracker
                  href: https://vollmint.vollminlab.com
                  icon: mdi-cash-multiple
                  namespace: vollmint
                  app: vollmint
  ```
  Check `settings.yaml`'s layout block in the same ConfigMap — if Tools has a fixed row/column count that the new tile overflows, adjust it in this same edit. No widget (custom app, no homepage integration); no new env vars, so `homepage-env-vars` is untouched.

---

### Task 16: Commit, push, open PR — STOP

**Files:** none (git)

- [ ] **Step 1:** Stage all Part B files **explicitly by name** (the vollmint/ tree, both flux-system index files + 2 new files, `docs/cluster-reference.md`, `.claude/rules/networkpolicy.md`, `terraform/authentik/applications.tf`, homepage `configmap.yaml`). Commit: `feat(vollmint): deploy vollmint — namespace, CNPG DB, HelmRelease, ingress, netpols, Authentik app, homepage tile`.
- [ ] **Step 2:** Push, open the PR (`gh pr create`). Body must state the merge gate: **"Do not merge until vollmint v0.1.0 image + chart are published in Harbor (vollmint repo PR merged and tagged)."** No test-plan checklist in the body. Watch CI — Kyverno CLI + manifest validation must go green.
- [ ] **Step 3:** **STOP — do not merge.** Merging requires Scott's explicit approval (house rule).

---

### Task 17: Post-merge rollout verification + backfill (after Scott merges the cluster PR)

**Files:** none (operations)

- [ ] **Step 1:** `flux reconcile source git flux-system` then watch: `flux get kustomizations -A | grep vollmint` and `flux get helmrelease vollmint -n vollmint` → Ready. ExternalSecrets: `kubectl get externalsecret -n vollmint` → both READY=True.
- [ ] **Step 2:** CNPG: `kubectl get cluster -n vollmint vollmint-db` → 2/2 healthy. Confirm the doc-verified credential path live (first cluster in this fleet using CNPG auto-generated creds): `kubectl get secret vollmint-db-app -n vollmint -o jsonpath='{.data}' | python3 -c "import json,sys; print(sorted(json.load(sys.stdin)))"` → keys include `uri`. Serve pod Running, migrations applied (pod logs).
- [ ] **Step 3:** Ingress path: `dig +short vollmint.vollminlab.com` → ingress VIP (external-dns). `curl -sI https://vollmint.vollminlab.com/healthz` → **302 to authentik is the expected unauthenticated response** (forward-auth intercepts every path; the unauthenticated `/healthz` is for pod probes, which bypass the ingress). Authentik app exists: akshell list-applications (verification only) → slug `vollmint` present with `provider_id=None`. Short URL: `curl -sI https://vollm.in/vollmint` → 302. Homepage tile renders with a green k8s badge.
- [ ] **Step 4:** Manual first sync: `kubectl create job --from=cronjob/vollmint-sync vollmint-sync-manual -n vollmint`, then `kubectl logs -n vollmint job/vollmint-sync-manual -f` → completes, accounts + transactions ingested. (This is the by-design manual-sync path; there is deliberately no API endpoint for it.)
- [ ] **Step 5:** Backups: next morning confirm `kubectl get backups.postgresql.cnpg.io -n vollmint` shows the 01:45 backup Completed, and the Velero daily-full includes the namespace automatically (opt-out model — vollmint must NOT be added to any exclusion list; no action needed, just verify it appears in the next backup's namespace list).
- [ ] **Step 6:** Venmo backfill (Scott-driven, via the UI): log in, upload the ~12 Venmo CSV exports (90-day chunks) through the import page, then seed categories and budget targets. Report completion state to Scott with anything that needs his hands (CSV exports from Venmo's site).

---

## Spec coverage cross-check

| Design-doc requirement | Covered by |
|---|---|
| Multi-stage Dockerfile, distroless, nonroot | Task 2 |
| CI: Go tests (Postgres) + web tests on PR | Task 3 |
| Helm chart, one image / two entrypoints | Tasks 4–6 |
| Tag-driven publish of image + chart to Harbor | Tasks 7, 9 |
| CNPG `vollmint-db`, 2×5Gi Longhorn, MinIO barman backups | Task 11 |
| Nightly CNPG backup slot | Task 11 (01:45) |
| `DATABASE_URL` wiring | Tasks 5–6 (CNPG `vollmint-db-app`/`uri` — deviation table) |
| SimpleFIN secret, sync-only | Task 12 (ES) + Task 6 (CronJob-only env) |
| Sync CronJob 06:10/18:10 UTC | Task 6 |
| Ingress + Authentik forward-auth + wildcard TLS | Task 12 |
| LAN/Tailscale only, never Cloudflare | Task 12 note; no tunnel change anywhere |
| Shlink slug `vollmint` | Task 12 (annotation) |
| Authentik Application entry | Task 15 (tofu — deviation table) |
| Homepage tile | Task 15 |
| NetworkPolicies (default-deny namespace) | Task 13 |
| Flux wiring (both indexes) | Task 14 |
| Docs updates | Tasks 13–14 |
| Manual sync via `kubectl create job` | Task 17 |
| Venmo CSV backfill (~12 exports) | Task 17 |
| Velero coverage | Task 17 (automatic, verify only) |
