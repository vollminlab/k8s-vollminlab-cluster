# Foundry VTT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Self-host Foundry Virtual Tabletop at `https://foundry.vollminlab.com`, gated by Authentik forward-auth, backed up via VolSync to Backblaze B2.

**Architecture:** A single-replica StatefulSet from the `charts.derwitt.dev` `foundryvtt` chart in a dedicated `foundry` namespace with default-deny NetworkPolicies. Exposed through ingress-nginx and the existing shared Cloudflare `nginx` tunnel (no new tunnel, no inbound firewall ports). Credentials come from 1Password via ESO. Backups use VolSync `copyMethod: Clone` so restic never walks the live LevelDB.

**Tech Stack:** Flux CD (HelmRelease + Kustomization), Helm chart `foundryvtt` 15.2.3 (appVersion 14.367.0, image `ghcr.io/felddy/foundryvtt`), External Secrets Operator + 1Password Connect, Kyverno, VolSync (restic → B2), OpenTofu (Authentik + Cloudflare).

---

## Context an implementer needs

**Why not the chart you'll find first.** `hugoprudente/foundry-vtt` is the top artifacthub hit. Its *installable* version is `12.343.0` published 2025-06-07 — two Foundry majors behind. Its git `main` is at 13.345 but was never packaged into the gh-pages index, so `helm repo add` cannot reach it. Do not use it.

**Why a StatefulSet matters.** The felddy image binds the Foundry license to the **container hostname**. A Deployment's pod name changes on every restart, which fails license verification each time. This chart sets `spec.hostname` explicitly from the release fullname, which is stable. It also hardcodes `replicas: 1`, which matches the license term that only one player-accessible instance may run.

**Why `nameOverride: foundry`.** The chart's fullname helper is `if contains $name .Release.Name → .Release.Name else "<release>-<chart>"`. With release `foundry` and chart name `foundryvtt`, `contains "foundryvtt" "foundry"` is false, so you would get `foundry-foundryvtt` everywhere. Setting `nameOverride: foundry` makes `$name` = `foundry`, `contains "foundry" "foundry"` is true, and every object is simply `foundry`. This also sets `app.kubernetes.io/name: foundry`, which the Homepage Kubernetes badge matches on.

**Why the `tls:` block is not optional.** In this chart, a non-empty `ingress.tls` is the *only* thing that turns on `FOUNDRY_PROXY_SSL=true` and `FOUNDRY_PROXY_PORT=443`. Without them Foundry generates `http://…:30000` invite links that do not work. The `wildcard-tls` secret does not exist in the `foundry` namespace and does not need to — ingress-nginx runs with `--default-ssl-certificate=cert-manager/wildcard-tls` and falls back to it.

**The NetworkPolicy port trap.** The Service is port 80; the container listens on **30000**. NetworkPolicy is evaluated post-DNAT at the pod, so the ingress rule must say `30000`. Writing `80` silently allows nothing.

**Egress 443 is required for the pod to start.** The felddy image downloads the Foundry distribution from foundryvtt.com at first boot and on every version change, and Foundry validates the license key against foundryvtt.com. Omit the egress rule and the pod never becomes Ready.

**Why VolSync `copyMethod: Clone` and not Velero FSB.** Foundry v11+ uses LevelDB. Foundry's own docs: *"never use a data sync service while Foundry VTT is running, you will permanently lose your work."* The danger is a file-by-file copy spanning wall-clock time, capturing `CURRENT`, `MANIFEST-*`, the WAL and the `.ldb` files at different points. `copyMethod: Clone` makes a Longhorn block-level clone first and restic walks *the clone*, which is frozen at creation — so the torn-file window does not exist. Foundry's own snapshot/backup feature was evaluated and rejected: it is UI-only, with no API, CLI, or hook to trigger it non-interactively.

**PVC sizing.** 10Gi on the default `longhorn` StorageClass (3 replicas = 30Gi cluster-wide). Measured 2026-08-21, schedulable headroom above Longhorn's 25% floor: worker01 61.8GiB, worker03 27.3GiB, worker04 36.7GiB, worker02 8.5GiB (the scheduler will skip worker02). Expand later with an online resize rather than provisioning speculatively.

**Ordering constraint.** The Authentik Application must exist **before** the auth-annotated Ingress is reconciled. Without it the outpost returns 400 and nginx converts it to 500. PR 1 (tofu) must merge and apply before PR 2 (manifests).

---

## Prerequisites (human, before Task 1)

- [ ] **Purchase a Foundry VTT license** at https://foundryvtt.com/purchase/ ($50 USD, one-time). Note the license key from https://foundryvtt.com/me/licenses.

---

## File structure

**Created:**

```
clusters/vollminlab-cluster/foundry/
  namespace.yaml                                       # ns + labels + PSA warn/audit
  kustomization.yaml                                   # aggregates networkpolicies/ + foundry/app/
  networkpolicies/
    kustomization.yaml
    networkpolicy.yaml                                 # 4 policies in one file
  foundry/app/
    helmrelease.yaml
    configmap.yaml                                     # Helm values
    foundry-credentials-externalsecret.yaml
    kustomization.yaml
  volsync/
    kustomization.yaml
    foundry-data-replicationsource.yaml
    volsync-foundry-data-restic-externalsecret.yaml
clusters/vollminlab-cluster/flux-system/repositories/foundry-helmrepository.yaml
clusters/vollminlab-cluster/flux-system/flux-kustomizations/foundry-kustomization.yaml
```

**Modified:**

```
clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml       # add foundry-helmrepository.yaml
clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml # add foundry-kustomization.yaml
clusters/vollminlab-cluster/homepage/homepage/app/configmap.yaml              # Tools group tile
README.md                                                                     # structure block + service table
docs/roadmap.md                                                               # move Foundry Deferred -> Completed
.claude/rules/networkpolicy.md                                                # container-port table row
.claude/rules/kyverno.md                                                      # gaming category row
terraform/authentik/applications.tf                                           # Application + policy binding
terraform/authentik/groups.tf                                                 # foundry_users group
terraform/cloudflare/dns.tf                                                   # foundry CNAME
terraform/cloudflare/tunnels.tf                                               # hostname on the nginx tunnel
```

