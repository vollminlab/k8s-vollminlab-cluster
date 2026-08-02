# vollmint Plan-5 Design — Transaction Splits, Bill Forecasting, Spending Insights

**Created:** 2026-07-29
**Status:** Approved scope (Scott, 2026-07-29); design pending final review
**Repo:** `vollminlab/vollmint` (Go stdlib API + React/TS SPA)

## Goal

Three features on top of the live v0.2.0 app:

1. **Transaction splits** — divide one transaction across multiple categories, inline on the Transactions page.
2. **Bill forecasting** — an "Upcoming bills" dashboard panel (paid vs. due) derived from recurring-charge detection.
3. **Spending-reduction insights** — rule-based, SQL-computed plain-language cards: category spikes and a subscription audit. No ML.

Explicitly out of scope: net worth / balance snapshots (phase-6 candidate), small-charge-bleed and merchant-concentration insights, insight dismiss/state tracking, per-user authorization.

## Cross-cutting constraints

- Money is Postgres `numeric` cast `::text` — decimal strings end-to-end, never float64.
- JSON: single-key envelopes (`{"bills":[...]}`), errors `{"error":"msg"}`.
- All new endpoints accept `view` (scott/nikki/joint/household) and `month` (YYYY-MM) query params with the same semantics as existing endpoints (`ownerFilter`, transfer exclusion).
- TDD throughout: store/report tests against `TEST_DATABASE_URL`, handler tests, Vitest component tests.

### The Venmo problem (Scott's call-out)

Venmo (and Zelle) charges all carry the same generic payee, so payee-grouped logic would collapse all Venmo activity into one fake "recurring bill" or "subscription."

