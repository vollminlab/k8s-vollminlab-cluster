# Triage Agent Design

**Date:** 2026-10-11
**Status:** Design approved, not yet planned
**Goal:** When something in the cluster breaks, have a root-cause diagnosis waiting before anyone opens a terminal. For a short allowlist of well-understood failure classes, also open the fix PR without being asked. A human still merges.

---

## Background and why not an existing tool

The design is inspired by [k8s-mechanic](https://github.com/lenaxia/k8s-mechanic). That project is a controller that watches for failing workloads and has an in-cluster LLM agent open fix PRs. It was evaluated and rejected as-is:

- **This repo is public.** mechanic writes cluster evidence into PR bodies, and its secret redaction only matches patterns. Hostnames, topology and account details would be published.
- **It detects only loud failures.** It handles CrashLoopBackOff, OOMKilled, NotReady and similar, all of which Alertmanager already covers. The incidents that cost the most here were silent: empty Velero backups reporting `Completed`, Kyverno policies evaluating nothing, a healer CronJob exiting 137 behind a Ready status. It has no HelmRelease or Kustomization detection.
- **Its fixes are generic.** The `.claude/rules/` files exist because the obvious fix is often wrong here. Raising a limit to fix an unscoped list only defers the OOM, and patching webhook `failurePolicy` gets reverted. A generic prompt proposes exactly these.
- **It looks stalled.** The last release was v0.4.4 in March 2026, with only Renovate commits since.

What it gets right, and what this design keeps: deduplication by parent workload, a read-only agent, a human merging every change, and a dry-run mode.

The key differences in this design:

1. **The agent is Claude Code, run from a checkout of this repo.** `CLAUDE.md` and `.claude/rules/` load automatically, so the agent starts with everything already learned.
2. **The agent cannot publish.** A separate, deterministic publisher decides what reaches GitHub. No LLM-written free text ever reaches the public repo.
3. **Detection reuses existing alerting.** The agent is driven by Alertmanager and Flux events plus a nightly sweep for silent failures. It has no watcher of its own.

---

## Decisions

| Decision | Choice |
|---|---|
| Purpose | Hands-off fix PRs for allowlisted classes; a private diagnosis for everything else |
| Allowlisted classes | `oom-memory-limit`, `cpu-throttle-limit`, `renovate-breakage` — each starts in `shadow` mode |
| Report destination | Issues in a new **private** repo, `vollminlab/triage-reports`, plus a Pushover notification |
| Runtime | In-cluster: namespace `triage`, category `observability` |
| Claude auth | Dedicated Anthropic Console **API key** (see below) |
| Triggers | Alertmanager webhook, Flux notification-controller events, nightly sweep |
| Architecture | dispatcher → suspended Job per incident → `investigate` (Claude Code) → `publish` (plain code) |

**Why an API key and not a subscription token:** Claude Code's legal and compliance page says OAuth subscription auth "is designed to support ordinary use of Claude Code", and that people building services "should use API key authentication." An unattended alert handler is at best a grey area under the subscription terms. An API key also gives a per-run `--max-budget-usd` cap and visible spend.

---

## Architecture

```
Alertmanager ──webhook──┐
Flux notification ──────┼──▶ dispatcher (Deployment) ──creates suspended Job──▶ triage Job
nightly CronJob ──tick──┘        │  dedup via GitHub issues                     ├─ investigate: claude -p  → result.json
                                 │  un-suspends 1 Job at a time                  └─ publish: issue / PR / Pushover
                                 └─ metrics, spend cap
```

### Components

| Unit | Location | Responsibility | Credentials |
|---|---|---|---|
| **dispatcher** | `build/triage-dispatcher/` (Python), deployed at `clusters/vollminlab-cluster/triage/dispatcher/app/` | Receives events, normalizes them to `Incident`, fingerprints, deduplicates, manages the Job queue, enforces the spend cap, exposes metrics | RBAC: create/list/patch/delete Jobs in `triage`; read-only get on pods, ReplicaSets, Jobs and Flux objects cluster-wide, for owner resolution and re-checks. GitHub App token for **read-only** issue search on the reports repo. Pushover token. |
| **investigate** container | `build/triage-agent/` image | Shallow-clones this repo at `main` (public, so no auth needed), runs `claude -p` inside a wrapper, writes `/work/result.json` | Anthropic API key. ServiceAccount `triage-agent`: get/list/watch on all resources **except Secrets**, plus `pods/log`. **No GitHub write credential.** |
| **publish** container | same image, `publish.py`, no LLM | Reads `result.json`; writes the private issue; for live classes validates the patch and opens the PR; sends Pushover | GitHub App installation token; Pushover token |
| **sweep** | CronJob in `triage`, nightly | POSTs `{"source":"sweep"}` to the dispatcher | none |
| **policy config** | ConfigMap `triage-policy` (in git) | Class allowlist and `shadow\|live` mode per class, model, max turns, per-run and daily budget caps, sweep checklist, queue cap | none |

The investigate and publish containers share one Job and an `emptyDir`. Publish runs as the main container, and investigate runs first as an init container whose wrapper **always exits 0** (see Error handling). Nothing holding the API key or GitHub token outlives a single incident.

**Credential isolation inside the pod.** The two containers share the pod's ServiceAccount token. So the `triage-agent` ServiceAccount must stay read-only, and publish gets **no Kubernetes write access either**: it reports back only through its container termination message, which the dispatcher reads from pod status. The GitHub App Secret and Pushover Secret are volume-mounted into the publish container only. The Anthropic key is mounted into the investigate container only. Because investigate runs as an init container, it has finished before publish's credentials are mounted, and containers do not share a PID namespace.

### New external pieces

- Private GitHub repo `vollminlab/triage-reports`, created via tofu in `github-admin` per the branch-protection model, not the UI.
- GitHub App `vollminlab-triage`: Issues read/write on `triage-reports`; Contents and Pull requests write on `k8s-vollminlab-cluster`. Installed on those two repos only.
- 1Password items, all tagged Homelab: `Triage Anthropic API Key`, `Triage GitHub App` (`app_id`, `installation_id`, `private_key`) and `Triage Pushover` (`token`, `user`). Each is consumed by an ExternalSecret in `triage`.
- Alertmanager: a `triage` webhook receiver added as a `continue: true` route alongside the existing Pushover route. Pushover behaviour is unchanged.
- Flux: a notification-controller `Provider` (type `generic`, address = dispatcher Service) and an `Alert` on `HelmRelease` and `Kustomization` with `eventSeverity: error`. Neither exists today, so Flux failures currently notify nowhere.

### Configuration defaults (initial values, recalibrated in Live verification step 6)

| Key | Initial value |
|---|---|
| `model` | `claude-opus-5-5` |
| `max_turns` | 40 |
| `per_run_budget_usd` | 3 |
| `daily_budget_usd` | 15 |
| `queue_cap` | 10 |
| `flux_settle_seconds` | 600 |
| `reinvestigate_after_hours` | 24 |
| `sweep_schedule` | `30 7 * * *` UTC (after the 03:00–06:00 backup window) |
| all classes | `shadow` |

---

## Data flow

### 1. Intake and normalization

Every source becomes an `Incident` with these fields: `source` (`alertmanager` / `flux` / `sweep`), `reason` (alertname or Flux reason), `namespace`, `workload` (kind/name), `labels`, `started_at`, and the raw payload.

For pod-keyed alerts, the dispatcher resolves the pod to its top-level owner (Pod → ReplicaSet → Deployment, Pod → Job → CronJob, and so on). Five restarting pods of one Deployment are therefore one incident, and pod churn faster than an alert's `for:` cannot fragment it (#1266).

### 2. Fingerprint

`sha256(source, reason, namespace, workload)`, truncated to 12 hex characters for labels. Volatile values (pod hashes, messages, timestamps) are excluded.

### 3. Deduplication: GitHub issues are the state

There is no database and no CRD. Each issue body carries `<!-- fp:<full-hash> -->` and the label `fp/<12-hex>`.

- A Job labelled `triage.vollminlab.com/fp=<12-hex>` already exists (suspended or running) → drop the event.
- An open issue exists for the fingerprint, updated less than `reinvestigate_after_hours` ago → drop.
- An open issue exists and is older → dispatch. The result is posted as a **comment** on that issue.
- No open issue → dispatch a new incident.

### 4. Settle and queue

Alertmanager already applies `for:` and grouping, so its events are queued immediately. Flux events wait `flux_settle_seconds`, because Flux retries on its own.

Each Job is created with `spec.suspend: true`, so the queue *is* Kubernetes and survives a dispatcher restart. When no triage Job is running, the dispatcher un-suspends the oldest one. Just before un-suspending, it re-checks that the alert is still firing (Alertmanager API) or that the Flux object is still not Ready. If not, it deletes the Job.

When `queue_cap` is reached, new events are dropped and a single Pushover message is sent until the queue drains.

### 5. Investigate

The wrapper:
1. Clones the repo.
2. Writes the incident to `/work/incident.json`.
3. Runs `claude -p` from the checkout root with `--output-format json`, `--json-schema` (the result schema), `--max-turns`, `--max-budget-usd`, `--permission-mode dontAsk`, and the baked-in settings (see Security).

The prompt is a short fixed instruction plus the incident. Cluster-derived text is wrapped in an explicit untrusted-data envelope.

`result.json` schema:

| Field | Type | Reaches public PR? |
|---|---|---|
| `status` | `ok` / `failed` | no |
| `failure_reason` | `max_turns` / `budget_cap` / `timeout` / `api_error` / `invalid_output` / null | no |
| `summary`, `root_cause` | string | **no** |
| `evidence[]` | strings | **no** |
| `confidence` | `low` / `medium` / `high` | no |
| `class` | allowlisted class name or `none` | yes (selects the template) |
| `patch` | unified diff or null | yes (as the diff itself) |
| `metrics` | map of name → number | yes (validated numeric) |
| `injection_suspected` | bool | no |
| `cost_usd` | number, from Claude's `total_cost_usd` | no |

### 6. Publish

**Always:** create the private issue (or a comment) with the full write-up, labels `fp/<hash>` and `class/<class>`, and status labels as applicable: `agent-error`, `redacted`, `injection-suspected`, `shadow`.

**A PR is opened only if all four gates pass:**

1. `class` is allowlisted and in `live` mode.
2. `confidence` is `high`.
3. The **diff guard** passes:
   - Every changed file is under the incident workload's app directory (`clusters/vollminlab-cluster/<ns>/<app>/`).
   - No changed file matches `*-externalsecret.yaml`, `kustomization.yaml` or `*networkpolicy*`.
   - No files are added or deleted.
   - Changed lines are limited to the class's fields:
     - `oom-memory-limit`: `resources.limits.memory` (and the matching request), increase only, no more than 2× the current value, and the new value ≥ `metrics.peak_memory_bytes` × 1.2.
     - `cpu-throttle-limit`: removal of a `resources.limits.cpu` line only, and `metrics.throttled_ratio` is present.
     - `renovate-breakage`: a single `version:`, `tag:` or image-tag line, which must equal a value present in this repo's git history for that line (a revert or pin, never a new version). Renovate-pinned packages listed in `.claude/rules/` (for example `velero-plugin-for-aws`) are rejected outright.
4. Validation passes: `kustomize build` of the app's Flux Kustomization path, `kubeconform`, and `kyverno test` with the repo's CI policies.

**The PR body is generated by code**, never by the model. It is built from:
- A fixed per-class paragraph citing the rule file that justifies the change.
- A table parsed from the diff: file, field, old value, new value.
- The numeric `metrics` values.
- The fingerprint, as `fp/<12-hex>`, with no link to the private issue.

Branch: `fix/triage-<class>-<12-hex>`. Title: `fix(<app>): <class template>`, for example `fix(renovate): raise memory limit to measured peak (triage)` for an OOM in the `renovate` app.

**In shadow mode**, gates 2–4 still run. The issue shows the exact branch, title, body and diff that *would* have been opened, plus which gates passed.

### 7. Notify

One Pushover message per new issue: the summary's first line (private channel, so this is fine), the issue link, and the PR link if one was opened. Comments on existing issues do not notify.

### 8. Resolution

When Alertmanager sends `resolved`, or a Flux event reports Ready for the object, the dispatcher:
- Comments "resolved at <time>" on the open issue for that fingerprint and closes it.
- Deletes any suspended Job for that fingerprint.

Open PRs are left alone.

### 9. Nightly sweep

The sweep incident's prompt comes from the `sweep_checklist` in `triage-policy`. Initial checklist:
- Velero backups in the last 24h with zero PodVolumeBackups on a selector-based schedule.
- Kyverno ClusterPolicies with zero `pass` results.
- CronJobs whose last N Jobs exited non-zero.
- Flux objects not Ready for more than 1h.
- VolSync ReplicationSources whose last sync is older than 36h.
- PVCs protected by neither Velero nor VolSync (the per-pod method in `velero.md`).

Each finding becomes its own incident with fingerprint `sha256("sweep", check, namespace, object)` and goes through deduplication normally. **Sweep findings never open PRs**, regardless of class. They always get diagnosis issues.

---

## Error handling

### Agent failures produce a visible issue, never silence

The wrapper exits 0 in every case and always writes `result.json`. It maps failures to `failure_reason`:

| Condition | Detected by | `failure_reason` |
|---|---|---|
| Turn limit hit | Claude JSON `subtype` / non-zero exit with max-turns error | `max_turns` |
| Budget cap hit | Claude JSON error | `budget_cap` |
| Wall clock | wrapper `timeout` (Job `activeDeadlineSeconds` − 120s) | `timeout` |
| 429, 5xx, credit exhausted | stderr / JSON error | `api_error` |
| Output fails schema | wrapper validation | `invalid_output` |

Publish turns any of these into an issue labelled `agent-error`, with the raw output attached. The Job has `backoffLimit: 0`, because a retry would pay for the whole investigation again.

### Publish failures

GitHub calls retry three times with exponential backoff inside the container. On final failure, Pushover receives the summary directly, marked "publish failed". A gate that blocks a PR is reported in the issue with the gate name and the rejected diff.

### Dispatcher

The dispatcher exposes Prometheus metrics (with a ServiceMonitor):
- `triage_events_total{source,outcome}` (dispatched / deduped / dropped_queue_full / dropped_resolved / ignored_self)
- `triage_jobs_total{status}`
- `triage_spend_usd_total`
- `triage_last_sweep_success_timestamp`

Spend is read from completed Jobs' `cost_usd`. Publish writes a one-line JSON summary (`cost_usd`, outcome, issue number) as its container termination message, and the dispatcher reads it from the pod status.

New PrometheusRules:
- `TriageDispatcherDown`: the dispatcher target is down for 10 minutes.
- `TriageSweepStale`: `time() - triage_last_sweep_success_timestamp > 26h`.
- `TriageAgentErrors`: 3 or more `agent-error` outcomes in 24h.

**Spend cap:** when the rolling 24h spend reaches `daily_budget_usd`, the dispatcher stops un-suspending Jobs (the queue holds) and sends one Pushover message.

**Self-loop guard:** events whose namespace is `triage` are ignored (counted as `ignored_self`). Those alerts still reach Pushover through the existing route.

---

## Security

Threat model: **any text the agent reads from the cluster may be an attacker's prompt injection.** Logs, events and annotations are reachable from workloads, including the DMZ.

1. **Nothing the model writes reaches the public repo verbatim.** The PR body is code-generated. The patch is constrained by the diff guard to the incident's own app directory and the class's fields.
2. **No write credentials in the agent.** The `triage-agent` ServiceAccount has get/list/watch on all resources except `secrets`, plus `pods/log`. The ClusterRole is written with explicit resource lists, not a wildcard minus Secrets, because RBAC has no deny. The GitHub token exists only in the publish container.
3. **Claude Code settings are baked into the image** as managed settings. Those have the highest precedence, and a deny rule cannot be overridden by an allow at any level, so a repo-level `.claude/settings.json` cannot loosen them. `claude -p` runs project hooks without a trust prompt, so the managed settings also disable hooks, and the static lockdown test asserts that the cloned repo contains no `.claude/settings.json`. If a repo-level settings file ever appears, the wrapper refuses to run and reports `invalid_output`.
   - Allow: Read, Grep and Glob within the checkout. `Bash(kubectl get *)`, `Bash(kubectl describe *)`, `Bash(kubectl logs *)`, `Bash(kubectl top *)`, `Bash(kubectl events *)`, `Bash(flux get *)`, `Bash(helm history *)`, `Bash(helm list *)`, and `Bash(curl -s http://victoria-metrics-single-server.monitoring.svc:8428/*)`, `Bash(curl -s http://victoria-metrics-lt-server.monitoring.svc:8428/*)`, `Bash(curl -s http://loki.monitoring.svc:3100/*)`.
   - Deny: WebFetch, WebSearch, Edit, Write (except under `/work/scratch/`), `Bash(helm get *)`, `Bash(kubectl get secret*)`, and any `Bash(kubectl * -o yaml* secret*)` form.
4. **NetworkPolicy in `triage`:**
   - `default-deny-all` and `allow-dns`.
   - The dispatcher accepts ingress on its container port from `monitoring` (Alertmanager, Prometheus scrape), `flux-system` (notification-controller) and the sweep CronJob.
   - Egress for the agent and publish pods:
     - the apiserver on 6443
     - `monitoring` on 8428 (VictoriaMetrics) and 3100 (Loki), both container ports verified 2026-10-11
     - 443 to `0.0.0.0/0` except RFC1918, for Anthropic, GitHub clone and API, and Pushover
   - Egress for the dispatcher: the apiserver, `monitoring` on 9093 (Alertmanager `http-web` container port, verified 2026-10-11) and 443 (GitHub, Pushover).
   - Container ports are verified per `networkpolicy.md` before writing, and rows are added to its port table.
5. **`monitoring` is default-deny on ingress.** It needs a new `allow-triage` policy, scoped per the `networkpolicy.md` checklist to the target pods and ports, never `podSelector: {}`:
   - `app: server` (VictoriaMetrics, both tiers) on 8428 and `app: loki` on 3100, from `triage` pods labelled `app: triage-agent`.
   - `app: alertmanager` on 9093, from `app: triage-dispatcher`.
6. **Accepted residual risk:** NetworkPolicy cannot filter by FQDN, so internet egress on 443 is open. An injected agent could in principle send data it can read to an outside host. It cannot read Secrets, and its Bash allowlist does not permit arbitrary `curl`. An egress proxy is deliberately out of scope.
7. **Leak scan.** Publish runs `gitleaks` over the issue body, comments, the patch and the PR body before posting. On a hit, it redacts the match and labels the issue `redacted`. A hit in the PR body or patch blocks the PR.
8. **Existing gates still apply to every PR:** CI (Kyverno test, gitleaks secret scanning), branch protection, and a human merge.

---

## Testing

### Unit tests (pytest), wired into CI from day one

`build-triage-dispatcher.yaml` and `build-triage-agent.yaml` run the test suite before pushing to Harbor, and a test failure blocks the push.

- **Dispatcher:**
  - Normalization from captured real Alertmanager and Flux payloads (fixtures).
  - Fingerprint stability across pod hashes.
  - Deduplication decisions against a fake GitHub client.
  - Suspended-queue ordering and the cap.
  - Settle delay.
  - Resolve closes the issue and deletes the queued Job.
  - Own-namespace ignore.
  - Spend-cap halt.
- **Publish** (security-critical, adversarial fixtures):
  - The diff guard rejects a patch that touches another app's directory, touches a `*-externalsecret.yaml`, adds a file, changes an image under the OOM class, lowers a memory limit, raises it more than 2×, raises it without `metrics.peak_memory_bytes`, or pins `velero-plugin-for-aws`.
  - **Provenance canary:** a unique marker string placed in every free-text field of `result.json` must never appear in the generated PR body or branch name.
  - gitleaks redaction on a planted fake key, and blocking when the key is in the patch.
  - Each gate failure renders its explanation in the issue.
  - Shadow mode renders the would-be PR and never calls the PR API.
- **Wrapper** (stub `claude` binary): success, invalid JSON, max-turns, budget cap, timeout and 429 each produce the matching `result.json`, and the wrapper exits 0 in every case.
- **Lockdown, static check:** the managed settings contain every required deny entry, and the image contains no project hooks.

### Live verification after first deploy (all classes in `shadow`)

1. **Alertmanager path:** `amtool alert add TriageTest` with a namespace and workload label routes through the real config. Check that a Job ran, a private issue was created, a second `amtool` fire was deduplicated, and expiring the alert closed the issue.
2. **Real OOM:** a `triage-canary` Deployment (standard labels, approved-registry image, tiny memory limit) OOMs on purpose. The shadow PR must raise the limit based on measured peak usage and pass every gate.
3. **Flux path:** POST a captured Flux failure event to the dispatcher and run the same checks.
4. **Injection red-team:** the canary logs instructions telling the agent to curl an outside host, print Secrets, and open a PR changing another app. Expected: denied tool calls in the Claude JSON log, `injection-suspected` on the issue, no PR, and no outside connection in the canary's or agent's flow logs.
5. **Sweep:** the first nightly run sets `triage_last_sweep_success_timestamp`, and `TriageSweepStale` stays quiet.
6. **Cost:** read `cost_usd` from the runs above, set `per_run_budget_usd` and `daily_budget_usd` from measured values, and record them in this spec.

Delete the canary once these pass. **A Ready Deployment and a Completed Job are not evidence.** Each step is verified by the artifact it produces: the issue, the label, the metric, or the absent PR.

### Shadow mode as the acceptance test

Each shadow issue is labelled `shadow-correct` or `shadow-wrong` by a human. A class is flipped to `live` in `triage-policy`, by a PR, only after **3 consecutive `shadow-correct` with no `shadow-wrong` in between** for that class. Promotion is always a manual git change.

---

## Out of scope

- Any write to the cluster other than Jobs in `triage`. All changes go through git.
- Auto-merge.
- Auto-closing triage PRs.
- An egress proxy or FQDN filtering.
- Investigating `triage`'s own failures.
- Additional fix classes. These are added later by editing `triage-policy` and the diff guard, each starting in `shadow`.

---

## Docs to update when implemented

- `docs/cluster-reference.md`: the `triage` namespace, dispatcher, agent image and sweep CronJob (CI enforces this).
- `.claude/rules/networkpolicy.md`: port table rows for `triage` and the `monitoring` `allow-triage` rule.
- `.claude/rules/kyverno.md`: no change. `observability` is an existing category.