The Ingress is rendered by the chart from `configmap.yaml` values, so there is no `ingress.yaml` file. This is a deliberate deviation from the usual layout: the chart is the only way to get `FOUNDRY_PROXY_SSL`/`FOUNDRY_PROXY_PORT` set, and those are driven by `ingress.tls`.

**Known accepted deviation:** the chart-rendered Ingress will not carry `app`/`env`/`category` labels, because the chart exposes no `ingress.labels` value. Kyverno's `require-standard-labels` only matches Deployment/StatefulSet/DaemonSet/Pod/Namespace, so this does not block admission. Do not add a postRenderer purely for these labels.

---

## PR 1 — Tofu: Authentik + Cloudflare

Branch: `feat/foundry-tofu`

### Task 1: Authentik access group

**Files:**
- Modify: `terraform/authentik/groups.tf`

- [ ] **Step 1: Add the group**

Insert alphabetically, after the `filebrowser_users` block and before `grafana_admins`:

```hcl
resource "authentik_group" "foundry_users" {
  name  = "Foundry Users"
  users = toset([authentik_user.vollmin.id, authentik_user.jvollmin.id, authentik_user.gkroner.id, authentik_user.jkvedaras.id, authentik_user.chavelock.id])
}
```

Adjust the user list to the actual players. Available users defined in `terraform/authentik/users.tf`: `vollmin`, `jvollmin`, `gkroner`, `jkvedaras`, `chavelock`, `rjutkiewicz`, `speterson`, `bhasslinger`. If a player is not in that list, add an `authentik_user` block for them in `users.tf` following the existing shape (username/name/email/is_active plus `lifecycle { ignore_changes = [password, groups] }`).

- [ ] **Step 2: Verify formatting**

Run: `cd terraform/authentik && tofu fmt -check groups.tf`
Expected: no output, exit 0.

### Task 2: Authentik Application and policy binding

**Files:**
- Modify: `terraform/authentik/applications.tf`

- [ ] **Step 1: Add the Application and binding**

Insert alphabetically, after the `filebrowser` block (and its `filebrowser_users` policy binding) and before `grafana`:

```hcl
resource "authentik_application" "foundry" {
  name             = "Foundry VTT"
  slug             = "foundry"
  meta_description = "Virtual tabletop for online tabletop RPG sessions"
  meta_launch_url  = "https://foundry.vollminlab.com"
  open_in_new_tab  = false
}

resource "authentik_policy_binding" "foundry_users" {
  target = authentik_application.foundry.uuid
  group  = authentik_group.foundry_users.id
  order  = 0
}
```

There is deliberately **no** `protocol_provider`. Foundry uses the domain-wide `vollminlab-forward-auth` `forward_domain` provider, exactly like `alertmanager`, `bazarr` and `filebrowser`. Do not create a `forward_single` ProxyProvider — per `.claude/rules/authentik-akshell.md` that causes an OAuth callback loop.

No `meta_icon` is set because `homarr-labs/dashboard-icons` has no Foundry icon (verified: `svg/foundryvtt.svg`, `svg/foundry-vtt.svg`, `svg/foundry.svg`, `png/foundryvtt.png` all return 404).

- [ ] **Step 2: Verify formatting**

Run: `cd terraform/authentik && tofu fmt -check applications.tf`
Expected: no output, exit 0.

### Task 3: Cloudflare DNS record

**Files:**
- Modify: `terraform/cloudflare/dns.tf`

- [ ] **Step 1: Add the CNAME**

In the "Cloudflare Tunnel CNAMEs" section, insert alphabetically after the `filebrowser` record and before `jellyfin`:

```hcl
resource "cloudflare_dns_record" "foundry" {
  zone_id = var.cloudflare_zone_id
  name    = "foundry.vollminlab.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.nginx.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}
```

This points at the **existing shared `nginx` tunnel**, the same one `filebrowser` uses. No new tunnel, no new cloudflared Deployment, no new ExternalSecret.

- [ ] **Step 2: Verify formatting**

Run: `cd terraform/cloudflare && tofu fmt -check dns.tf`
Expected: no output, exit 0.

### Task 4: Cloudflare tunnel ingress hostname

**Files:**
- Modify: `terraform/cloudflare/tunnels.tf`

- [ ] **Step 1: Add the hostname to the nginx tunnel config**

Replace the `ingress` list inside `resource "cloudflare_zero_trust_tunnel_cloudflared_config" "nginx"` with:

```hcl
    ingress = [
      {
        hostname = "filebrowser.vollminlab.com"
        service  = "http://ingress-nginx-controller.ingress-nginx.svc.cluster.local:80"
      },
      {
        hostname = "foundry.vollminlab.com"
        service  = "http://ingress-nginx-controller.ingress-nginx.svc.cluster.local:80"
      },
      {
        service = "http_status:404"
      },
    ]
```

The catch-all `http_status:404` entry **must remain last**.

- [ ] **Step 2: Verify formatting**

Run: `cd terraform/cloudflare && tofu fmt -check tunnels.tf`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add terraform/authentik/groups.tf terraform/authentik/applications.tf terraform/cloudflare/dns.tf terraform/cloudflare/tunnels.tf
git commit -m "feat(foundry): add Authentik application and Cloudflare DNS for Foundry VTT"
```

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin feat/foundry-tofu
gh pr create --title "feat(foundry): Authentik application and Cloudflare DNS for Foundry VTT" --body "$(cat <<'EOF'
Adds the Authentik Application, access group and policy binding for Foundry VTT, plus the Cloudflare DNS record and tunnel hostname.

Ships ahead of the cluster manifests on purpose: an auth-annotated Ingress whose host has no Authentik Application makes the outpost return 400, which nginx converts to 500.

Reuses the existing shared `nginx` tunnel rather than creating a dedicated one.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017Fuk2t1KKrrLikj2xghzn9
EOF
)"
```

