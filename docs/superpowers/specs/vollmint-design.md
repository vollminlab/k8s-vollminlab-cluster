# vollmint — Budget & Finance Tracker Design

**Created:** 2026-07-22
**Status:** Approved design, pending implementation plan
**Owner:** Scott

## What and why

vollmint is a self-hosted budget/finance web app for the vollminlab cluster. Goals, in priority order:

1. See where money actually goes (category breakdown, per-charge drill-down)
2. Find recurring charges worth cutting
3. Surface vice/impulse spending explicitly
4. Understand paycheck allocation and whether it can improve

Single household, four filter views: **Scott / Nikki / Joint / Household**. Single login (Scott) via Authentik forward-auth; the views are UI filters, not user accounts.

## Decisions (settled during brainstorming)

| Decision | Choice |
|----------|--------|
| Build vs adopt | Custom build (Go API + React/TS SPA) |
| Bank ingestion | SimpleFIN Bridge ($1.50/mo, rides MX; read-only protocol, no money movement possible) |
| Venmo ingestion | CSV upload in v1 (Venmo has no personal API; not a SimpleFIN institution — verified live); Gmail receipt ingestion is the v1.1 headline |
| Runtime shape | One image, two entrypoints: `vollmint serve` Deployment + `vollmint sync` CronJob (shape B) |
| Database | CNPG Postgres only — no Redis (trivial data volume, nothing to cache) |
| Users/auth | Scott only, domain-wide Authentik forward-auth; LAN/Tailscale only, never Cloudflare tunnel |
| v1 feature scope | Budget targets per category + the four core reports; everything else → v1.1 backlog |
| Investments (Wealthfront, Fidelity, Vanguard 401k/IRA) | Deferred to v1.1 (balances-only net-worth view) |
| Mortgage | Nikki pays it; Scott's Zelle payment to her is the tracked outflow → category rule `Housing: Mortgage`. No servicer link |
| UI layout | Single-page dashboard (layout A) + drill-down; secondary pages: Transactions, Budgets |
| Deployment | Repo-standard custom-app pattern: own Helm chart in app repo, image + chart pushed to Harbor, HelmRelease via `chartRef` → OCIRepository (shlink-ingress-controller / longhorn-rebalancing-controller precedent) |

## Verified facts (no assumptions)

- SimpleFIN Bridge supports every institution in play — verified live on the bridge institution search: **Ally Bank**, **Chase Bank** (checking/savings/card under one login), **Discover Bank Account** + **Discover Credit Card**, **Wealthfront**, **Fidelity Investments**, **Vanguard** (+ "Vanguard Retirement - Ascensus")
- Venmo is **not** a SimpleFIN institution (search returns nothing) and has no official personal API; desktop-web CSV export, max 90 days per file, ~3 years of history — this is the backfill path
- SimpleFIN protocol: setup token → POST claim URL → **Access URL** (embedded HTTP Basic auth = the only credential); `GET /accounts` with `start-date`, `end-date`, `pending=1`; 90-day max window per request; ≤24 requests/day guidance; data refreshes ~once/24h upstream
- No maintained Go SimpleFIN client exists; the client is ~100 lines (one POST, one GET, JSON decode). Actual Budget uses the same bridge (prior art)

## Architecture

```
GitHub: vollminlab/vollmint (new repo — Go API + React SPA + Dockerfile + Helm chart)
  └─ ARC runner CI: test → build → trivy scan → push BOTH artifacts to Harbor:
       harbor.vollminlab.com/vollminlab/vollmint:vX.Y.Z            (image)
       harbor.vollminlab.com/vollminlab/charts/vollmint:X.Y.Z      (chart, OCI)

k8s-vollminlab-cluster (this repo) — namespace: vollmint  (labels app/env/category=apps)
  ├─ Deployment  vollmint        `vollmint serve` :8080 — REST API + embedded SPA (go:embed)
  │                              stateless, no PVC, strategy Recreate, 1 replica
  ├─ CronJob     vollmint-sync   `vollmint sync` — 06:10 & 18:10 UTC (SimpleFIN refreshes ~daily;
  │                              two runs bound staleness; well under 24 req/day)
  ├─ CNPG        vollmint-db     sibling dir vollmint-db/app/ (shlink-db pattern, plain CR,
  │                              never helm-templated) — 2 instances, 5Gi Longhorn each,
  │                              scheduled base backup → MinIO (authentik pattern)
  ├─ Ingress     vollmint-ingress  vollmint.vollminlab.com → svc :8080
  │                              Authentik forward-auth annotations + auth-snippet
  │                              shlink.vollminlab.com/slug: vollmint → vollm.in/vollmint
  │                              (+ akshell Application entry, provider_id=None)
  └─ ExternalSecret vollmint-simplefin  ← 1P item "SimpleFIN Access URL" (field: token)
```

