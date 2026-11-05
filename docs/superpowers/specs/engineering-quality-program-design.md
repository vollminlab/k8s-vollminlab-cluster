# Engineering Quality Program — Design

**Created:** 2026-10-28
**Status:** Approved in conversation; each item is tracked by its own issue (#1187, #1416–#1423).

## Goal

Catch the defects this cluster actually has before they reach production, and prove that
what is running does what it claims. Do it without slowing the PR loop.

## The evidence this is built on

Read the incident history back and almost no failure was a code bug a diff reviewer would have
caught. They were cases where **the status said yes and the system said no**:

| Incident | What reported success | What was true |
|---|---|---|
| `velero-victoria-metrics-b2` | `Completed`, 0 errors, nightly | Matched zero volumes for its whole life |
| Three Kyverno policies (#1104, #1109) | Policy Ready, 0 fails | `foreach.list` resolved to null; evaluated nothing |
| `velero-pvb-healer` (#1056) | Flux Ready, CronJob reconciled | Exited 137 on every run |
| NetworkPolicy port trap (×2) | Policy applied | Used the service port; allowed nothing |
| Portainer data loss | Flux applied the rename | A rename is a prune-delete of the PVC |
| `velero-plugin-for-aws` v1.14.2 | Merged, CI green | Broke B2 six days later, on pod restart |
| Proxy-auth apps (#1329/#1330) | Default-deny namespace | Unscoped monitoring rule let pods forge identity headers |
| Kyverno cold-boot deadlock (#1305) | Patch job logged success every 5 min | The admission controller reverted every patch |

The design follows from that: **test against the failure classes we have had, and verify the
running system, not the status field.**

## Principles

1. **The PR path stays fast.** A blocking PR check must be deterministic and add well under a
   minute per change. Anything slow, flaky or live-cluster-heavy runs after merge or nightly.
2. **Deterministic beats probabilistic.** A known attack gets a scripted test that runs every
   night for free. AI review and agent pen-testing look for the *unknown* and are advisory.
3. **Every check proves it ran.** A check that can pass by evaluating nothing gets a guard
   that fails it on empty input, the same lesson as `scripts/test-kyverno-policies.py`.
4. **Measure the reviewers.** An AI reviewer whose findings are mostly dismissed is a cost, and
   it trains the human to skim. Each one is on a trial with a stated hit-rate bar.
5. **Restore beats backup.** A backup that has never been restored is not a control.

## The tiers

| Tier | When | Blocks merge | Contents |
|---|---|---|---|
| 1 | Every PR | Yes | Existing checks + rendered diff, destructive-change guard, NetworkPolicy port lint |
| 2 | After merge + nightly | No (alerts) | Security conformance probe, "it actually ran" checks, kubescape trend |
| 3 | Every PR | No | One AI reviewer from a different model family, advisory, on trial |
| 4 | Quarterly / on demand | No | Scoped agent pen test; a full audit against a published benchmark |

## Items, in priority order

### 1. etcd snapshots and a restore test — #1187, #1416

Nothing snapshots etcd on a schedule (#1187). Velero's restore test has been an unmet roadmap gate
since Phase 8 was written.

- Scheduled etcd snapshot on the control plane, kept on-host or in a target that is as
  protected as etcd itself. **Snapshots hold every Secret in plaintext**; they are never copied
  to a general-purpose bucket.
- A documented restore drill for Velero: restore one namespace with a PVC into a scratch
  namespace, verify the data, delete it. Run it quarterly and record the date in the roadmap.
- **Done when:** a snapshot exists from the last 24h, alerting fires if one is missing, and one
  Velero restore has been verified end to end.

### 2. Rendered diff and destructive-change guard (tier 1) — #1417

Today `integration-test` server-side dry-runs **changed HelmReleases only**. A PR that touches only
raw manifests (36 of 89 app directories) is checked for schema and Kyverno, never rendered as a whole.

- Use `flux-local` (8.4.0, maintained) to build every Flux Kustomization and template every
  HelmRelease with its real `valuesFrom` on both the base and the head commit.
- Post the rendered diff as a sticky PR comment, so the reviewer sees what Flux will apply,
  not just the file that changed.
- **Guard (blocking):** fail when an object that holds data — `PersistentVolumeClaim`,
  `StatefulSet`, CNPG `Cluster`, `Namespace` — disappears from the rendered output, or changes
  name or namespace. Override with a `allow-destructive` PR label, so the decision is visible.
- **Done when:** a test PR that renames a PVC fails, the same PR with the label passes, and the
  job's runtime is recorded.

### 3. NetworkPolicy port lint (tier 1, builds on 2) — #1418

The port trap has hit twice. With the rendered output from item 2, each `ports:` entry in an
ingress rule can be checked against the `containerPort`s of the pods the policy selects in the same
namespace.

- Fail when a port matches a Service `port` but no selected container's `containerPort`.
- Warn, not fail, when the selected pods declare no ports at all, since some containers listen
  undeclared (`1password` connect-api, Harbor metrics).
- **Done when:** both historical bugs, reconstructed as fixtures, fail the lint.

### 4. Nightly conformance probe (tier 2) — #1419

A CronJob, in its own namespace with its own least-privilege identity, that attempts what must
fail and verifies what must succeed. Each result is a metric; any regression alerts.

**Must fail:**
- From an unprivileged pod, connect directly to each proxy-auth app's pod, bypassing the edge
  (#1329/#1330).
- Send a forged `X-authentik-*` header through the edge and confirm the app does not see it.
- Reach `longhorn-backend:9500` from an unrelated namespace.
- Admit a `:latest` image, a privileged pod, and a pod without required labels (server-side
  dry-run).

**Must have happened:**
- Every deployed Kyverno policy has non-zero pass results.
- Every CronJob's last Job exited 0 inside its expected window.
- Every Velero backup since the last run created at least one PodVolumeBackup.

- **Done when:** each check has been shown to fail by breaking its precondition on purpose
  once, then restoring it.

### 5. AI review trial (tier 3) — #1420

Most PRs here are written by Claude. A Claude reviewer shares its blind spots; a reviewer from a
different model family does not.

- Enable GitHub Copilot code review on PRs, advisory only.
- Add `.github/copilot-instructions.md` pointing it at the hard constraints and `.claude/rules/`,
  so it does not flag correct local conventions.
- For one month, label each finding `review:useful` or `review:noise` on the PR.
- **Keep it if at least one third of findings are useful.** Otherwise turn it off and record why
  in the roadmap's rejected-options list.
- Not doing: several models on every push. That adds cost and noise, not coverage.

### 6. Hardening benchmark trend (tier 2) — #1421

- Run `kubescape` (v4.0.17) nightly against the NSA and CIS frameworks, read-only, and export
  the score and failed-control count as metrics with a Grafana panel.
- `kube-bench` (v0.16.0) runs once per node after each Kubernetes hop, since node configuration
  only changes then.
- Alert on a drop in score, not on the absolute number. The first run is the baseline, and each
  accepted exception is written down with its reason.

### 7. Scoped agent pen test and full audit (tier 4) — #1422

- A written scope first: targets, allowed techniques, rate limits, and what is out of bounds
  (DNS, Authentik account lockout, the Cloudflare WAF rules, anything destructive).
- Pass 1, external: from outside, through the edge only.
- Pass 2, assume-breach: from a pod in a throwaway namespace with default rights.
- Read-only by default. Findings become issues with a reproduction; nothing is fixed during the
  test.
- Quarterly, plus after any change to the edge (including the Gateway API migration).
- The full audit is pass 1 + pass 2 plus the item 6 report, compared against the 2026-08-19 audit.

### 8. Change classes, written down — #1423

Risky live work already follows "plan, daytime window, Scott watching". Write it down so it does
not depend on memory: a short rule in `.claude/rules/` that classifies changes (routine, data-bearing,
cluster-critical) and states what each class requires before it is applied.

## Deferred

- **Ephemeral staging cluster in CI (kind/k3d).** It cannot model Longhorn, Calico IPIP or
  MetalLB, which is where most incidents live. Revisit if item 2 still misses raw-manifest breakage.
- **Chainsaw end-to-end tests.** Item 4 covers the live behaviours that matter today, with less
  machinery.
- **Mutation testing of the test suites.** Worth doing once items 2–4 exist and have something to mutate.

## Cost

Items 2, 3, 6 and 8 are free to run. Item 4 is a CronJob. Item 5 depends on the Copilot plan in use.
Item 7 is the only one with a per-run model cost, and it runs quarterly.