- [ ] **Step 5: After merge, confirm the apply landed**

The `authentik-config` and `cloudflare-config` Terraform CRs are `approvePlan: auto` on a 10-minute interval, so no manual approval is needed.

```bash
kubectl get terraform -n tofu authentik-config cloudflare-config \
  -o custom-columns='NAME:.metadata.name,READY:.status.conditions[-1].status,MSG:.status.conditions[-1].message'
```
Expected: both `READY=True` with an applied message referencing the new commit.

Then confirm the Application exists:

```bash
AUTHENTIK_POD=$(kubectl get pods -n authentik -l app.kubernetes.io/name=authentik,app.kubernetes.io/component=server -o name | head -1 | cut -d/ -f2)
kubectl exec -n authentik $AUTHENTIK_POD -- ak shell -c "
from authentik.core.models import Application
a = Application.objects.filter(slug='foundry').first()
print('FOUND' if a else 'MISSING', a.name if a else '', a.provider_id if a else '')
"
```
Expected: `FOUND Foundry VTT None` — `provider_id` of `None` is correct for forward-auth.

---

## PR 2 — Cluster manifests

Branch: `feat/foundry-vtt`. **Do not open until PR 1 has merged and applied.**

### Task 5: HelmRepository

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/repositories/foundry-helmrepository.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`

- [ ] **Step 1: Create the HelmRepository**

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: foundry-repo
  namespace: flux-system
  labels:
    app: foundry
    env: production
    category: gaming
spec:
  url: https://charts.derwitt.dev
  interval: 5m
```

- [ ] **Step 2: Add to the repositories index**

In `clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml`, insert into `resources` between `external-secrets-helmrepository.yaml` and `goldilocks-helmrepository.yaml`:

```yaml
  - foundry-helmrepository.yaml
```

This list is explicit, not a glob. Flux silently ignores any file not listed.

- [ ] **Step 3: Verify the chart is reachable at the pinned version**

Run:
```bash
curl -s https://charts.derwitt.dev/index.yaml | grep -A2 "foundryvtt-15.2.3"
```
Expected: a `urls:` entry for `foundryvtt-15.2.3.tgz`. If 15.2.3 is gone, pick the newest published version and use it consistently in Task 7.

### Task 6: Namespace and NetworkPolicies

**Files:**
- Create: `clusters/vollminlab-cluster/foundry/namespace.yaml`
- Create: `clusters/vollminlab-cluster/foundry/kustomization.yaml`
- Create: `clusters/vollminlab-cluster/foundry/networkpolicies/kustomization.yaml`
- Create: `clusters/vollminlab-cluster/foundry/networkpolicies/networkpolicy.yaml`

- [ ] **Step 1: Create the namespace**

`clusters/vollminlab-cluster/foundry/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: foundry
  labels:
    app: foundry
    env: production
    category: gaming
    goldilocks.fairwinds.com/enabled: "true"
    pod-security.kubernetes.io/warn: restricted
    pod-security.kubernetes.io/audit: restricted
```

`warn`/`audit` without `enforce` matches every other application namespace here (`vollmint`, `mediastack`). The Kyverno `inject-namespace-labels` mutate copies `app`/`env`/`category` from this namespace onto the StatefulSet and its pod template at admission, which is what satisfies `require-standard-labels` — the chart does not set those labels itself.

- [ ] **Step 2: Create the namespace kustomization**

`clusters/vollminlab-cluster/foundry/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - foundry/app
  - networkpolicies
```

Namespace-level kustomizations in this repo carry no `metadata` block (see `vollmint/kustomization.yaml`); app-level ones do.

- [ ] **Step 3: Create the networkpolicies kustomization**

`clusters/vollminlab-cluster/foundry/networkpolicies/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - networkpolicy.yaml
```

- [ ] **Step 4: Create the NetworkPolicies**

`clusters/vollminlab-cluster/foundry/networkpolicies/networkpolicy.yaml`:

```yaml
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: foundry
  labels:
    app: foundry
    env: production
    category: security
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns
  namespace: foundry
  labels:
    app: foundry
    env: production
    category: security
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
        - podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
---
# Allow ingress-nginx to reach Foundry. NetworkPolicy is evaluated post-DNAT at
# the pod, so the CONTAINER port applies: the Service is 80, the container
# listens on 30000. Using 80 here would silently allow nothing.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-ingress-nginx
  namespace: foundry
  labels:
    app: foundry
    env: production
    category: security
spec:
  podSelector: {}
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ingress-nginx
      ports:
        - protocol: TCP
          port: 30000
---
# Required for the pod to START, not just for convenience: the felddy image
# downloads the Foundry distribution from foundryvtt.com on first boot and on
# every version change, and Foundry validates the license key against
# foundryvtt.com. Also carries the VolSync restic mover's traffic to Backblaze B2.
#
# RFC1918 is excluded so a compromised third-party Foundry module cannot reach
# anything on the internal network. Modelled on dmz/masters-league.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-https-egress
  namespace: foundry
  labels:
    app: foundry
    env: production
    category: security
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - protocol: TCP
          port: 443
```

No monitoring-scrape rule: Foundry exposes no Prometheus metrics endpoint. No MinIO egress rule: VolSync here targets Backblaze B2 over 443, not MinIO (verified against `harbor/volsync/` and `mediastack/volsync/` — both use `b2_key_id`/`b2_key`). Consequently MinIO's `allow-cnpg-backups` peer list needs **no** edit for this project.

### Task 7: HelmRelease and values

**Files:**
- Create: `clusters/vollminlab-cluster/foundry/foundry/app/helmrelease.yaml`
- Create: `clusters/vollminlab-cluster/foundry/foundry/app/configmap.yaml`
- Create: `clusters/vollminlab-cluster/foundry/foundry/app/kustomization.yaml`

- [ ] **Step 1: Create the HelmRelease**