### Flux wiring (cluster repo)

- `flux-system/repositories/vollmint-ocirepository.yaml` — `metadata.name: vollmint-repo`, url `oci://harbor.vollminlab.com/vollminlab/charts/vollmint`, pinned `spec.ref.tag`, `secretRef: harbor-vollminlab-pull`; listed in `repositories/kustomization.yaml`
- `flux-system/flux-kustomizations/vollmint-kustomization.yaml` — `metadata.name: vollmint-vollmint`; listed in `flux-kustomizations/kustomization.yaml`
- HelmRelease uses `chartRef` → the OCIRepository, `valuesFrom` → ConfigMap `vollmint-values` (no inline values)
- Both index files updated in the same PR, alphabetized

### Security model

- **The serve process never sees the SimpleFIN credential.** The `vollmint-simplefin` Secret is mounted as an env var in the sync CronJob **only**. Compromise of the web app cannot reach the bank feed; the feed itself is read-only by protocol anyway.
- LAN/Tailscale exposure only — no Cloudflare tunnel route, ever.
- Authentik forward-auth on the Ingress (domain-wide `vollminlab-forward-auth`); akshell Application entry required.
- ESO + 1Password for all secrets; CNPG generates its own app credentials (`vollmint-db-app`).
- Uploaded Venmo CSV files are parsed and discarded; only normalized rows + the raw record (jsonb) persist.
- NetworkPolicy: default-deny + allow-dns; ingress allows nginx→8080, monitoring→metrics, cnpg-operator→5432/8000; egress allows CNPG 5432 and HTTPS 443 (sync job → SimpleFIN). Container ports verified per `networkpolicy.md` before the PR.

## Data model (Postgres)

- **`accounts`** — simplefin_id, name, org, currency, latest balance + date, **`owner`** (`scott` | `nikki` | `joint`), active flag. Upserted every sync.
- **`transactions`** — the core table:
  - `source` (`simplefin` | `venmo_csv`) + `external_id`, **unique (source, external_id)** → idempotent syncs and re-uploads
  - `posted` date, `amount numeric(12,2)` (SimpleFIN string decimals parsed exactly — never floats), `description`, `payee` (normalized), `pending`
  - `category_id` (nullable → uncategorized queue), `owner_override` (nullable — rare per-charge reattribution on joint accounts)
  - `transfer_peer_id` (nullable self-reference) — transfer pairing
  - `raw` jsonb — original record kept for re-processing
- **`categories`** — name, optional parent, `kind` (`spend` | `income` | `transfer` | `savings`), **`is_vice`** flag (drives the vice report directly)
- **`category_rules`** — priority-ordered payee matchers (substring/regex) → category; applied at ingest, re-runnable over history; created from the UI ("always categorize this payee as…")
- **`budgets`** — (category, month, target amount)
- **`sync_runs`** — one row per sync/import: window, rows upserted, status, error. Powers "last synced" and gives alerts something to point at.

Views are filters: a transaction's effective owner = `owner_override` ?? account `owner`; Household = all.

**Ingestion never deletes.** Rows are inserted or updated only; a bad sync cannot destroy history.

### Transfer matching (one matcher, three cases)

1. **Ally ↔ Venmo**: bank-funded Venmo payments appear twice — Ally ACH debit ("VENMO PAYMENT", amount only) + Venmo CSV row (real payee/note). Matcher pairs by amount + posted date ±3 days; Ally side becomes `kind=transfer`, Venmo side carries the category. Unpaired Ally VENMO debits **stay counted as spend** in a "needs Venmo detail" bucket — totals never understate; the UI nags for a CSV upload. Balance-funded Venmo payments exist only in the CSV; no pairing needed.
2. **Credit-card payments**: card purchases are the spend at purchase time; the checking → Chase/Discover payment is a transfer (same matcher, card-payment descriptors). Prevents double-counting.
3. **Future Nikki accounts**: if her accounts are linked later (same SimpleFIN subscription, her credentials, zero schema change), the Zelle mortgage payment upgrades from plain spend to a transfer pair automatically.

### Sync mechanics (CronJob)

1. `GET /accounts?start-date=<last successful run − 7 days>&pending=1` — the overlap re-fetches recent history so late-posting/corrected transactions self-heal via upsert
2. Upsert accounts + transactions; a pending row that posts under a new id reconciles by amount + date match; stale pending rows swept after 14 days
3. Apply category rules to new rows; run transfer matching; write `sync_runs`
4. Any error → non-zero exit → failed Job → existing KSM CronJob alerting (no new alert plumbing)

Manual sync: `kubectl create job --from=cronjob/vollmint-sync ...` — deliberately **not** an API endpoint, because the serve pod has no SimpleFIN credential.

### Venmo CSV upload