- **Splits are the intended tool for Venmo**: one Venmo charge covering dinner + tickets gets split by category. Split parts always win over the parent's rule-derived category in every aggregate.
- **Forecasting and the subscription audit exclude P2P payees**: any payee matching (case-insensitive substring) `venmo` or `zelle` is excluded from bill prediction and the subscription audit. The Recurring *page* is unchanged (it already shows Venmo as one row; that's informational and fine).

---

## Feature 1 — Transaction splits

### Schema (migration `0003_splits.sql`)

```sql
CREATE TABLE transaction_splits (
    id             bigserial PRIMARY KEY,
    transaction_id bigint NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    category_id    int NOT NULL REFERENCES categories(id),
    amount         numeric(12,2) NOT NULL,
    note           text NOT NULL DEFAULT ''
);
CREATE INDEX idx_split_txn ON transaction_splits (transaction_id);
CREATE INDEX idx_split_category ON transaction_splits (category_id);
```

Invariants (enforced in the API, since parts are replaced atomically in one transaction):

- Parts sum exactly to the parent's `amount`; every part has the same sign as the parent and is non-zero.
- Minimum 2 parts (1 part = recategorization; use the existing PATCH).
- The parent must not be a transfer (`transfer_peer_id IS NULL`) and must not be `pending` (pending rows are swept/replaced by sync).

### Sync interaction

If SimpleFIN re-upserts a transaction with a *changed amount*, its splits become stale. After each sync upsert batch, run one cleanup statement: delete all splits whose per-transaction sum no longer equals the parent amount, and count the deletions into the sync run detail. Rare, deterministic, no triggers.

### Split-aware category attribution — one SQL fragment, reused

The core trick is a `LEFT JOIN` that naturally replaces a split parent with its parts:

```sql
-- "effective" rows: a txn with N splits yields N rows carrying split
-- category+amount; an unsplit txn yields itself unchanged.
FROM transactions t
LEFT JOIN transaction_splits sp ON sp.transaction_id = t.id
-- effective category: COALESCE(sp.category_id, t.category_id)
-- effective amount:   COALESCE(sp.amount, t.amount)
```

Defined once in `internal/report` as a shared fragment and applied to:

- **`SpendByCategory`** (category totals + budget progress + drill-down entry point) — group by effective category, sum effective amount.
- **`Summary`** — only the `Vices` term changes (`is_vice` is a category attribute); `In`/`Out` totals are unaffected because parts sum to the parent. Implement Vices via the effective join; keep In/Out on raw transactions.
- **Category-filtered transaction list** — `GET /api/transactions?category_id=N` matches a transaction if its *effective* categorization includes N (own category when unsplit, or any split part). The parent row is returned (with its splits embedded), so the drill-down shows the real ledger row.
- **`MonthlyFlow` (Trends)** — untouched: aggregates totals, not categories.
- **Insights category-spike generator** (below) uses the same fragment.

### API

- `GET /api/transactions` — each transaction gains `"splits": [{id, category_id, category, amount, note}]` (empty array when unsplit). Fetched with one batched query over the page's transaction IDs (no N+1).
- `PUT /api/transactions/{id}/splits` — body `{"splits":[{"category_id":N,"amount":"-32.50","note":""}, ...]}`. Validates invariants; replaces the full set atomically (DELETE + INSERT in one tx). Returns the updated transaction with splits. 400 on sum mismatch (message includes expected total and received sum), <2 parts, sign mismatch, transfer/pending parent, unknown category. 404 unknown transaction.
- `DELETE /api/transactions/{id}/splits` — unsplit (idempotent; 200 even if no splits).

### UI (Transactions page)

- Each row gets a **Split** action. It expands an inline editor under the row: rows of category-select + amount input (+ optional note), an add-row button, and a live **remainder** readout (`parent − Σ parts`). Save is disabled until the remainder is exactly 0.00. Amounts are entered as positive dollars; the client applies the parent's sign.
- Editor seeds with two rows: the parent's current category with the full amount, and an empty row.
- Split rows show a badge in the category cell ("Split · Dining + Groceries" or "Split (3)") and expand to show parts. Split action on an already-split row reopens the editor pre-filled; an Unsplit action calls DELETE.
- Transfer and pending rows don't show the Split action.

---

## Feature 2 — Bill forecasting

Computed on the fly — no schema.

### Endpoint

`GET /api/forecast?view=&month=` →

```json
{"forecast": {
  "month": "2026-07", "view": "household",
  "bills": [
    {"payee": "VERIZON WIRELESS", "category_id": 12, "category": "Utilities",
     "predicted_day": 14, "expected_amount": "128.42",
     "paid": true, "paid_date": "2026-07-13", "paid_amount": "128.42"},
    ...
  ],
  "remaining_expected": "412.17"
}}
```

### Expected-bill detection (`internal/report.Forecast`)

One SQL query, building on the same base as `Recurring`:

1. **Candidate payees**: non-transfer spend, payee ≠ '', effective-owner filtered, **excluding P2P payees** (`payee ILIKE '%venmo%' OR payee ILIKE '%zelle%'`), grouped by exact payee.
2. **Monthly cadence filter**: charged in ≥ 3 distinct months overall AND in at least 2 of the 3 full months preceding the requested month (drops dead subscriptions and sporadic merchants that pass the ≥3-months bar).
3. **Predicted day**: median day-of-month (`percentile_cont(0.5)`) over the payee's charges in the last 6 months, rounded to int.
4. **Expected amount**: the most recent charge's magnitude (not the average — captures price changes immediately).
5. **Category**: the payee's most frequent category (mode) across its history.
6. **Paid check**: left-join the requested month's transactions by exact payee (spend, non-transfer, non-pending). If any exist, `paid=true` with the first (earliest posted) match's date and magnitude. No amount tolerance — payee identity is the match key; the amount shown is informational.

`remaining_expected` = Σ `expected_amount` over unpaid bills. Bills sorted: unpaid by `predicted_day` ascending, then paid by `paid_date`.

### UI (Dashboard)

New **Upcoming bills** panel: header shows "Upcoming bills — $412.17 remaining expected". Each row: payee (title-cased for display), category, predicted date ("~Jul 14") or paid date, amount. Paid rows get a checkmark and dimmed styling and sink below unpaid ones. Empty state: "No recurring bills detected yet." Panel respects the dashboard's current view/month selectors.

---

## Feature 3 — Spending-reduction insights

Computed on the fly — no schema, no dismiss state.

### Endpoint

`GET /api/insights?view=&month=` →

```json
{"insights": [
  {"type": "category_spike", "title": "Dining is running hot",
   "body": "You've spent $412.10 on Dining this month — $161.40 above your 3-month average of $250.70.",
   "amount": "161.40"},
  {"type": "subscription_total", "title": "...", "body": "...", "amount": "..."},
  ...
]}
```

Cards carry pre-rendered plain-language `title`/`body` from the API (numbers formatted server-side from decimal strings); `type` drives the icon/accent; `amount` (the money at stake) drives sort order, descending.

### Generator A — category spikes (`internal/report.InsightCategorySpikes`)

Split-aware (uses the effective-category fragment). For each spend category in the requested month:

- `spent` = month-to-date effective spend; `avg3` = mean of the 3 preceding full months' effective spend; `budget` = that category's budget for the month, if set.
- **Spike card** fires when `spent ≥ 1.25 × avg3` AND `spent − avg3 ≥ $50` (materiality floor — no nagging about $12). Body cites spent, average, and the delta.
- **Budget-breach card** fires when a budget exists and `spent > budget`: "Groceries is $83.20 over its $600.00 budget with N days left in the month." (Only for the current month; for past months the phrasing drops "with N days left".)
- At most one card per category (budget breach wins over spike); cap at the top 5 by delta.

### Generator B — subscription audit (`internal/report.InsightSubscriptions`)

Operates on the forecast's expected-bill set (same detection, same P2P exclusion), restricted to payees whose category is `Subscriptions` **or** whose amount is stable (latest magnitude within ±10% of the median) — pure utilities/rent also recur, and they belong in the total but not in "cancel this" framing. Three card types:

- **Total burn** (always emitted when ≥1 subscription-like bill): "You're carrying $86.93/month across 7 recurring charges." Lists the top 3 by amount in the body.
- **Price increase** — for any recurring payee whose latest charge exceeds the previous one by >5% AND ≥$1: "Netflix went from $15.49 to $17.99 (+$2.50)."
- **Overlap** — static keyword map compiled into the binary: `streaming` (netflix, hulu, disney, max, paramount, peacock, youtube premium, apple tv), `music` (spotify, apple music, tidal, pandora), `cloud storage` (dropbox, google one, icloud, onedrive), `ai` (anthropic, claude.ai, openai, chatgpt). If ≥2 active recurring payees match the same group: "You're paying for 2 streaming services (Netflix, Hulu) — $33.48/month combined."

### UI (Trends page)

Insight cards render below the trends chart in a responsive card grid. Each card: icon by type, title, body. Empty state: nothing renders (no "all good!" filler). Cards respect the page's view/month selectors.

---

## Testing

- **Report/store tests** (Go, real Postgres via `TEST_DATABASE_URL`): split invariant enforcement incl. atomic replace; effective-attribution math in SpendByCategory/Summary-vices/category filter; sync stale-split cleanup; forecast cadence filter (payee in 2 of last 3 months in, dead subscription out, Venmo out), median day, paid matching; spike thresholds (fires at 1.25×+$50, not below), budget-breach precedence; subscription price-increase and overlap detection.
- **API handler tests**: PUT/DELETE splits status codes and error bodies; forecast/insights envelope shapes; view/month param handling.
- **Vitest component tests**: SplitEditor (remainder math, save-disabled gate, sign handling), UpcomingBills panel (paid/unpaid ordering, empty state), InsightCards (render by type, empty state).

## Delivery

Single vollmint PR (v0.3.0): migration 0003, report + api + web changes. Deploys via the existing chain (tag → Harbor build → cluster OCIRepository tag bump PR). Migrations run via the existing goose auto-migrate on startup. No cluster-side changes beyond the version bump.