`clusters/vollminlab-cluster/foundry/foundry/app/helmrelease.yaml`:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: foundry
  namespace: foundry
  labels:
    app: foundry
    env: production
    category: gaming
spec:
  interval: 5m
  timeout: 15m
  chart:
    spec:
      chart: foundryvtt
      version: "15.2.3"
      sourceRef:
        kind: HelmRepository
        name: foundry-repo
        namespace: flux-system
  valuesFrom:
    - kind: ConfigMap
      name: foundry-values
      valuesKey: values.yaml
```

`timeout: 15m` rather than the usual 10m: first boot downloads the ~1 GB Foundry distribution before the pod goes Ready.

- [ ] **Step 2: Create the values ConfigMap**

`clusters/vollminlab-cluster/foundry/foundry/app/configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: foundry-values
  namespace: foundry
  labels:
    app: foundry
    env: production
    category: gaming
data:
  values.yaml: |
    # nameOverride makes the chart's fullname helper resolve to a bare "foundry"
    # instead of "foundry-foundryvtt", and sets app.kubernetes.io/name=foundry
    # which the Homepage Kubernetes badge matches on.
    nameOverride: foundry

    image:
      registry: ghcr.io
      repository: felddy/foundryvtt
      pullPolicy: IfNotPresent

    config:
      # All three point at the single Secret materialized by the ExternalSecret.
      foundryAuthSecret:
        name: foundry-credentials
        usernameKey: username
        passwordKey: password
      adminKeySecret:
        name: foundry-credentials
        key: admin-key
      licenseKeySecret:
        name: foundry-credentials
        key: license-key
      # false = the container rewrites options.json from env on every start, so
      # the manifest stays the source of truth. Changing settings in Foundry's
      # admin UI will not survive a restart, which is intended.
      preserveConfig: false
      enableTelemetry: false
      minifyStaticFiles: true
      cssTheme: dark
      downloadRetries: 5

    # 10Gi is the chart default and deliberate: x3 Longhorn replicas = 30Gi
    # cluster-wide. 20Gi would drop worker03's schedulable headroom from
    # 27.3GiB to ~7.3GiB and constrain the next PVC anyone provisions. Foundry
    # itself needs ~1Gi; the rest is uploaded maps and audio. storage.md:
    # "Start conservative. Expand later." Longhorn resizes online, no downtime.
    storage:
      size: 10Gi
      className: longhorn

    service:
      type: ClusterIP
      port: 80

    ingress:
      enabled: true
      className: nginx
      annotations:
        nginx.ingress.kubernetes.io/ssl-redirect: "false"
        # 0 = unlimited, so large map/audio uploads work from the LAN. Cloudflare
        # still caps uploads THROUGH THE TUNNEL at 100 MB and a tunnel hostname
        # cannot be un-proxied, so upload big assets on the LAN (Pi-hole points
        # LAN clients at the ingress VIP, bypassing the tunnel).
        nginx.ingress.kubernetes.io/proxy-body-size: "0"
        nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
        nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
        nginx.ingress.kubernetes.io/proxy-buffering: "off"
        nginx.ingress.kubernetes.io/proxy-request-buffering: "off"
        nginx.ingress.kubernetes.io/proxy-buffer-size: "128k"
        nginx.ingress.kubernetes.io/auth-url: "http://authentik-proxy.authentik.svc.cluster.local:9000/outpost.goauthentik.io/auth/nginx"
        nginx.ingress.kubernetes.io/auth-signin: "https://authentik.vollminlab.com/outpost.goauthentik.io/start?rd=https://$http_host$escaped_request_uri"
        nginx.ingress.kubernetes.io/auth-response-headers: "Set-Cookie,X-authentik-username,X-authentik-groups,X-authentik-email,X-authentik-name,X-authentik-uid"
        nginx.ingress.kubernetes.io/auth-snippet: |
          proxy_set_header X-Forwarded-Host $http_host;
        shlink.vollminlab.com/slug: foundry
      hosts:
        - host: foundry.vollminlab.com
          paths:
            - path: /
              pathType: Prefix
      # A NON-EMPTY tls list is what makes the chart emit FOUNDRY_PROXY_SSL=true
      # and FOUNDRY_PROXY_PORT=443. Without it Foundry hands out broken
      # http://...:30000 invite links. The wildcard-tls Secret does not exist in
      # this namespace and does not need to: ingress-nginx runs with
      # --default-ssl-certificate=cert-manager/wildcard-tls.
      tls:
        - hosts:
            - foundry.vollminlab.com
          secretName: wildcard-tls

    # Kyverno require-resources: requests for cpu+memory and a memory limit are
    # mandatory. A CPU limit is deliberately omitted per .claude/rules/kyverno.md.
    resources:
      requests:
        cpu: 500m
        memory: 1Gi
      limits:
        memory: 4Gi

    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: false
      runAsNonRoot: true
      runAsUser: 1000
      runAsGroup: 1000
      seccompProfile:
        type: RuntimeDefault
      capabilities:
        drop:
          - ALL

    podLabels:
      app: foundry
      env: production
      category: gaming

    podAnnotations:
      # Keep Velero's file-system backup away from the live LevelDB. Foundry's
      # docs are explicit that copying its database files while the server runs
      # can corrupt them. The durable copy is the VolSync ReplicationSource,
      # which restics a frozen Longhorn CLONE instead of the live volume.
      backup.velero.io/backup-volumes-excludes: data

    # The chart default is 18 x 10s = 3 minutes, which is not enough for the
    # first-boot distribution download. 60 x 10s = 10 minutes.
    startupProbe:
      httpGet:
        path: /
        port: http
        scheme: HTTP
      failureThreshold: 60
      periodSeconds: 10
      timeoutSeconds: 5
```

- [ ] **Step 3: Create the app kustomization**

`clusters/vollminlab-cluster/foundry/foundry/app/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
metadata:
  name: foundry-app
resources:
  - foundry-credentials-externalsecret.yaml
  - configmap.yaml
  - helmrelease.yaml
