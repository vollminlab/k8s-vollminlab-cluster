# GitHub App Identity Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the user-owned GitHub credentials that automation depends on with an org-owned GitHub App identity, and add the liveness detection that would have caught their expiry.

**Architecture:** A GitHub App owned by the `vollminlab` organization issues short-lived (~1h) installation tokens. In-cluster, ESO's `GithubAccessToken` generator mints and refreshes them, so no GitHub credential is stored at rest. Outside the cluster, the `integrations/github` Terraform provider authenticates with the App's `app_auth` block. The App private key becomes the single long-lived secret, held in 1Password.

**Tech Stack:** External Secrets Operator v2.9.0 (`generators.external-secrets.io/v1alpha1`), 1Password Connect, OpenTofu + `integrations/github`, GitHub Actions, Flux CD.

**Date:** Drafted 2026-09-03.

---

## Context an implementer needs

### The finding that started this

`gh` stopped working on 2026-09-03. The cause was not a local config problem: the credential itself,
`Github-Org-PAT`, returns **401** straight from the vault. Testing every GitHub credential found
three dead ones:

| 1Password item | Live? | Actually used by |
|---|---|---|
| `Github-Admin-Token` | 200, expires **2027-04-27** | `github-admin` Terraform, via GitHub Actions |
| `Renovate-Org-PAT` | 200, expires **2027-04-11** | `renovate/renovate-token` ExternalSecret |
| `Github-Org-PAT` | **401** | the local `gh` CLI on devsbx01 — replaced 2026-09-03 by `Github-CLI-PAT-devsbx01`, old token deleted on GitHub |
| `FluxCD-GitHub-PAT` | **401** | nothing — legacy, superseded by the Flux GitHub App |
| `Renovate Bot PAT` | **401** | nothing — legacy, superseded by `Renovate-Org-PAT` |

`Github-Admin-CI Token` is **not** a GitHub credential — it is a 1Password *service account* used by
the `github-admin` Actions workflows. Do not include it in GitHub token sweeps.

### There is no such thing as an org-owned PAT

This is the load-bearing fact. Both live tokens were measured with `GET /user`:

```
Github-Admin-Token   login=svollmi1  type=User  id=14255291
Renovate-Org-PAT     login=svollmi1  type=User  id=14255291
```

The `-Org-` in the names is aspirational. GitHub fine-grained PATs are **always owned by a user**;
an organization can approve or revoke their *access*, but the credential belongs to the person. So:

- **Bus factor.** One personal account is a single point of failure for renovate *and* for the
  Terraform that manages branch protection on 13 repositories.
- **Attribution is cosmetic.** Renovate sets `gitAuthor: Renovate Bot <bot@renovateapp.com>`, but
  every push authenticates as `svollmi1`. The audit log cannot distinguish renovate from a human.
- **Silent revocation.** An org admin can revoke a PAT's org access without touching the account,
  and nothing would report it until something 401s.

Flux is the counter-example and the proof the fix works: its secret holds `githubAppID`,
`githubAppInstallationID`, `githubAppPrivateKey`. It is org-owned, and it was the one GitHub
consumer that did not break.

### Traps

**`Issues: write` is required, and is not implied.** `github_repository_milestone` uses the Issues
API. A fine-grained PAT does **not** inherit that from the repo admin role. Without it, *plan passes*
(plan only reads) and *apply fails 403* on **every** resource in the module. This already happened —
github-admin#29, reverted by #30. The App must be granted `Issues: write` explicitly. Expect the
same failure shape if it is missed.

**`github-admin` does not run in-cluster.** It runs in GitHub Actions (`plan.yml` on PR, `apply.yml`
on push to main), pulling credentials at runtime via `1password/load-secrets-action@v4`. It is
**not** a tofu-controller module, so the `approvePlan: auto` auto-apply hazard and
`scripts/check-tofu-provider-approval.py` do **not** apply to it. Changing its auth is a normal PR.

**The ESO generator is `v1alpha1`.** `githubaccesstokens.generators.external-secrets.io` is present
in ESO v2.9.0 and its required spec is `appID`, `installID`, `auth.privateKey.secretRef`, with
optional `permissions` and `repositories`. It is the least stable API surface in ESO — pin it and
re-verify on any ESO major bump.

**All three PATs expire inside a three-week window in April 2027.** Whatever is left on PATs after
this migration must be staggered, or one quiet week next spring takes out everything at once.

**Do the detection first.** Three credentials died with nothing noticing, and it surfaced only
because a CLI broke by chance. Detection is the cheapest item here and it is what verifies every
later phase. Issue #1188 already tracks this.

---

## Decisions needed before starting

- [ ] **One App or several?** Separate Apps per consumer (renovate, github-admin) give cleaner
  audit-log actors and tighter per-App permissions, at the cost of more moving parts. Reusing
  `flux-sync-app` is fewest parts but makes one key the blast radius for everything.
  *Recommendation: a second App for renovate, a third for github-admin; leave Flux alone.*
- [ ] **Does `gh` stay a PAT?** A GitHub App cannot back the `gh` CLI without re-minting hourly.
  A personal credential is genuinely correct for a laptop CLI. *Recommendation: keep it a PAT,
  rename the vault item so it stops implying org ownership, and set a 366-day expiry staggered
  away from April.*

---

## Phase 0 — Unblock `gh` (5 minutes, do first, independent of everything else)

- [ ] Regenerate a fine-grained PAT on GitHub for CLI use; expiry 366 days, **not** April 2027
- [ ] Store it in 1Password renamed to **`Github-CLI-PAT-devsbx01`**. `Github-Org-PAT` was the
      name that caused this confusion; keying on the host says plainly that it is a machine-local
      personal credential, and names the box to re-key if devsbx01 is compromised. Nothing
      automated references the old name, so the rename is safe
