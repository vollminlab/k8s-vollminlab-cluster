# vollmint plan-6: Balance Snapshots + Net Worth — Design

Created: 2026-08-07
Status: Approved (pending final spec review)
Repo: `vollmint` (implementation), deployed via `k8s-vollminlab-cluster` Flux

## Overview

vollmint currently stores only the *latest* balance per account (`accounts.balance` /
`accounts.balance_date`, refreshed by every SimpleFIN sync at 06:10/18:10 UTC). Nothing
retains history, so there is no way to draw a Monarch-style net-worth or per-account
balance graph. SimpleFIN serves current balances only — history cannot be backfilled
later, so every day without capture is a day of graph lost.

Plan-6 adds:

1. **Daily balance snapshots** captured inside the existing sync.
2. **Manual accounts** for assets/liabilities SimpleFIN can't feed (401k until linked,
   home value estimate, mortgage principal), flowing through the same snapshot pipeline.
3. **A "Net Worth" page** with a net-worth line, per-account drill-down, and manual
   balance editing.

Anything Scott later links in SimpleFIN Bridge (401k custodian, mortgage servicer)
joins the pipeline automatically on link day — no code depends on linking timing. A
manual account covering the same asset is then flipped `active = false`.

## Goals

- One snapshot row per account per day; evening sync overwrites morning (upsert).
- Net worth = sum of all account balances (liabilities are negative balances, as with
  credit cards today).
- Manual accounts creatable and updatable from the UI; balances snapshot immediately.
- The existing view switcher (Scott / Nikki / Joint / Household) filters the page using
  the same owner semantics as existing endpoints.

## Non-goals

- No backfill from transaction walk-back (rejected: Discover exposes ~2 weeks of
  history, Venmo has no balance — reconstructed balances would be wrong).
- No automatic home-value estimation (Zillow etc.). Manual entry only.
- No per-account snapshot editing/correction UI.
- No deletion of manual accounts — deactivate (`active = false`) instead.

## Data model — migration `0004_balance_snapshots.sql`

```sql
CREATE TABLE account_balance_snapshots (
    account_id    text NOT NULL REFERENCES accounts(id),
    snapshot_date date NOT NULL,
    balance       numeric(14,2) NOT NULL,
    PRIMARY KEY (account_id, snapshot_date)
);

ALTER TABLE accounts ADD COLUMN is_manual boolean NOT NULL DEFAULT false;
```

- No index beyond the PK — all queries are keyed by `account_id, snapshot_date`.
- Manual account ids: `manual-<slug>` where slug is derived from the name
  (lowercase, non-alphanumerics → `-`). Creation returns 409 on id collision.
- Manual accounts set `org = 'Manual'`, `currency = 'USD'`, and use the existing
  `owner` column (`scott` / `joint` / `nikki`), so all owner/view filtering works
  unchanged. The sync never touches them: it upserts by SimpleFIN account id, and
  `manual-*` ids never appear in the SimpleFIN payload (the standalone `venmo` row
  already proves this coexistence).

## Snapshot capture

In the sync, immediately after `UpsertAccounts` succeeds, run one statement:

```sql
INSERT INTO account_balance_snapshots (account_id, snapshot_date, balance)
SELECT id, balance_date, balance
FROM accounts
WHERE balance IS NOT NULL AND balance_date IS NOT NULL
ON CONFLICT (account_id, snapshot_date)
    DO UPDATE SET balance = EXCLUDED.balance;
```

Properties:

- **Keyed on SimpleFIN's `balance_date`**, not "today" — a stale account (bank feed
  broken) keeps writing to its last real date instead of fabricating fresh data.
- Idempotent; the 18:10 sync overwrites the 06:10 row for the same date.
- Venmo (NULL balance) is skipped by the `WHERE` clause.
- Manual accounts are also swept up by this statement (their `balance_date` is the
  date of last manual edit) — harmless re-upsert of the same value.
- **A capture failure logs an error and does not fail the sync.** Transaction
  ingestion is never held hostage by the snapshot write.

When a manual balance is edited via the API, the handler sets
`accounts.balance = $1, balance_date = CURRENT_DATE` and upserts the snapshot row for
`CURRENT_DATE` in the same transaction, so the graph reflects the edit immediately
rather than after the next sync.