```

### Task 8: 1Password item and ExternalSecret

**Files:**
- Create: `clusters/vollminlab-cluster/foundry/foundry/app/foundry-credentials-externalsecret.yaml`

- [ ] **Step 1: Create the 1Password item**

Create a Login item named exactly **`Foundry VTT`** in the **Homelab** vault, tagged `Homelab`, with these fields:

| Field label | Value |
|---|---|
| `username` | your foundryvtt.com account email |
| `password` | your foundryvtt.com account password |
| `admin_key` | a freshly generated strong password — this is Foundry's `/setup` admin key |
| `license_key` | the license key from https://foundryvtt.com/me/licenses |

Add to the item's notes: `Referenced by ExternalSecret — do not rename fields`.

Generate the admin key without it ever touching the shell history or this transcript:

```bash
op item create --category=login --vault=Homelab --title="Foundry VTT" \
  --tags=Homelab \
  username="CHANGEME@example.com" \
  password="CHANGEME" \
  admin_key="$(openssl rand -base64 24)" \
  license_key="CHANGEME"
```

Then edit the three `CHANGEME` values in the 1Password UI or with `op item edit`. Do **not** paste real credentials into a chat transcript or a file.

- [ ] **Step 2: Verify the item and field labels resolve**

Run:
```bash
op item get "Foundry VTT" --vault Homelab --format json | python3 -c "
import json,sys
d=json.load(sys.stdin)
labels={f.get('label') for f in d['fields'] if f.get('label')}
need={'username','password','admin_key','license_key'}
print('MISSING:', need-labels if need-labels else 'none')
"
```
Expected: `MISSING: none`. A mismatch here is the single most common cause of `SecretSyncedError` later.

- [ ] **Step 3: Create the ExternalSecret**

`clusters/vollminlab-cluster/foundry/foundry/app/foundry-credentials-externalsecret.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: foundry-credentials
  namespace: foundry
  labels:
    app: foundry
    env: production
    category: gaming
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: onepassword-cluster-store
    kind: ClusterSecretStore
  target:
    name: foundry-credentials
    creationPolicy: Owner
  data:
    - secretKey: username
      remoteRef:
        key: "Foundry VTT"
        property: username
    - secretKey: password
      remoteRef:
        key: "Foundry VTT"
        property: password
    - secretKey: admin-key
      remoteRef:
        key: "Foundry VTT"
        property: admin_key
    - secretKey: license-key
      remoteRef:
        key: "Foundry VTT"
        property: license_key
```

The filename base equals `metadata.name` exactly, as `.claude/rules/secrets.md` requires.

### Task 9: Flux Kustomization CR

**Files:**
- Create: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/foundry-kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`

- [ ] **Step 1: Create the Kustomization CR**

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: foundry
  namespace: flux-system
  labels:
    app: foundry
    env: production
    category: gaming
spec:
  interval: 10m
  path: ./clusters/vollminlab-cluster/foundry
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
  timeout: 10m
  dependsOn:
    - name: external-secrets
```

`dependsOn: external-secrets` because the HelmRelease cannot start without the ESO-materialized Secret.

- [ ] **Step 2: Add to the flux-kustomizations index**

In `clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml`, add to `resources` immediately after `- vollmint-kustomization.yaml`:

```yaml
  - foundry-kustomization.yaml
```

This index is ordered roughly by dependency, not alphabetically. Placing it near the other application namespaces is correct.

### Task 10: Homepage tile

**Files:**
- Modify: `clusters/vollminlab-cluster/homepage/homepage/app/configmap.yaml`

- [ ] **Step 1: Add the tile to the Tools group**

Immediately after the `- Vollmint:` block (which ends with `app: vollmint`) and before `- Vollminlab Org:`, insert:

```yaml
            - Foundry VTT:
                description: Virtual tabletop for tabletop RPG sessions
                href: https://foundry.vollminlab.com
                icon: mdi-dice-d20
                namespace: foundry
                app: foundry
```

`mdi-dice-d20` because `homarr-labs/dashboard-icons` has no Foundry icon; `mdi-`/`si-` icons are already used elsewhere in this file. `app: foundry` matches the `app.kubernetes.io/name` label the chart sets via `nameOverride`, which is what drives the Kubernetes status badge.

The Tools group is correct rather than Personal: Personal holds external bookmarks only, while Tools holds cluster-hosted apps carrying `namespace`/`app` badges (Vollmint is the precedent).

### Task 11: README, roadmap and rule-file updates

**Files:**
- Modify: `README.md`
- Modify: `docs/roadmap.md`
- Modify: `.claude/rules/networkpolicy.md`
- Modify: `.claude/rules/kyverno.md`

- [ ] **Step 1: Add to the structure block**

Insert between the `external-secrets/` and `goldilocks/` lines:

```
  foundry/                              # Foundry VTT virtual tabletop
