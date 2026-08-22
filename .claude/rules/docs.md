# Documentation Currency Rules

Two documents in this repo describe live infrastructure, and both silently rotted until a
2026-08-22 audit. `docs/roadmap.md` had six wrong statuses; `docs/cluster-reference.md` — the
"Full configuration reference" CLAUDE.md points every session at — was missing **46 of 93 app
directories** and still listed Plex, Overseerr, Tautulli and sealed-secrets as running.

## The two rules

**1. A new app directory updates `docs/cluster-reference.md` in the same PR.**

`cluster-reference.md` is the *inventory*: what exists, at what version, on what port, with what
PVC. Every `clusters/<namespace>/<app>/` directory belongs in it. CI enforces this — see below.

**2. A PR that changes what a roadmap phase _means_ updates `docs/roadmap.md` in the same PR.**

Completing a phase, dropping one, splitting one, or invalidating its premise. **Not** every
deployment. `roadmap.md` is the *plan*: goals, rationale, rejected options and their reasons,
sequencing constraints. Most app directories here were never on a plan — `longhorn-trim`,
`velero-pvb-healer`, `longhorn-mount-healer` and `vcenter-credential-age` are all incident
responses. Retroactively roadmapping them fabricates a plan that never existed and turns the
roadmap into a second copy of the reference, which is the duplication that caused this.

## Which document does a change belong in?

| Change | Reference | Roadmap |
| --- | --- | --- |
| New app deployed | **yes** | only if it completes a phase |
| App removed | **yes** | only if it changes a phase's meaning |
| Version/port/PVC changed | **yes** | no |
| Incident-response CronJob added | **yes** | no |
| A phase is finished, dropped or split | no | **yes** |
| An approach is rejected | no | **yes** — record *why*, or it gets re-proposed |

## `scripts/check-doc-currency.sh`

Runs unconditionally on every PR as a step in the existing CI job — no new required check, no
tooling, no cluster access. Three checks:

1. **Every app directory appears in `cluster-reference.md.`** Baselined in
   `docs/.cluster-reference-exempt`, seeded with the 46 already missing so the check could land
   without a mass-documentation PR. **That file is a ratchet and must only shrink.** Adding a new
   app to it to silence CI defeats the check.
2. **Retired components stay retired.** `docs/.retired-components` lists removed components; each
   must not reappear as a live app directory, and must not appear in an *inventory table row* in
   `cluster-reference.md`. Prose is not checked — the note that `bootstrap/sealed-secrets/` is
   kept as historical DR reference is correct and should stay.
3. **`roadmap.md` carries a `Last verified against the live cluster: YYYY-MM-DD` line**, warned at
   90 days and failed at 180.

## Why the check is not path-filtered

**The drift comes from the _other_ file changing.** Plex was deleted from `clusters/` on
2026-05-09 and both documents went on describing it for over three months. A check gated on
`docs/**` would never have fired — inert in exactly the case it exists for, which is the same
failure as a Kyverno rule whose `foreach.list` resolves to null and reports zero fails because it
evaluates nothing.

## What the check cannot catch

**Prose.** Four of the six stale roadmap statuses were sentences, not status fields: "deployed
alongside Plex", a section titled "Tautulli / Plex Metrics Dashboard", "SealedSecret changes
trigger restarts". No string comparison finds those.

Check 3 is the only defence, and it is a prompt for a human to re-read — not a test. When that
warning fires, re-read both documents against the live cluster and move the date. Do not move the
date without doing the reading.

## How the drift happened, so it is recognisable next time

**Every wrong field described something that was _replaced_ rather than removed** — Plex by
Jellyfin, Tautulli by Jellystat, SealedSecrets by ESO. The replacement got documented in a new
section while the old section kept its `done`. `roadmap.md` section 3.7 said "SealedSecret changes
trigger restarts" while 3.8, four sections later, described removing the controller. Both were
written by someone who knew.

**A `done` status records that something was built, not that it is still running.** Sections
describing live infrastructure need verifying against the cluster, not against the PR that shipped
them.