- [ ] **Name the GitHub token identically to the 1Password item** (`Github-CLI-PAT-devsbx01`). Two
      names for one credential is how `Github-Org-PAT` drifted from what it was into what people
      assumed it was
- [ ] Resource owner **`vollminlab`**, access **All repositories**. Note that resource owner is not
      ownership: the credential still lives in the user's personal settings and authenticates as
      `svollmi1`. Selecting the org only decides what it can *reach*
- [ ] Permissions — GitHub's summary view renames these, so match on the picker's labels:
      **Contents** (shown as "code") R/W, **Issues** R/W, **Pull requests** R/W, **Workflows** R/W,
      **Actions** Read, **Metadata** Read (auto).
      **`Workflows: write` is required** — pushes touching `.github/workflows/` are rejected without
      it. Omit `Secrets` unless `gh secret` is actually used, and omit `Checks` — the previous token
      had neither Checks nor any problem running `gh pr checks`
- [ ] Restore it without abandoning the pattern — plain `gh auth login` silently replaces it with a
      personal OAuth token:
      `op read "op://Homelab/Github-CLI-PAT-devsbx01/password" | gh auth login --with-token -h github.com`
- [ ] Verify: `gh pr list` in this repo returns results

## Phase 1 — Credential liveness detection (issue #1188)

Do this **before** any migration, so later phases have a working verifier.

- [ ] Write a CronJob that reads each credential from 1Password and probes its provider
      (`GET /user` for GitHub PATs; App installation for the Apps), exporting a metric per item
- [ ] Include the token's own `github-authentication-token-expiration` header as a days-remaining
      gauge — a dead token returns no such header, so absence is itself the alert signal
- [ ] Alert on both: unreachable/401 **and** expiry under 30 days
- [ ] Cover the non-GitHub credentials too, at minimum the `Github-Admin-CI` 1Password service
      account, whose failure would silently stop all `github-admin` applies
- [ ] Verify by pointing it at a known-dead item (`FluxCD-GitHub-PAT`) before retiring that item —
      a probe that has never gone red has not been tested
- [ ] **Reconcile against GitHub, not just the vault.** The probe reads items from 1Password, so it
      is blind to any token that exists on GitHub but was never recorded there. List the org's
      fine-grained tokens and diff that against the vault; anything present on GitHub and absent
      from 1Password is either undocumented or forgotten, and both need resolving. 1Password is a
      record of what someone remembered to save; GitHub is the authority on what exists.

## Phase 2 — Renovate onto a GitHub App (highest value)

- [ ] Create the App in the org; grant **only** what renovate needs, and scope the installation to
      the five managed repositories, not all repos
- [ ] Store the App private key in 1Password; materialize it into the `renovate` namespace with an
      ExternalSecret (PEM newline handling is a known trap — validate with `head -3`)
- [ ] Add a `GithubAccessToken` generator with `appID`, `installID`, `auth.privateKey.secretRef`,
      plus `permissions` and `repositories` narrowed to the five repos
- [ ] Point the existing `renovate-token` ExternalSecret at the generator via `sourceRef.generatorRef`
      instead of the 1Password `remoteRef`
- [ ] Add both new files to the directory `kustomization.yaml` — Flux uses explicit lists
- [ ] **Keep `Renovate-Org-PAT` in place until proven.** Roll back by reverting the ExternalSecret
- [ ] Verify: one full renovate run succeeds, and the PRs it opens are attributed to the App actor
      rather than `svollmi1`. Attribution is the whole point — check it, do not assume it

## Phase 3 — `github-admin` Terraform onto a GitHub App

- [ ] Create the App with repo administration permissions **and `Issues: write`** (see Traps)
- [ ] Replace `provider "github" { token = var.github_token }` in `terraform/versions.tf` with the
      provider's `app_auth` block (`id`, `installation_id`, `pem_file`)
- [ ] Update `plan.yml` and `apply.yml` to load the App key from 1Password instead of
      `op://Homelab/Github-Admin-Token/password`
- [ ] **Verify on a milestone resource specifically.** Plan passing proves nothing here — plan only
      reads. The #29 failure appeared only at apply
- [ ] Verify: a no-op apply completes clean, and branch protection on all 13 repos is unchanged

## Phase 4 — Retire the dead credentials

Only after Phase 1's probe is live and Phases 2–3 are proven.

Retiring a credential is **two** actions: delete the 1Password item *and* revoke the token on
GitHub. Doing only the first leaves a live-or-dead credential in the org with no record of what it
was for — the worst of both states. Revoking a dead token is risk-free by construction: it already
returns 401, so nothing can depend on it that is not already broken.

- [ ] Revoke on GitHub **and** delete from 1Password: `FluxCD-GitHub-PAT` (dead, unused — Flux uses
      its App)
- [ ] Revoke on GitHub **and** delete from 1Password: `Renovate Bot PAT` (dead, unused — superseded)
- [ ] Revoke the **old** `Github-Org-PAT` on GitHub after Phase 0 replaces it — the vault item is
      reused for the new token, so this one is a GitHub-side revoke only
- [ ] Revoke and delete `Renovate-Org-PAT` and `Github-Admin-Token` once their App replacements have
      run clean through at least one full cycle each
- [ ] Add "Referenced by ExternalSecret — do not rename fields" notes to every surviving item
- [ ] Re-run the Phase 1 probe and confirm it reports a clean, complete inventory

---

## What this does not solve

Rotation is not detection. If an App key is revoked, the failure is still a silent 401 — Phase 1 is
what makes that visible, and it is the only phase that is strictly required. Phases 2–4 reduce blast
radius and fix ownership; Phase 1 is what stops the next one going unnoticed for months.