```

- [ ] **Step 2: Add to the service table**

After the `| masters-league | dmz | ... |` row:

```
| Foundry VTT | foundry | Virtual tabletop (Authentik-gated, tunnel-exposed) |
```

- [ ] **Step 3: Verify the structure check passes**

Run: `./scripts/check-readme-structure.sh`
Expected: `✅ README structure block matches all 34 namespace directories` (34, up from 33). This check is CI-enforced and will fail the PR if the block is not updated.

- [ ] **Step 4: Add the container-port row to the NetworkPolicy reference table**

`.claude/rules/networkpolicy.md` declares its namespace/container-port table "the source of truth" and requires a row for every new port-restricted policy. Add these two rows to that table, after the `vollmint` rows:

```
| `foundry` | `foundryvtt` | 30000 | Foundry VTT HTTP + socket.io (svc 80→named port `http`→30000) | allow-ingress-nginx ingress (from ingress-nginx) |
| `foundry` | n/a (egress target) | 443 | foundryvtt.com distribution download + licence check, and the VolSync restic mover to B2 | allow-https-egress egress (RFC1918 excluded) |
```

- [ ] **Step 5: Correct the stale `gaming` category description**

`.claude/rules/kyverno.md` currently reads `| `gaming` | Minecraft (dmz namespace only) |`. That parenthetical was already inaccurate before this change — Minecraft's HelmRepository carries `category: gaming` while living in `flux-system`. Replace the row with:

```
| `gaming`        | Minecraft (dmz), Foundry VTT (foundry)                         |
```

Kyverno validates `category: "?*"` — any non-empty string — so this table is documentation, not enforcement. Correcting it keeps it from misleading the next reader into thinking `gaming` is dmz-scoped.

- [ ] **Step 6: Move Foundry out of the roadmap's Deferred table**

`docs/roadmap.md` line ~449 currently has this row under "## Deferred / Under Evaluation":

```
| Foundry VTT | Self-hosted tabletop game server (`felddy/foundryvtt` image). Very feasible: single stateful web app, ~5Gi PVC, no database, handles its own player auth. Add to `foundry` namespace with `category: apps`. Sequence after Cilium migration so it lands on the final ingress stack. |
```

**Delete that row entirely** and add a row to the "## Completed" table instead:

```
| Foundry VTT | Deployed to `foundry` namespace, `category: gaming`, 10Gi Longhorn PVC. Authentik forward-auth (domain-wide provider, blank per-world Foundry passwords). Exposed via the shared `nginx` Cloudflare tunnel. Backed up by VolSync clone-based restic to B2. |
```

Three details in the old row were wrong or superseded and must not survive: `category: apps` (the deployment uses `gaming`, matching Minecraft), `~5Gi PVC` (10Gi), and "handles its own player auth" (it is behind Authentik). The "sequence after Cilium migration" note is deliberately dropped — the chart ships a native `HTTPRoute` template, so the Phase 8 migration is a values change rather than a rewrite, and Phase 8 migrates all 34 ingresses regardless. Do not leave a stale Deferred row alongside a live deployment.

### Task 12: Validate and commit

- [ ] **Step 1: Validate the Helm values**

Run:
```bash
python3 scripts/check-helm-values.py clusters/vollminlab-cluster/foundry/foundry/app/configmap.yaml clusters/vollminlab-cluster/foundry/foundry/app/helmrelease.yaml
```
Expected: either a pass, or `no HelmRelease affected` / a note that the chart ships no `values.schema.json`. Any *schema violation* must be fixed before proceeding.

- [ ] **Step 2: Render the chart locally to confirm the values produce what is expected**

```bash
helm repo add foundry-tmp https://charts.derwitt.dev
helm repo update foundry-tmp
python3 -c "
import yaml,sys
d=yaml.safe_load(open('clusters/vollminlab-cluster/foundry/foundry/app/configmap.yaml'))
open('/tmp/foundry-values.yaml','w').write(d['data']['values.yaml'])
"
helm template foundry foundry-tmp/foundryvtt --version 15.2.3 -n foundry -f /tmp/foundry-values.yaml > /tmp/foundry-rendered.yaml
grep -E "^  name:|hostname:|containerPort:|FOUNDRY_PROXY_SSL|FOUNDRY_PROXY_PORT|FOUNDRY_HOSTNAME" -A1 /tmp/foundry-rendered.yaml | head -40
```

Expected, all four of these must hold:
- object names are `foundry`, not `foundry-foundryvtt`
- `hostname: foundry` appears in the pod spec
- `containerPort: 30000`
- `FOUNDRY_PROXY_SSL` = `"true"` and `FOUNDRY_PROXY_PORT` = `"443"` are present

If `FOUNDRY_PROXY_SSL` is absent, the `ingress.tls` list is empty or malformed — fix before continuing, because invite links will be broken.

```bash
helm repo remove foundry-tmp
```

- [ ] **Step 3: Commit**

```bash
git add clusters/vollminlab-cluster/foundry \
        clusters/vollminlab-cluster/flux-system/repositories/foundry-helmrepository.yaml \
        clusters/vollminlab-cluster/flux-system/repositories/kustomization.yaml \
        clusters/vollminlab-cluster/flux-system/flux-kustomizations/foundry-kustomization.yaml \
        clusters/vollminlab-cluster/flux-system/flux-kustomizations/kustomization.yaml \
        clusters/vollminlab-cluster/homepage/homepage/app/configmap.yaml \
        README.md docs/roadmap.md .claude/rules/networkpolicy.md .claude/rules/kyverno.md
git commit -m "feat(foundry): deploy Foundry VTT — namespace, HelmRelease, netpols, Authentik-gated ingress"
```

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin feat/foundry-vtt
gh pr create --title "feat(foundry): deploy Foundry VTT" --body "$(cat <<'EOF'
Deploys Foundry VTT at https://foundry.vollminlab.com in a dedicated `foundry` namespace.

- Chart `foundryvtt` 15.2.3 from https://charts.derwitt.dev (appVersion 14.367.0). The artifacthub top hit (`hugoprudente/foundry-vtt`) is two Foundry majors behind and was rejected.
- StatefulSet with a stable `spec.hostname` — the felddy image binds the Foundry licence to the container hostname, so a Deployment would fail licence verification on every restart.
- Default-deny NetworkPolicies. The ingress rule uses container port **30000**, not the Service's 80, because policy is evaluated post-DNAT. Egress 443 is required for the pod to start at all: the image downloads the Foundry distribution from foundryvtt.com and validates the licence there.
- Authentik forward-auth via the domain-wide `vollminlab-forward-auth` provider. The Application shipped in the preceding tofu PR.
- Exposed through the existing shared `nginx` Cloudflare tunnel — no new tunnel, no inbound firewall ports.

Backups follow in a separate PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017Fuk2t1KKrrLikj2xghzn9
EOF
)"
```

### Task 13: Post-merge verification

- [ ] **Step 1: Confirm Flux reconciled**

```bash
flux reconcile kustomization foundry --with-source
flux get helmrelease -n foundry
```
Expected: `foundry` `READY=True`.

- [ ] **Step 2: Confirm the Secret materialized**

