# Terraform provider bumps

How to land a provider version bump on a tofu-controller module, and why it is not
just "merge the Renovate PR".

## Why there is a procedure at all

All 11 `Terraform` CRs run `approvePlan: auto` on a 10-minute interval. That is the
right default for ordinary config changes — the PR review is the review, and the
apply follows the merge like any other GitOps change.

It is the wrong default for a *provider* bump. A new provider version can rewrite
resources it merely re-reads, and CI cannot tell you: it runs `tofu validate`, never
`tofu plan`. `validate` proves the config parses, not what the upgrade will do.

Two things keep that from applying itself:

- **Committed lockfiles** (`.terraform.lock.hcl`, all 11 modules) pin the exact
  provider version, so a new release cannot be picked up without a commit.
- **`scripts/check-tofu-provider-approval.py`**, wired into the `Validate Terraform
  Modules` CI job, fails any PR touching `versions.tf` or `.terraform.lock.hcl` on a
  module whose CR is `approvePlan: auto`.

So a Renovate provider PR arrives **blocked by design**. Unblocking it is this
procedure.

## The procedure

### 1. Flip that module to manual

In the module's `terraform-cr.yaml`:

```yaml
  approvePlan: ""      # was: auto
```

Merge that first, on its own. The guard now passes for the bump PR, and the
controller plans without applying.

### 2. Merge the bump

Renovate's PR. On merge the controller plans with the new provider and **holds**.

```bash
kubectl get terraform -n tofu <module>-config \
  -o jsonpath='{.status.plan.pending}{"\n"}'
```

`<module>-config` — the CR is not named after the module alone. `kubectl get
terraform -A` if unsure; a wrong name returns NotFound, and a jsonpath poll against
a missing object reports an empty plan forever, which reads as "no changes".

### 3. Read the plan

The controller does **not** log plan contents — only `plan: ok, found drift:
true|false`. Extract and render it yourself:

```bash
kubectl get secret -n tofu tfplan-default-<module>-config \
  -o jsonpath='{.data.tfplan}' | base64 -d | gunzip -c > tfplan
tofu show -no-color tfplan
```

**Plan files are not portable across tofu versions.** The runner writes 1.12.1; a
1.12.6 `tofu show` refuses outright. Download the matching binary. To get provider
schemas, `tofu init -backend=false` in a copy of the module — no state or
credentials needed.

Read for `must be replaced` and `will be destroyed`, not just the summary line.

### 4. Approve

**Not through git.** The pending plan is named `plan-<branch>-<source sha>` and is
regenerated on *every* commit to this repo, so an approval PR is invalidated by its
own merge commit — it names a plan that no longer exists. This was tried in #1145
and the apply was held.

```bash
P=$(kubectl get terraform -n tofu <module>-config -o jsonpath='{.status.plan.pending}')
kubectl patch terraform -n tofu <module>-config --type=merge \
  -p "{\"spec\":{\"approvePlan\":\"$P\"}}"
```

The controller applies within seconds. Flux then restores the file's value, which
re-arms the hold — that revert is the mechanism, not a race.

### 5. Flip back to auto

Manual is a state you enter to do this and leave afterwards. Leaving a module on
`""` means every ordinary change waits for an out-of-band patch, and the repo
averages ~22 terraform commits a month.

## Watch for a perpetual diff

If the plan comes back with the *same* diff after a successful apply, the provider
is reading back a field the config does not set, storing it in state, and diffing
state against config forever.

Fix it by declaring the field, not by ignoring it. Authentik's
`allowed_redirect_uris` did this: the API returns `redirect_uri_type`, the config set
only `matching_mode` + `url`, and provider 2026.5.1 types the attribute as
`list(map(string))` so nothing suppressed the difference. Adding
`redirect_uri_type = "authorization"` — the value already live on all 14 URIs, and
the API's own default — converged it without an apply (#1150).

Under `auto` a perpetual diff rewrites the resources every interval, which is what
the authentik WAL bloat was. Under manual it holds a plan that never clears, hiding
any real change behind standing noise.
