# Plan: Harbor Docker Hub pull-through cache

**Created:** 2026-05-30
**Spec:** `docs/superpowers/specs/harbor-dockerhub-proxy-cache-design.md`
**Goal:** Stop Docker Hub 429 `ImagePullBackOff` storms by pulling all docker.io images through a Harbor proxy-cache project.

## Why (evidence)
2026-05-30 node maintenance hit Docker Hub anonymous 429 three times (masters-league CI; post-drain loki/jellystat/filebrowser/vmware-exporter; b2-exporter negative-cache). Self-recovered when the window reset, so not an emergency — but recurs on every mass reschedule and worsens with growth. Durable fix.

## Decide first (brainstorm)
**Option A repoint refs** (GitOps, per-image, subchart gaps) vs **Option B containerd mirror** (ansible node-prep, transparent, covers all). Recommend **B** for durability, **A** to ship today via Flux. Don't code until chosen. Also decide: authenticated (Docker Hub pull-only token → 1Password + SealedSecret) vs anonymous upstream.

## Verified (2026-05-30)
- Harbor v2.15.1, provider goharbor/harbor ~>3.10 (supports harbor_registry + proxy project).
- terraform/harbor/ has projects.tf/robots.tf; NO registries.tf, NO proxy project; /api/v2.0/registries empty.
- containerd config_path="" , no certs.d → Option B is net-new node config.
- Pull identity: robot$vollminlab+cluster-pull via harbor-vollminlab-pull secret.
- ~16 docker.io images (cloudflared, filebrowser, flaresolverr, minecraft-server, jellystat, vmware_exporter, redis, alpine/k8s, alpine/kubectl, docker:dind, velero-plugin-for-aws + Helm subchart defaults).

## Steps
1. Brainstorm A vs B + auth vs anon. If auth: create Docker Hub pull-only token, save to 1Password.
2. **Phase 1 (both): Harbor proxy project via tofu**
   - registries.tf: harbor_registry "dockerhub" (docker-hub, https://hub.docker.com, optional auth from harbor-tf-credentials SealedSecret).
   - projects.tf: harbor_project "dockerhub_proxy" (name dockerhub-proxy, public=true, proxy registry_id — VERIFY 3.10 arg).
   - Apply via harbor-config tofu-controller workspace; watch robot-import quirks (project_harbor_tf_robot_fix).
   - Verify: curl -sk -u admin:$pw https://harbor.vollminlab.com/v2/dockerhub-proxy/library/busybox/manifests/latest → 200.
3. **Phase 2:**
   - B: ansible node-prep → config_path=/etc/containerd/certs.d + docker.io/hosts.toml → proxy; rolling containerd restart all 9 nodes + bake into node-prep; verify crictl pull docker.io/library/busybox.
   - A: repoint all docker.io refs → harbor.vollminlab.com/dockerhub-proxy/... (incl subchart values + masters-league Dockerfile); PR; Flux reconcile; confirm Renovate still tracks versions.
4. **Phase 3: validate** — delete a previously-429'd pod, confirm clean pull; Harbor shows cached repos; runbook + roadmap update.

## Cautions
- No plaintext Docker Hub token — SealedSecret + 1Password.
- harbor-config tofu workspace has robot-import friction history — check CR health before/after.
- Option B containerd restarts during low churn (cluster just did heavy maintenance 2026-05-30).
- Kyverno :latest block + labels still apply; proxy preserves tags — verify no policy impact.

## Related memory
[[project_masters_league_ci]] · [[project_worker02_memory_pressure]] · [[project_harbor_lb_migration]] · [[project_harbor_tf_robot_fix]]