```bash
kubectl get externalsecret -n foundry foundry-credentials
kubectl get secret -n foundry foundry-credentials -o jsonpath='{.data}' | python3 -c "import json,sys; print(sorted(json.load(sys.stdin).keys()))"
```
Expected: `READY=True` / `SecretSynced`, and `['admin-key', 'license-key', 'password', 'username']`.

- [ ] **Step 3: Confirm the pod is Running and got the license**

```bash
kubectl get pods -n foundry -o wide
kubectl logs -n foundry foundry-0 -c foundryvtt --tail=40
```
Expected: `foundry-0` `1/1 Running`. The container is named `foundryvtt` (from `.Chart.Name`), not `foundry`. First boot takes several minutes while the distribution downloads.

- [ ] **Step 4: Confirm the NetworkPolicy port is right**

```bash
kubectl get pod -n foundry foundry-0 -o jsonpath='{.spec.containers[*].ports}' | python3 -m json.tool
```
Expected: `containerPort: 30000`. If this is anything else, fix `allow-ingress-nginx` before debugging further.

- [ ] **Step 5: Confirm end-to-end through the tunnel**

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://foundry.vollminlab.com/
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller --tail=20 | grep foundry
```
Expected: a `302` to Authentik (not `500`). A `500` means the Authentik Application is missing — recheck PR 1 Step 5.

- [ ] **Step 6: Confirm the short link was created**

```bash
kubectl logs -n shlink deployment/shlink-ingress-controller --tail=20 | grep foundry
```
Expected: a line showing `vollm.in/foundry` was created from the `shlink.vollminlab.com/slug` annotation.

---

## PR 3 — VolSync backup

Branch: `feat/foundry-backup`. Open after PR 2 has merged and `foundry-0` is Running, because the ReplicationSource references a PVC that must already exist.

### Task 14: Restic repository credentials

**Files:**
- Create: `clusters/vollminlab-cluster/foundry/volsync/volsync-foundry-data-restic-externalsecret.yaml`

- [ ] **Step 1: Add a restic password field to the shared 1Password item**

The existing Homelab-vault item **`Volsync Restic Credentials`** holds the shared B2 fields (`b2_key_id`, `b2_key`, `endpoint`, `bucket`) plus one password field per protected PVC. Add a new field labelled `foundry-data`:

```bash
op item edit "Volsync Restic Credentials" --vault Homelab \
  "foundry-data[password]=$(openssl rand -base64 32)"
```

- [ ] **Step 2: Verify the field exists**

```bash
op item get "Volsync Restic Credentials" --vault Homelab --format json | python3 -c "
import json,sys
d=json.load(sys.stdin)
labels={f.get('label') for f in d['fields'] if f.get('label')}
print('foundry-data present:', 'foundry-data' in labels)
print('shared fields present:', {'b2_key_id','b2_key','endpoint','bucket'} <= labels)
"
```
Expected: both `True`.

- [ ] **Step 3: Create the ExternalSecret**

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: volsync-foundry-data-restic
  namespace: foundry
  labels:
    app: foundry
    env: production
    category: gaming
spec:
  refreshInterval: "0"
  secretStoreRef:
    name: onepassword-cluster-store
    kind: ClusterSecretStore
  target:
    name: volsync-foundry-data-restic
    creationPolicy: Owner
    template:
      engineVersion: v2
      data:
        AWS_ACCESS_KEY_ID: "{{ .b2_key_id }}"
        AWS_SECRET_ACCESS_KEY: "{{ .b2_key }}"
        RESTIC_PASSWORD: '{{ index . "foundry-data" }}'
        RESTIC_REPOSITORY: "s3:{{ .endpoint }}/{{ .bucket }}/foundry/foundry-data"
  dataFrom:
    - extract:
        key: "Volsync Restic Credentials"
```

`refreshInterval: "0"` matches every other VolSync secret here — the restic password must never rotate underneath an existing repository.

### Task 15: ReplicationSource

**Files:**
- Create: `clusters/vollminlab-cluster/foundry/volsync/foundry-data-replicationsource.yaml`
- Create: `clusters/vollminlab-cluster/foundry/volsync/kustomization.yaml`
- Modify: `clusters/vollminlab-cluster/foundry/kustomization.yaml`

- [ ] **Step 1: Confirm the source PVC name**