Authenticated endpoint in the SPA. Header-drift tolerant (columns matched by name, not position — Venmo's export format drifts). Dedupe on Venmo's own transaction ID. Reports "N new / M duplicates skipped." Backfill = ~12 exports (90-day max per file, ~3 years available).

## UI (layout A — locked)

Single-page dashboard; month pager; view switcher chips (Scott / Nikki / Joint / Household):

- Summary cards: In / Out / vs Budget / **Vices**
- Spend by category (bars) · budget progress bars
- Recurring charges panel (detected by payee + cadence + amount stability; flags **new** recurrences)
- Needs-attention panel: uncategorized count, Ally-VENMO-awaiting-CSV count, last-sync status

**No number is a dead end** — category bars, vice card, budget rows, and recurring entries all deep-link into the Transactions page pre-filtered (category + month + view). Transaction rows: re-categorize, create an "always…" rule, owner override; rollups update immediately.

Secondary pages: **Transactions** (filter/search/categorize/CSV upload), **Budgets** (targets per category per month).

## API surface (all under forward-auth)

```
GET  /api/summary?view=&month=          dashboard rollups
GET  /api/transactions?view=&month=&category=&account=&q=&uncategorized=
PATCH /api/transactions/{id}            category, owner_override
GET/POST/DELETE /api/rules              payee matcher → category (+ re-run over history)
GET/POST/PATCH  /api/categories
GET/PUT  /api/budgets?month=
GET  /api/recurring?view=&month=
POST /api/imports/venmo                 multipart CSV
GET  /api/sync/status                   last sync_runs rows
GET  /healthz                           (unauthenticated, for probes)
GET  /metrics                           Prometheus (scraped via netpol allow)
```

## Error handling & observability

- Sync: per-account failures recorded in `sync_runs` (status `partial`) and logged; whole-run failure exits non-zero → KSM alert. SimpleFIN 402/403 (lapsed subscription / revoked access) produces a distinct error message in `sync_runs` surfaced on the dashboard.
- CSV import: per-row errors reported back to the UI (row number + reason); import is all-or-nothing per file (single transaction).
- Metrics: sync duration/rows/last-success timestamp, HTTP basics. Grafana panel later if wanted — the dashboard's own needs-attention panel is the primary surface.

## Testing

- Go unit tests: SimpleFIN response parsing (string-decimal amounts), Venmo CSV parser (golden files for known header variants), transfer matcher (Ally↔Venmo, CC payments, edge: same-amount-same-day pairs), category rules engine
- DB layer tested against real Postgres in CI (ARC runner service container)
- Frontend: component tests for the drill-down filter plumbing; no e2e suite in v1
- Cluster manifests: existing kyverno-cli CI in this repo

## CI/CD (vollmint repo)

Follows longhorn-rebalancing-controller precedent:

1. PR: lint + test (Go + frontend), image build (multi-stage: React build → `go:embed` → distroless), Trivy scan
2. Tag `vX.Y.Z`: push image `vollminlab/vollmint:vX.Y.Z` + Helm chart `vollminlab/charts/vollmint:X.Y.Z` to Harbor
3. Deploy = bump `spec.ref.tag` in `vollmint-ocirepository.yaml` here (Renovate can automate later)

## Rollout checklist (implementation plan will expand)

1. SimpleFIN Bridge subscription; link Ally, Chase, Discover; claim Access URL → 1P item **"SimpleFIN Access URL"** (field `token`, Homelab vault, tag Homelab, "Referenced by ExternalSecret" note) — **save before wiring**
2. New repo `vollminlab/vollmint` + Obsidian vault org checklist (docs/, sync script repos list, graph color)
3. Longhorn capacity check (`storage.md`) before committing the 2×5Gi CNPG PVCs
4. Cluster PR: namespace, Flux wiring (both indexes), CNPG cluster, HelmRelease + values ConfigMap, ExternalSecret, Ingress, NetworkPolicy, homepage tile (+ `app.kubernetes.io/name` label)
5. akshell Application entry for vollmint (provider_id=None)
6. Venmo CSV backfill (~12 exports), category seeding, budget targets

## v1.1 backlog

- Account balances / net-worth trend (link Wealthfront, Fidelity, Vanguard, balances-only sync)
- Venmo Gmail receipt ingestion (read-only, label-scoped) to replace routine CSV uploads
- Pushover alerts + weekly digest (new recurring charge detected, budget threshold crossed)
- LLM-assisted categorization for the uncategorized queue
- Nikki as a real Authentik user; her personal accounts linked
- Mortgage balance / servicer link if ever wanted

## Out of scope (deliberate)

- Unofficial Venmo API (ToS/credential risk — hard pass)
- Money movement of any kind (SimpleFIN is read-only by protocol)
- Multi-tenant auth, public exposure, Redis, split microservices
