# Harbor Docker Hub Pull-Through Cache — Design

**Created:** 2026-05-30
**Status:** Draft — ready to implement

## Problem statement

The cluster pulls many images from Docker Hub (`docker.io`) **unauthenticated**. Docker Hub's anonymous limit is ~100 pulls / 6h / source IP, and the whole cluster shares one egress IP. Any event that reschedules many pods at once exhausts the limit → `ImagePullBackOff` storms.

This is not hypothetical. During the **2026-05-30 node-maintenance window**, Docker Hub 429 broke pulls **three separate times**:
1. masters-league CI base images (`node:22-alpine`) failed the build.
2. Post-drain rescheduled pods stuck `ImagePullBackOff`: loki, jellystat, filebrowser, vmware-exporter.
3. b2-exporter got stuck (Harbor-hosted, but the kubelet negative-cached during the same churn).

All recovered when the rate-limit window reset, so it's **not an emergency** — but it recurs on every mass-reschedule (node maintenance, reboots, the future Cilium migration) and worsens as the cluster grows. The user confirmed: "I swear that didn't used to be a problem, but the cluster does have more going on now." Exactly — more workloads, more pulls, lower headroom under the shared anonymous limit.

Prior point-fixes (Tailscale → ghcr.io mirrors, PRs #743/#746) were manual and per-image; they don't generalize.

## Goal

A **Harbor proxy-cache (pull-through) project for Docker Hub**. docker.io images are pulled *through* Harbor, which caches them. After the first pull, subsequent pulls come from Harbor in-cluster — no rate limit. Authenticating the proxy to a Docker Hub account (pull-only token) raises the upstream limit further.

## Verified facts (2026-05-30)

- **Harbor:** v2.15.1 — proxy-cache projects fully supported (mature since 2.4). Chart 1.19.0.
- **tofu provider:** `goharbor/harbor ~> 3.10` — supports `harbor_registry` (upstream endpoint) and proxy-cache projects. **VERIFY** the exact argument that marks a `harbor_project` as a proxy cache in 3.10 (commonly `registry_id` on the project; confirm name/shape).
- **tofu/harbor module** (`terraform/harbor/`): has `projects.tf` (library, vollminlab), `robots.tf` (github-actions push, cluster-pull pull-only), `config.tf`, `providers.tf` (admin auth), `variables.tf`, `imports.tf`. **No registries.tf, no proxy project** — confirmed `/api/v2.0/registries` is empty as admin.
- **Cluster pull identity:** `robot$vollminlab+cluster-pull` (pull-only) via `harbor-vollminlab-pull` dockerconfigjson secret in app namespaces.
- **containerd registry mirror:** NOT configured — `config_path = ""`, no `/etc/containerd/certs.d/`. (Re-verify on a node when SSH agent is available; assumed unchanged.) This drives the architecture decision below.
- **Image blast radius (sampled, NOT exhaustive — rebuild at implementation):** explicit docker.io-default images seen include `cloudflare/cloudflared:2026.3.0`, `filebrowser/filebrowser:v2.63.3`, `flaresolverr/flaresolverr:v3.3.21`, `itzg/minecraft-server:2025.3.0`, `cyfershepard/jellystat:1.1.10`, `pryorda/vmware_exporter:v0.18.4`, `redis:7.4.2-alpine`, `alpine/k8s:1.30.3`, `docker.io/alpine/kubectl:1.33.4`, `docker:26-dind` (ARC), `velero/velero-plugin-for-aws:v1.14.0`, plus Helm subchart defaults (loki, promtail, grafana, kube-prometheus-stack). Build the authoritative list at implementation (command below).

## Two architecture options — DECIDE FIRST (brainstorm at session start)

### Option A — Repoint image references (GitOps-native)
Change each `docker.io/foo/bar:tag` → `harbor.vollminlab.com/dockerhub-proxy/foo/bar:tag` in manifests/Helm values (library images → `dockerhub-proxy/library/<name>`).
- **Pros:** Pure GitOps, no node changes, explicit/auditable, immediate on reconcile. Same pattern masters-league Dockerfile should adopt.
- **Cons:** Must touch every ref; Helm subchart images (loki/grafana) are set via `values` overrides — fiddlier; **Renovate** must still track upstream versions (proxy path keeps `foo/bar:tag` suffix so regex *should* match — VERIFY).

### Option B — containerd registry mirror (node-level, transparent) — recommended for durability
Configure containerd on every node: `config_path = "/etc/containerd/certs.d"` + `/etc/containerd/certs.d/docker.io/hosts.toml` pointing at the Harbor proxy. Image refs stay `docker.io/...`; containerd silently fetches via Harbor.
```toml
server = "https://registry-1.docker.io"
[host."https://harbor.vollminlab.com/v2/dockerhub-proxy"]
  capabilities = ["pull", "resolve"]
  override_path = true
```
- **Pros:** Zero manifest changes; covers ALL docker.io images including subchart defaults and future ones; transparent; Renovate unaffected.
- **Cons:** Node-level = **ansible change** (not GitOps); apply to all 9 nodes + bake into node-prep for future nodes; containerd restart per node (net-new config since `config_path=""`).

**Recommendation:** **Option B** is the durable, complete fix (transparent, covers everything, survives growth) and the cluster already warrants an ansible node-prep pass (iscsid/multipath items noted during the maintenance). **Option A** ships today purely via Flux but leaves gaps (subcharts, future images). Pick one before coding.

## Implementation outline

### Phase 1 — Harbor proxy-cache project (tofu; both options need this)
1. `terraform/harbor/registries.tf`: `harbor_registry "dockerhub"` — type `docker-hub`, url `https://hub.docker.com`. Optionally authenticated: add `harbor_dockerhub_user` + `harbor_dockerhub_token` (pull-only) to the `harbor-tf-credentials` SealedSecret → `TF_VAR_*`. **Save the Docker Hub token to 1Password first** (secrets rules).
2. `terraform/harbor/projects.tf`: `harbor_project "dockerhub_proxy"` — name `dockerhub-proxy`, public **true** (cluster-pull robot reads it without per-ns secrets), set proxy `registry_id = harbor_registry.dockerhub.registry_id` (VERIFY arg name in provider 3.10).
3. Apply via the `harbor-config` tofu-controller workspace (reconciles cleanly today). Watch the robot-import quirks in `project_harbor_tf_robot_fix`.
4. Verify pull-through: `curl -sk -u admin:$pw "https://harbor.vollminlab.com/v2/dockerhub-proxy/library/busybox/manifests/latest"` → 200.

### Phase 2a — IF Option B (containerd mirror)
1. In `ansible-playbooks`: node-prep task to set `config_path` + write `docker.io/hosts.toml` (above). New playbook or extend the node-prep role.
2. Restart containerd per node (rolling, low-churn window — the cluster just went through heavy maintenance 2026-05-30).
3. Apply to all 9 nodes + bake into node-prep so new nodes inherit it.
4. Verify on a node: `crictl pull docker.io/library/busybox:latest` succeeds; repo appears in Harbor dockerhub-proxy.

### Phase 2b — IF Option A (repoint refs)
1. Build authoritative list:
   ```bash
   grep -rh "image:" clusters/ | grep -v "#" \
     | grep -ivE "harbor.vollminlab|ghcr.io|registry.k8s.io|quay.io|gcr.io" \
     | sed -E 's/.*image: *"?//; s/"?$//' | sort -u
   ```
   Plus Helm subchart images in each `configmap.yaml` values (loki/promtail/grafana/kube-prometheus-stack).
2. Repoint each to `harbor.vollminlab.com/dockerhub-proxy/<path>:<tag>`.
3. Focused PR(s); Flux reconciles. Update masters-league Dockerfile base images too.
4. Confirm Renovate still detects upstream bumps.

### Phase 3 — validation
- Delete a previously-429'd pod (e.g. vmware-exporter) → pulls cleanly.
- Harbor dockerhub-proxy project shows cached repos.
- Kyverno `:latest` block + required labels still apply (proxy preserves tags — verify no policy impact).
- Runbook in `docs/runbooks/` + roadmap update.

## Out of scope
- "Right-size memory requests" (separate problem, `project_worker02_memory_pressure`).

## Open questions for session start
1. **Option A vs B** — the core decision.
2. Authenticated vs anonymous upstream — authenticated (pull-only token) gives higher limits; worth one SealedSecret. Confirm Docker Hub account/token availability.
3. Exact `goharbor/harbor` 3.10 proxy-cache project syntax (registry_id attribute).