```bash
kubectl get pvc -n foundry
```
Expected: `data-foundry-0` (the chart's `volumeClaimTemplates` entry is named `data`; the StatefulSet is `foundry`). Use whatever this prints in the next step.

- [ ] **Step 2: Create the ReplicationSource**

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationSource
metadata:
  name: foundry-data-restic
  namespace: foundry
spec:
  sourcePVC: data-foundry-0
  trigger:
    schedule: "10 2 * * *"
  restic:
    pruneIntervalDays: 7
    repository: volsync-foundry-data-restic
    retain:
      daily: 7
      weekly: 4
      monthly: 3
    copyMethod: Clone
    storageClassName: longhorn-r1
    cacheCapacity: 1Gi
    cacheStorageClassName: longhorn
```

`copyMethod: Clone` is the load-bearing choice. Restic walks a Longhorn block-level clone that is frozen at creation, never the live volume — which is what makes this safe against Foundry's LevelDB. Do not change it to `Direct`.

`storageClassName: longhorn-r1` gives the transient clone a single replica, matching every other ReplicationSource here.

`02:10` UTC is the first free ten-minute slot; `00:00`–`02:00` are taken and Velero's `daily-full` starts at `03:00`.

- [ ] **Step 3: Create the volsync kustomization**

`clusters/vollminlab-cluster/foundry/volsync/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - foundry-data-replicationsource.yaml
  - volsync-foundry-data-restic-externalsecret.yaml
```

- [ ] **Step 4: Wire it into the namespace kustomization**

In `clusters/vollminlab-cluster/foundry/kustomization.yaml`, add `- volsync` to `resources`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - foundry/app
  - networkpolicies
  - volsync
```

- [ ] **Step 5: Commit, push, open the PR**

```bash
git add clusters/vollminlab-cluster/foundry/volsync clusters/vollminlab-cluster/foundry/kustomization.yaml
git commit -m "feat(foundry): back up Foundry data with VolSync clone-based restic to B2"
git push -u origin feat/foundry-backup
gh pr create --title "feat(foundry): VolSync backup for Foundry data" --body "$(cat <<'EOF'
Adds a nightly VolSync restic backup of the Foundry data volume to Backblaze B2.

`copyMethod: Clone` is deliberate. Foundry v11+ stores worlds in LevelDB and its own docs warn that copying those files while the server runs can corrupt them. A clone is frozen at creation, so restic never walks the live volume and the torn-file window does not exist. Foundry's built-in snapshot feature was evaluated and rejected: it is UI-only with no API, CLI or hook to trigger it non-interactively.

Velero FSB is kept away from the same volume by the `backup.velero.io/backup-volumes-excludes: data` pod annotation set in the previous PR.

Runs at 02:10 UTC — the first free slot before Velero's 03:00 daily-full.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017Fuk2t1KKrrLikj2xghzn9
EOF
)"
```

### Task 16: Verify the backup actually produced data

- [ ] **Step 1: Trigger and watch the first sync**

```bash
kubectl get replicationsource -n foundry foundry-data-restic \
  -o custom-columns='NAME:.metadata.name,LASTSYNC:.status.lastSyncTime,DURATION:.status.lastSyncDuration'
```

If `lastSyncTime` is empty, wait for 02:10 UTC or force it:
```bash
kubectl annotate replicationsource -n foundry foundry-data-restic \
  volsync.backube/trigger="manual-$(date +%s)" --overwrite
```

- [ ] **Step 2: Confirm the mover pod ran and exited cleanly**

```bash
kubectl get pods -n foundry | grep volsync
kubectl logs -n foundry -l app.kubernetes.io/created-by=volsync --tail=30
```
Expected: a completed mover pod whose log shows `snapshot ... saved`. A hang here with no network error usually means the `allow-https-egress` policy is missing or wrong.

- [ ] **Step 3: Confirm a non-empty snapshot exists — do not trust status alone**

```bash
kubectl get replicationsource -n foundry foundry-data-restic -o yaml | grep -A5 "status:"
```
Expected: a populated `lastSyncTime` and a `lastSyncDuration` of more than a couple of seconds. A sub-second duration means it backed up nothing.

Per `.claude/rules/velero.md`, a backup that matched nothing still reports success — verify the artifact, not the status.

---

## Post-deployment: Foundry-side setup (manual, one-off)

These cannot be automated — Foundry has no admin API.

- [ ] **Step 1: Log in to Foundry**

Browse to https://foundry.vollminlab.com. Authentik will challenge first. Then Foundry's `/setup` page asks for the admin key — that is the `admin_key` field from the `Foundry VTT` 1Password item.

- [ ] **Step 2: Create a world and its player users**

In the world, create one Foundry user per player. **Leave every player password blank.** Foundry supports this ("Newly created users do not start with a password"), and it is what makes the login feel like single sign-on: Authentik authenticates, then the player just clicks their name.

Understand the resulting model: anyone in the `Foundry Users` Authentik group can join as any character, because the per-world passwords are blank. That is the accepted trade for a stable friend group. If that ever needs locking down, the upgrade path is `MaienM/foundry-vtt-header-auth` — see the "Deferred" section.

- [ ] **Step 3: Sanity-check an invite link**

Copy the invitation link from Setup → world → Invitations and confirm it is `https://foundry.vollminlab.com/...`, not `http://...:30000`. If it is the latter, `FOUNDRY_PROXY_SSL`/`FOUNDRY_PROXY_PORT` did not get set — recheck `ingress.tls` in the values.

---

## Deferred / explicitly not doing

- **True OIDC "Sign in with Authentik"** — impossible. Foundry has no native OIDC/OAuth2/SAML; the core request has been open since 2020 and the maintainer has declined it. Foundry has no global user accounts for an OIDC subject to map onto.
- **`MaienM/foundry-vtt-header-auth`** — the only real path to zero-click SSO. Deferred because it is applied via `CONTAINER_PATCHES`, which this chart does not expose (no `extraEnv`/`envFrom`), so it needs a `postRenderers` patch; and because it pins Foundry to specific builds while Renovate auto-bumps the image through the chart's `appVersion`. Adopting it requires an `allowedVersions` gate in `renovate.json5`, the same treatment as the velero-plugin-for-aws pin. It also *replaces* password auth entirely, so a break has no fallback.
- **MinIO as Foundry's S3 asset backend** — rejected. Foundry requires the bucket be publicly readable by design, which contradicts the least-privilege rule.
- **A dedicated Cloudflare tunnel** — unnecessary. Foundry is a browser app reached over HTTPS, so it reuses the shared `nginx` tunnel like filebrowser.
- **The `dmz` namespace** — rejected. The DMZ's model is raw TCP through the HAProxy VMs, correct for Minecraft but wrong for a web app. It would also force `longhorn-dmz`'s 2-replica volumes.
- **Prometheus scrape rule** — Foundry exposes no metrics endpoint.

## Known operational limits to remember

- **Cloudflare caps uploads through the tunnel at 100 MB** and a tunnel hostname cannot be un-proxied. Battlemaps (10–20 MB) are fine; video map loops and long ambient audio are not. Upload large assets from the LAN, where Pi-hole points clients at the ingress VIP and bypasses the tunnel entirely. Downloads are not capped.
- **Cloudflare's CDN terms** reserve the right to act on serving "a disproportionate percentage of pictures, audio files, or other large files". A soft risk at homelab scale, but real on paper.
- **Authentik sessions last 30 days** with no IP or GeoIP binding (`terraform/authentik/stages.tf`), so the auth-proxy idle-timeout failure that drops players mid-session in other people's setups does not apply here. Do not shorten `session_duration` without considering this.
- **Foundry's WebSocket** survives fine — socket.io pings roughly every 25s, inside Cloudflare's 100s idle timeout.