## API

All endpoints live alongside the existing handlers; `view` and error-shape conventions
match the existing API exactly.

### `GET /api/networth?view=<view>&range=<range>`

- `view`: same values and owner-filter semantics as existing endpoints
  (scott / nikki / joint / household).
- `range`: `1m` | `3m` | `6m` | `1y` | `all` (default `3m`).
- Response:

```json
{
  "series": [
    { "date": "2026-08-01", "total": "99327.87",
      "accounts": { "ACT-…": "26878.63", "manual-401k": "412000.00", "…": "…" } }
  ],
  "accounts": [
    { "id": "manual-401k", "name": "401k", "owner": "scott",
      "is_manual": true, "balance": "412000.00", "balance_date": "2026-08-07" }
  ]
}
```

- The series is a continuous daily date axis (`generate_series` over the range).
  Per account, the value on each date is the **last known snapshot on or before that
  date** (carry-forward). Dates before an account's first snapshot contribute nothing
  for that account — no fabricated history.
- Only `active = true` accounts are included.
- Query lives in `internal/report` next to the existing trends/insights queries.

### `POST /api/accounts/manual`

Body: `{ "name": "401k", "owner": "scott", "balance": "412000.00" }`
Creates the account row (`is_manual = true`, `balance_date = CURRENT_DATE`) and its
first snapshot in one transaction. Validates: non-empty name, owner in the allowed
set, balance parses as numeric (negative allowed — that's a liability). 409 if the
derived id already exists.

### `PUT /api/accounts/{id}/balance`

Body: `{ "balance": "415250.00" }`
400 unless the account has `is_manual = true` (synced accounts are owned by the sync;
an edit would be silently overwritten and mislead). Updates balance + `balance_date`,
upserts today's snapshot, same transaction.

## UI — new "Net Worth" page

New nav entry alongside the existing pages; own route.

- **Net-worth line chart** for the selected view, with a range selector
  (1M / 3M / 6M / 1Y / All). Reuses the Trends chart idiom: fixed-size chart (the
  established workaround for ResponsiveContainer under jsdom).
- **Assets / Liabilities summary** above the chart: current totals split by balance
  sign, plus net worth.
- **Account list** below: each row shows name, owner badge, current balance, and
  as-of date. Clicking a row swaps the chart to that account's own line (click again
  or "All" to return to the net-worth line).
- **Manual account controls**: manual rows get an inline balance edit (input + save →
  `PUT`); an "Add manual account" form (name, owner, starting balance) → `POST`.
  Stale manual balances (balance_date > 60 days old) show a subtle "update?" hint.
- The Scott/Nikki/Joint/Household view switcher filters the whole page.

## Error handling

- Snapshot capture failure in sync: logged, sync continues (see above).
- `GET /api/networth` with no snapshots yet: empty `series`, populated `accounts` —
  the page renders the account list with an empty-state chart, not an error.
- Manual endpoints return the API's standard error JSON on validation failure
  (400), unknown id (404), non-manual target (400), id collision (409).

## Testing

- **DB tests** (`TEST_DATABASE_URL` harness): snapshot upsert idempotency
  (same-day double capture keeps one row, last value wins); `balance_date` keying
  (stale date does not create a today row); carry-forward across a gap; series
  excludes pre-first-snapshot dates; view filtering; manual-account creation +
  balance update write snapshot rows transactionally.
- **Handler tests**: networth response shape, range/view param validation, manual
  POST/PUT validation matrix (bad owner, non-numeric balance, PUT on synced account,
  409 collision).
- **SPA tests**: page renders with fixture series; drill-down switches series;
  manual add/edit forms call the API; empty state.

## Rollout

- vollmint `0.4.0` (minor bump: schema + endpoints + page). Migration 0004 runs via
  the existing migration-on-start path.
- k8s repo: OCIRepository tag bump `0.3.2 → 0.4.0` in a separate PR after images
  publish, as with prior releases.
- History starts accruing at the first post-deploy sync; the graph becomes
  interesting over weeks. Manual accounts give day-one net-worth totals.
