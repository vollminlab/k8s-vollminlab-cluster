# CLAUDE.md — Vollminlab Kubernetes Cluster

GitOps-managed Kubernetes cluster using Flux CD. All workloads are Helm-based Flux resources under `clusters/vollminlab-cluster/`.

**Full configuration reference (versions, values, resource limits, network policies):** `docs/cluster-reference.md`

## Essential reading before working

- `.claude/rules/flux.md` — repo layout, HelmRelease conventions, reconciliation commands
- `.claude/rules/kyverno.md` — required labels, enforce/audit policies, DMZ rules, autogen danger
- `.claude/rules/secrets.md` — SealedSecrets workflow, never plain Secrets
- `.claude/rules/subagents.md` — when to spawn agents vs. act directly
- `.claude/rules/storage.md` — Longhorn capacity check before PVC sizing, replica math, online resize
- `.claude/rules/velero.md` — backup schedules, circular backup check, kopia GC, status commands
- `.claude/rules/git-workflow.md` — branch naming, session hygiene, /compact reminders
- `.claude/rules/networkpolicy.md` — container port verification, per-namespace port table, pre-PR checklist

**Operational runbooks** (reference when needed, not loaded every session):
`docs/runbooks/` — flux-templates, kyverno-recovery, homepage, external-dns, incidents

## Hard constraints

- Never commit a plain `kind: Secret`. Use `SealedSecret` only.
- Never push directly to `main`. PR required (branch protection enforced via GitHub repository settings).
- Never touch `bootstrap/calico/` with Flux. CNI changes are manual + verified.
- Never use `:latest` image or chart version tags. Kyverno blocks them.
- All pods require `app`, `env`, and `category` labels. Kyverno enforces in enforce mode.
- Never set `policy: sync` in external-dns — shared Pi-hole backend, `upsert-only` only. `policy: sync` wiped all infrastructure DNS records on 2026-04-05.
- Every new Ingress must include `shlink.vollminlab.com/slug: <service-name>` — the shlink-ingress-controller uses this to auto-create a `vollm.in/<slug>` short URL. Use the service name from the hostname (e.g. `radarr.vollminlab.com` → slug `radarr`). See `clusters/vollminlab-cluster/homepage/homepage/app/ingress.yaml` as the canonical example.

## Bootstrap / DR

`bootstrap/` is **not** Flux-managed. It contains manual DR reference manifests:
- `bootstrap/calico/` — CNI, must be applied before Flux bootstrap
- `bootstrap/coredns/` — CoreDNS custom config
- `bootstrap/sealed-secrets/` — sealing key restore procedure

Sealing key is backed up in 1Password as **"Sealed Secrets Sealing Key"**.
