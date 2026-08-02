# vollmint Splits, Bill Forecasting & Spending Insights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add transaction splits (divide one transaction across categories), an "Upcoming bills" forecast panel, and rule-based spending-reduction insight cards to the live vollmint v0.2.0 app, shipping as v0.3.0.

**Architecture:** One new table (`transaction_splits`) with atomic replace-set semantics in the store layer; all aggregates gain split-awareness via a shared `LEFT JOIN transaction_splits` + `COALESCE` pattern. Forecasting and insights are computed on the fly in `internal/report` SQL (no new schema), with P2P payees (Venmo/Zelle) excluded from payee-grouped detection. Three new API endpoints, three new React components.

**Tech Stack:** Go stdlib HTTP + pgx/v5 + goose migrations + Postgres numeric (decimal strings end-to-end, never float), React/TypeScript + Vitest.

**Date:** 2026-07-29
**Spec:** `docs/superpowers/specs/vollmint-forecasting-splits-insights-design.md` (this repo)

---

## Context — read before Task 1

- **Implementation repo:** `/home/vollmin/repos/vollminlab/vollmint` — NOT the k8s repo this plan lives in. All file paths below are relative to the vollmint repo root.
- **Worktree:** Use the superpowers:using-git-worktrees skill. Branch `feat/splits-forecast-insights`, worktree at `.worktrees/feat/splits-forecast-insights` (the repo's existing convention).
- **Never work on `main`; never push to `main`.** The finished branch becomes a PR that **Scott merges — never the agent**.
- **Env:** `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"` before any Go command.
- **Go tests:** `TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' go test ./...` (a dev Postgres runs on localhost:5433). Migrations auto-apply in test setup, so the new migration is live as soon as tests run.
- **Web tests:** `cd web && npx vitest run` — currently 35 tests in 10 files, all passing.
- **`web/dist/index.html` is a tracked placeholder.** If you run `npm run build`, restore it afterward: `git checkout web/dist/index.html`. Never commit a real build.
- **CI note:** the ARC runner occasionally hits a dind race. Retrigger with `gh pr close N && gh pr reopen N`.
- **Money is decimal strings** (Postgres `numeric` cast `::text`) everywhere. Never parse to float64 in Go or `Number()` in TS except for chart geometry. All money math happens in SQL or in integer cents.
- **JSON envelopes:** single-key (`{"forecast": {...}}`), errors `{"error": "msg"}`.
- **Commit trailers:** every commit message ends with these two lines:

  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX
  ```

- **Always `git add` files explicitly by name** — never `git add -A` or `git add .`.

### Existing helpers the tasks below rely on

| Helper | Where | Signature / behavior |
|---|---|---|
| `testDB(t)` | `internal/store/testdb_test.go:16` | `func testDB(t *testing.T) *Store` — connects via `TEST_DATABASE_URL`, runs migrations, truncates transactions/sync_runs/budgets (RESTART IDENTITY CASCADE — cascades to `transaction_splits`), deletes non-seed accounts/rules/categories |
| `testDB(t)` | `internal/ingest/testdb_test.go:17` | same idea, returns `*store.Store` |
| `testStore(t)` | `internal/report/` test helpers | same idea for report tests |
| `seedSpend(t, s, acct, owner, extID, posted, amount, catName)` | report tests | seeds an account + one categorized transaction — **sets `Payee: extID`**, so it CANNOT seed same-payee-across-months data (forecast tests define `seedBill` instead) |
| `setBudget(t, s, catName, month, amount)` | report tests | seeds a budget row |
| `testStore(t)`, `seedTxn(t, s, acct, owner, extID, posted, amount, desc) int64`, `mustDate(t, s)` | api tests | api-package equivalents; `seedTxn` returns the txn id |
| `store.Account` | `internal/store/store.go:31` | `{ID, Name, Org, Currency, Owner string; Balance string; BalanceDate time.Time}` |
| `store.Txn` | `internal/store/store.go:37` | `{ID int64; Source, ExternalID, AccountID string; Posted time.Time; Amount, Description, Payee string; Pending bool; Raw []byte}` |
| `ownerFilter(view, argN)` | `internal/report/report.go` | returns SQL fragment + args for view scoping (report package) |
| `ownerClause(view, &args)` | `internal/store/query.go` | store-package equivalent (pointer-append pattern) |
| `requireViewMonth(w, r)` | `internal/api/summary.go:15-36` | validates `view` (default `household`) + required `month` (YYYY-MM); writes the 400 itself and returns `ok=false` |
| `writeJSON(w, code, v)` / `writeErr(w, code, msg)` | `internal/api/server.go` | response helpers |

### File structure (what this plan creates/modifies)

```
internal/migrate/migrations/0003_splits.sql        CREATE  migration
internal/store/splits.go                           CREATE  ReplaceSplits/DeleteSplits/SplitsByTxnIDs
internal/store/splits_test.go                      CREATE  invariant + batch tests
internal/store/query.go                            MODIFY  TxnRow.Splits, split-aware category filter, GetTransaction
internal/store/query_test.go                       MODIFY  new tests appended
internal/ingest/sync.go                            MODIFY  CleanStaleSplits + SyncResult.SplitsDeleted + detail
internal/ingest/sync_test.go                       MODIFY  stale-split cleanup test
internal/api/splits.go                             CREATE  PUT/DELETE /api/transactions/{id}/splits
internal/api/splits_test.go                        CREATE  handler tests
internal/api/server.go                             MODIFY  route registration
internal/report/report.go                          MODIFY  split-aware SpendByCategory + Summary vices
internal/report/report_test.go                     MODIFY  effective-attribution tests
internal/report/text.go                            CREATE  cents/centsToDec/usd/titleCase helpers
internal/report/text_test.go                       CREATE  pure unit tests (no DB)
internal/report/forecast.go                        CREATE  Forecast()
internal/report/forecast_test.go                   CREATE  cadence/median/paid tests + seedBill helper
internal/report/insights.go                        CREATE  InsightCategorySpikes/InsightSubscriptions/Insights
internal/report/insights_test.go                   CREATE  threshold/audit tests
internal/api/forecast.go                           CREATE  GET /api/forecast
internal/api/forecast_test.go                      CREATE  handler tests
internal/api/insights.go                           CREATE  GET /api/insights
internal/api/insights_test.go                      CREATE  handler tests
web/src/api.ts                                     MODIFY  Split/Forecast/Insight types + calls
web/src/api.test.ts                                MODIFY  fixtures gain splits: [] + new call tests
web/src/format.ts                                  MODIFY  titleCase/monthDayLabel/dateLabel
web/src/format.test.ts                             MODIFY  new helper tests
web/src/components/SplitEditor.tsx                 CREATE  inline split editor
web/src/components/SplitEditor.test.tsx            CREATE  component tests
web/src/components/Transactions.tsx                MODIFY  split badge/actions/editor row
web/src/components/Transactions.test.tsx           MODIFY  fixtures + new tests
web/src/components/UpcomingBills.tsx               CREATE  dashboard panel
web/src/components/UpcomingBills.test.tsx          CREATE  component tests
web/src/components/Dashboard.tsx                   MODIFY  mount UpcomingBills
web/src/components/InsightCards.tsx                CREATE  insight card grid
web/src/components/InsightCards.test.tsx           CREATE  component tests
web/src/components/Trends.tsx                      MODIFY  mount InsightCards
```

---

### Task 1: Migration `0003_splits.sql` + cascade-delete test

**Files:**
- Create: `internal/migrate/migrations/0003_splits.sql`
- Test: `internal/store/splits_test.go` (created here with the first test; Task 2 appends more)

- [ ] **Step 1: Write the migration**

`internal/migrate/migrations/0003_splits.sql` (goose format, matching `0002_seed.sql`):

```sql
-- +goose Up
CREATE TABLE transaction_splits (
    id             bigserial PRIMARY KEY,
    transaction_id bigint NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    category_id    int NOT NULL REFERENCES categories(id),
    amount         numeric(12,2) NOT NULL,
    note           text NOT NULL DEFAULT ''
);
CREATE INDEX idx_split_txn ON transaction_splits (transaction_id);
CREATE INDEX idx_split_category ON transaction_splits (category_id);

-- +goose Down
DROP TABLE transaction_splits;
```

- [ ] **Step 2: Write the failing cascade test**

Create `internal/store/splits_test.go`:

```go
package store

import (
	"context"
	"testing"
	"time"
)

// seedSplitTxn creates an account + one posted, categorized-later transaction
// and returns its DB id. Amount is the parent amount (negative = spend).
func seedSplitTxn(t *testing.T, s *Store, extID, amount string) int64 {
	t.Helper()
	ctx := context.Background()
	if err := s.UpsertAccounts(ctx, []Account{{
		ID: "acct-split-test", Name: "Split Test", Org: "test", Owner: "scott",
	}}); err != nil {
		t.Fatalf("seed account: %v", err)
	}
	if _, err := s.UpsertTransactions(ctx, []Txn{{
		Source: "simplefin", ExternalID: extID, AccountID: "acct-split-test",
		Posted: time.Date(2026, 7, 10, 0, 0, 0, 0, time.UTC),
		Amount: amount, Description: "split test txn", Payee: "SPLIT TEST",
	}}); err != nil {
		t.Fatalf("seed txn: %v", err)
	}
	var id int64
	if err := s.Pool.QueryRow(ctx,
		`SELECT id FROM transactions WHERE source = 'simplefin' AND external_id = $1`,
		extID).Scan(&id); err != nil {
		t.Fatalf("lookup txn id: %v", err)
	}
	return id
}

// catID resolves a seed category name to its id.
func catID(t *testing.T, s *Store, name string) int {
	t.Helper()
	var id int
	if err := s.Pool.QueryRow(context.Background(),
		`SELECT id FROM categories WHERE name = $1`, name).Scan(&id); err != nil {
		t.Fatalf("category %q: %v", name, err)
	}
	return id
}

func TestSplitsCascadeDeleteWithTransaction(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedSplitTxn(t, s, "cascade-1", "-50.00")

	_, err := s.Pool.Exec(ctx,
		`INSERT INTO transaction_splits (transaction_id, category_id, amount)
		 VALUES ($1, $2, '-30.00'), ($1, $3, '-20.00')`,
		id, catID(t, s, "Dining"), catID(t, s, "Groceries"))
	if err != nil {
		t.Fatalf("insert splits: %v", err)
	}

	if _, err := s.Pool.Exec(ctx, `DELETE FROM transactions WHERE id = $1`, id); err != nil {
		t.Fatalf("delete txn: %v", err)
	}
	var n int
	if err := s.Pool.QueryRow(ctx,
		`SELECT count(*) FROM transaction_splits WHERE transaction_id = $1`, id).Scan(&n); err != nil {
		t.Fatalf("count splits: %v", err)
	}
	if n != 0 {
		t.Fatalf("want 0 splits after parent delete, got %d", n)
	}
}
```

- [ ] **Step 3: Run test to verify it passes (migration applies in testDB)**

```bash
export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/store/ -run TestSplitsCascadeDelete -v
```

Expected: PASS. (This task is schema-first, not strict red-green: the test exists to prove the migration's `ON DELETE CASCADE` and to lock the schema. If it FAILS with `relation "transaction_splits" does not exist`, the migration didn't apply — check the filename is exactly `0003_splits.sql`.)

- [ ] **Step 4: Run the full store suite to confirm no regression**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/store/ ./internal/migrate/
```

Expected: PASS (all existing tests still green — the TRUNCATE ... CASCADE in testDB covers the new table automatically).

- [ ] **Step 5: Commit**

```bash
git add internal/migrate/migrations/0003_splits.sql internal/store/splits_test.go
git commit -m "feat(store): add transaction_splits table (migration 0003)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 2: Store layer — `ReplaceSplits` / `DeleteSplits` / `SplitsByTxnIDs`

**Files:**
- Create: `internal/store/splits.go`
- Test: `internal/store/splits_test.go` (append)

- [ ] **Step 1: Write the failing tests**

Append to `internal/store/splits_test.go`:

```go
func TestReplaceSplitsHappyPath(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedSplitTxn(t, s, "happy-1", "-50.00")
	dining, groceries := catID(t, s, "Dining"), catID(t, s, "Groceries")

	err := s.ReplaceSplits(ctx, id, []SplitInput{
		{CategoryID: dining, Amount: "-30.00", Note: "dinner"},
		{CategoryID: groceries, Amount: "-20.00"},
	})
	if err != nil {
		t.Fatalf("ReplaceSplits: %v", err)
	}

	m, err := s.SplitsByTxnIDs(ctx, []int64{id})
	if err != nil {
		t.Fatalf("SplitsByTxnIDs: %v", err)
	}
	sp := m[id]
	if len(sp) != 2 {
		t.Fatalf("want 2 splits, got %d", len(sp))
	}
	if sp[0].Category != "Dining" || sp[0].Amount != "-30.00" || sp[0].Note != "dinner" {
		t.Fatalf("first split wrong: %+v", sp[0])
	}
	if sp[1].Category != "Groceries" || sp[1].Amount != "-20.00" {
		t.Fatalf("second split wrong: %+v", sp[1])
	}
}

func TestReplaceSplitsTooFewParts(t *testing.T) {
	s := testDB(t)
	id := seedSplitTxn(t, s, "few-1", "-50.00")
	err := s.ReplaceSplits(context.Background(), id, []SplitInput{
		{CategoryID: catID(t, s, "Dining"), Amount: "-50.00"},
	})
	if !errors.Is(err, ErrSplitTooFew) {
		t.Fatalf("want ErrSplitTooFew, got %v", err)
	}
}

func TestReplaceSplitsBadAmount(t *testing.T) {
	s := testDB(t)
	id := seedSplitTxn(t, s, "bad-amt-1", "-50.00")
	err := s.ReplaceSplits(context.Background(), id, []SplitInput{
		{CategoryID: catID(t, s, "Dining"), Amount: "thirty"},
		{CategoryID: catID(t, s, "Groceries"), Amount: "-20.00"},
	})
	if !errors.Is(err, ErrInvalidAmount) {
		t.Fatalf("want ErrInvalidAmount, got %v", err)
	}
}

func TestReplaceSplitsSumMismatch(t *testing.T) {
	s := testDB(t)
	id := seedSplitTxn(t, s, "sum-1", "-50.00")
	err := s.ReplaceSplits(context.Background(), id, []SplitInput{
		{CategoryID: catID(t, s, "Dining"), Amount: "-30.00"},
		{CategoryID: catID(t, s, "Groceries"), Amount: "-15.00"},
	})
	var sumErr *SplitSumError
	if !errors.As(err, &sumErr) {
		t.Fatalf("want *SplitSumError, got %v", err)
	}
	if sumErr.Expected != "-50.00" || sumErr.Received != "-45.00" {
		t.Fatalf("wrong totals in error: %+v", sumErr)
	}
}

func TestReplaceSplitsSignMismatch(t *testing.T) {
	s := testDB(t)
	id := seedSplitTxn(t, s, "sign-1", "-50.00")
	// -70 + +20 sums to -50 exactly — sign check must fire before sum check.
	err := s.ReplaceSplits(context.Background(), id, []SplitInput{
		{CategoryID: catID(t, s, "Dining"), Amount: "-70.00"},
		{CategoryID: catID(t, s, "Groceries"), Amount: "20.00"},
	})
	if !errors.Is(err, ErrSplitSign) {
		t.Fatalf("want ErrSplitSign, got %v", err)
	}
}

func TestReplaceSplitsNotFound(t *testing.T) {
	s := testDB(t)
	err := s.ReplaceSplits(context.Background(), 999999, []SplitInput{
		{CategoryID: catID(t, s, "Dining"), Amount: "-30.00"},
		{CategoryID: catID(t, s, "Groceries"), Amount: "-20.00"},
	})
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func TestReplaceSplitsRejectsPendingParent(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedSplitTxn(t, s, "pend-1", "-50.00")
	if _, err := s.Pool.Exec(ctx, `UPDATE transactions SET pending = true WHERE id = $1`, id); err != nil {
		t.Fatalf("mark pending: %v", err)
	}
	err := s.ReplaceSplits(ctx, id, []SplitInput{
		{CategoryID: catID(t, s, "Dining"), Amount: "-30.00"},
		{CategoryID: catID(t, s, "Groceries"), Amount: "-20.00"},
	})
	if !errors.Is(err, ErrSplitParent) {
		t.Fatalf("want ErrSplitParent, got %v", err)
	}
}

func TestReplaceSplitsRejectsTransferParent(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedSplitTxn(t, s, "xfer-1", "-50.00")
	if _, err := s.Pool.Exec(ctx,
		`UPDATE transactions SET transfer_peer_id = id WHERE id = $1`, id); err != nil {
		t.Fatalf("mark transfer: %v", err)
	}
	err := s.ReplaceSplits(ctx, id, []SplitInput{
		{CategoryID: catID(t, s, "Dining"), Amount: "-30.00"},
		{CategoryID: catID(t, s, "Groceries"), Amount: "-20.00"},
	})
	if !errors.Is(err, ErrSplitParent) {
		t.Fatalf("want ErrSplitParent, got %v", err)
	}
}

func TestReplaceSplitsUnknownCategory(t *testing.T) {
	s := testDB(t)
	id := seedSplitTxn(t, s, "cat-1", "-50.00")
	err := s.ReplaceSplits(context.Background(), id, []SplitInput{
		{CategoryID: 999999, Amount: "-30.00"},
		{CategoryID: catID(t, s, "Groceries"), Amount: "-20.00"},
	})
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "23503" {
		t.Fatalf("want FK violation 23503, got %v", err)
	}
}

func TestReplaceSplitsIsAtomicReplace(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedSplitTxn(t, s, "atomic-1", "-50.00")
	dining, groceries, ent := catID(t, s, "Dining"), catID(t, s, "Groceries"), catID(t, s, "Entertainment")

	if err := s.ReplaceSplits(ctx, id, []SplitInput{
		{CategoryID: dining, Amount: "-30.00"},
		{CategoryID: groceries, Amount: "-20.00"},
	}); err != nil {
		t.Fatalf("first replace: %v", err)
	}
	if err := s.ReplaceSplits(ctx, id, []SplitInput{
		{CategoryID: ent, Amount: "-45.00"},
		{CategoryID: groceries, Amount: "-5.00"},
	}); err != nil {
		t.Fatalf("second replace: %v", err)
	}
	m, err := s.SplitsByTxnIDs(ctx, []int64{id})
	if err != nil {
		t.Fatalf("SplitsByTxnIDs: %v", err)
	}
	sp := m[id]
	if len(sp) != 2 || sp[0].Category != "Entertainment" || sp[1].Amount != "-5.00" {
		t.Fatalf("second set did not fully replace first: %+v", sp)
	}
}

func TestDeleteSplitsIdempotent(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedSplitTxn(t, s, "del-1", "-50.00")
	if err := s.ReplaceSplits(ctx, id, []SplitInput{
		{CategoryID: catID(t, s, "Dining"), Amount: "-30.00"},
		{CategoryID: catID(t, s, "Groceries"), Amount: "-20.00"},
	}); err != nil {
		t.Fatalf("replace: %v", err)
	}
	if err := s.DeleteSplits(ctx, id); err != nil {
		t.Fatalf("first delete: %v", err)
	}
	if err := s.DeleteSplits(ctx, id); err != nil {
		t.Fatalf("second delete should be a no-op: %v", err)
	}
	m, _ := s.SplitsByTxnIDs(ctx, []int64{id})
	if len(m[id]) != 0 {
		t.Fatalf("splits remain after delete: %+v", m[id])
	}
}

func TestSplitsByTxnIDsEmptyInput(t *testing.T) {
	s := testDB(t)
	m, err := s.SplitsByTxnIDs(context.Background(), nil)
	if err != nil {
		t.Fatalf("SplitsByTxnIDs(nil): %v", err)
	}
	if len(m) != 0 {
		t.Fatalf("want empty map, got %+v", m)
	}
}
```

Add the imports the new tests need at the top of `splits_test.go` (final import block):

```go
import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/store/ -run 'TestReplaceSplits|TestDeleteSplits|TestSplitsByTxnIDs' -v
```

Expected: COMPILE FAILURE — `undefined: SplitInput`, `undefined: ErrSplitTooFew`, etc.

- [ ] **Step 3: Write the implementation**

Create `internal/store/splits.go`:

```go
package store

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
)

var (
	ErrSplitTooFew = errors.New("at least 2 split parts required")
	ErrSplitParent = errors.New("cannot split a transfer or pending transaction")
	ErrSplitSign   = errors.New("every split part must be non-zero and match the transaction's sign")
)

// SplitSumError reports a sum mismatch with the exact expected/received totals
// so the API can echo them back to the client.
type SplitSumError struct {
	Expected, Received string
}

func (e *SplitSumError) Error() string {
	return fmt.Sprintf("split amounts must sum to %s (got %s)", e.Expected, e.Received)
}

type SplitRow struct {
	ID         int64  `json:"id"`
	CategoryID int    `json:"category_id"`
	Category   string `json:"category"`
	Amount     string `json:"amount"`
	Note       string `json:"note"`
}

type SplitInput struct {
	CategoryID int    `json:"category_id"`
	Amount     string `json:"amount"`
	Note       string `json:"note"`
}

// ReplaceSplits atomically replaces a transaction's split set. Invariants
// (sum equals parent amount, matching sign, non-zero parts) are checked in
// Postgres numeric — never in Go floats.
func (s *Store) ReplaceSplits(ctx context.Context, txnID int64, parts []SplitInput) error {
	if len(parts) < 2 {
		return ErrSplitTooFew
	}
	for i, p := range parts {
		if !amountRe.MatchString(p.Amount) {
			return fmt.Errorf("split %d: bad amount %q: %w", i, p.Amount, ErrInvalidAmount)
		}
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var pending bool
	var transferPeer *int64
	err = tx.QueryRow(ctx,
		`SELECT pending, transfer_peer_id FROM transactions WHERE id = $1 FOR UPDATE`,
		txnID).Scan(&pending, &transferPeer)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if pending || transferPeer != nil {
		return ErrSplitParent
	}

	if _, err := tx.Exec(ctx, `DELETE FROM transaction_splits WHERE transaction_id = $1`, txnID); err != nil {
		return err
	}

	b := &pgx.Batch{}
	for _, p := range parts {
		b.Queue(`INSERT INTO transaction_splits (transaction_id, category_id, amount, note)
			VALUES ($1, $2, $3, $4)`, txnID, p.CategoryID, p.Amount, p.Note)
	}
	br := tx.SendBatch(ctx, b)
	for range parts {
		if _, err := br.Exec(); err != nil {
			br.Close()
			return err // FK violations (unknown category) surface here as *pgconn.PgError
		}
	}
	if err := br.Close(); err != nil {
		return err
	}

	var expected, received string
	var sumOK, signsOK bool
	err = tx.QueryRow(ctx, `
		SELECT t.amount::text, SUM(sp.amount)::text, t.amount = SUM(sp.amount),
		       bool_and(sign(sp.amount) = sign(t.amount) AND sp.amount <> 0)
		FROM transactions t
		JOIN transaction_splits sp ON sp.transaction_id = t.id
		WHERE t.id = $1
		GROUP BY t.amount`, txnID).Scan(&expected, &received, &sumOK, &signsOK)
	if err != nil {
		return err
	}
	if !signsOK {
		return ErrSplitSign
	}
	if !sumOK {
		return &SplitSumError{Expected: expected, Received: received}
	}
	return tx.Commit(ctx)
}

// DeleteSplits removes all splits for a transaction. Idempotent — deleting a
// transaction with no splits is a no-op.
func (s *Store) DeleteSplits(ctx context.Context, txnID int64) error {
	_, err := s.Pool.Exec(ctx, `DELETE FROM transaction_splits WHERE transaction_id = $1`, txnID)
	return err
}

// SplitsByTxnIDs returns splits for a batch of transactions in one query
// (no N+1). Transactions without splits are simply absent from the map.
func (s *Store) SplitsByTxnIDs(ctx context.Context, ids []int64) (map[int64][]SplitRow, error) {
	out := make(map[int64][]SplitRow)
	if len(ids) == 0 {
		return out, nil
	}
	rows, err := s.Pool.Query(ctx, `
		SELECT sp.transaction_id, sp.id, sp.category_id, c.name, sp.amount::text, sp.note
		FROM transaction_splits sp
		JOIN categories c ON c.id = sp.category_id
		WHERE sp.transaction_id = ANY($1)
		ORDER BY sp.id`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var txnID int64
		var r SplitRow
		if err := rows.Scan(&txnID, &r.ID, &r.CategoryID, &r.Category, &r.Amount, &r.Note); err != nil {
			return nil, err
		}
		out[txnID] = append(out[txnID], r)
	}
	return out, rows.Err()
}
```

Note: `amountRe`, `ErrInvalidAmount`, and `ErrNotFound` already exist in `internal/store/query.go` — do not redefine them.

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/store/ -v
```

Expected: PASS — all new split tests plus every pre-existing store test.

- [ ] **Step 5: Commit**

```bash
git add internal/store/splits.go internal/store/splits_test.go
git commit -m "feat(store): atomic split replace/delete/batch-fetch with numeric invariants

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 3: Split-aware transaction queries — `TxnRow.Splits`, category filter, `GetTransaction`

**Files:**
- Modify: `internal/store/query.go`
- Test: `internal/store/query_test.go` (append)

- [ ] **Step 1: Write the failing tests**

Append to `internal/store/query_test.go` (it is `package store`; `seedSplitTxn` and `catID` from `splits_test.go` are visible):

```go
func TestListTransactionsEmbedsSplits(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedSplitTxn(t, s, "embed-1", "-50.00")
	if err := s.ReplaceSplits(ctx, id, []SplitInput{
		{CategoryID: catID(t, s, "Dining"), Amount: "-30.00"},
		{CategoryID: catID(t, s, "Groceries"), Amount: "-20.00"},
	}); err != nil {
		t.Fatalf("replace: %v", err)
	}

	rows, err := s.ListTransactions(ctx, TxnFilter{View: "household", Month: "2026-07"})
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	var found *TxnRow
	for i := range rows {
		if rows[i].ID == id {
			found = &rows[i]
		} else if rows[i].Splits == nil {
			t.Fatalf("unsplit txn %d has nil Splits — want empty slice", rows[i].ID)
		}
	}
	if found == nil {
		t.Fatal("split txn not in listing")
	}
	if len(found.Splits) != 2 || found.Splits[0].Category != "Dining" {
		t.Fatalf("splits not embedded: %+v", found.Splits)
	}
}

func TestListTransactionsCategoryFilterIsSplitAware(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedSplitTxn(t, s, "filter-1", "-50.00")
	dining, groceries := catID(t, s, "Dining"), catID(t, s, "Groceries")

	// Parent categorized Dining, then split into Dining + Groceries.
	if err := s.UpdateTransaction(ctx, id, TxnPatch{CategoryID: &dining}); err != nil {
		t.Fatalf("categorize: %v", err)
	}
	if err := s.ReplaceSplits(ctx, id, []SplitInput{
		{CategoryID: dining, Amount: "-30.00"},
		{CategoryID: groceries, Amount: "-20.00"},
	}); err != nil {
		t.Fatalf("replace: %v", err)
	}

	// Filter by a split part's category → parent row returned.
	rows, err := s.ListTransactions(ctx, TxnFilter{View: "household", Month: "2026-07", CategoryID: &groceries})
	if err != nil {
		t.Fatalf("list groceries: %v", err)
	}
	if len(rows) != 1 || rows[0].ID != id {
		t.Fatalf("split-part filter should match parent, got %+v", rows)
	}
}

func TestListTransactionsUnsplitStillMatchesOwnCategory(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedSplitTxn(t, s, "unsplit-1", "-25.00")
	dining := catID(t, s, "Dining")
	if err := s.UpdateTransaction(ctx, id, TxnPatch{CategoryID: &dining}); err != nil {
		t.Fatalf("categorize: %v", err)
	}
	rows, err := s.ListTransactions(ctx, TxnFilter{View: "household", Month: "2026-07", CategoryID: &dining})
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if len(rows) != 1 || rows[0].ID != id {
		t.Fatalf("unsplit txn should match its own category, got %+v", rows)
	}
}

func TestGetTransaction(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedSplitTxn(t, s, "get-1", "-50.00")
	if err := s.ReplaceSplits(ctx, id, []SplitInput{
		{CategoryID: catID(t, s, "Dining"), Amount: "-30.00"},
		{CategoryID: catID(t, s, "Groceries"), Amount: "-20.00"},
	}); err != nil {
		t.Fatalf("replace: %v", err)
	}

	txn, err := s.GetTransaction(ctx, id)
	if err != nil {
		t.Fatalf("GetTransaction: %v", err)
	}
	if txn.ID != id || txn.Amount != "-50.00" || len(txn.Splits) != 2 {
		t.Fatalf("wrong txn: %+v", txn)
	}

	if _, err := s.GetTransaction(ctx, 999999); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound for missing id, got %v", err)
	}
}
```

Note: check how `UpdateTransaction` and its patch type are actually named in `internal/store/query.go` before writing these tests — the category-set pattern above (`TxnPatch{CategoryID: &dining}`) must match the existing signature used by `handlePatchTransaction`. If the existing method takes different arguments (e.g. `UpdateTransaction(ctx, id, catID *int, ownerOverride *string)`), adapt these two call sites; everything else stands.

- [ ] **Step 2: Run tests to verify they fail**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/store/ -run 'TestListTransactions|TestGetTransaction' -v
```

Expected: COMPILE FAILURE — `rows[i].Splits undefined` and `undefined: s.GetTransaction`.

- [ ] **Step 3: Implement in `internal/store/query.go`**

3a. Add `"github.com/jackc/pgx/v5"` to the import block.

3b. Add the field to `TxnRow`, immediately after `TransferPeerID`:

```go
	Splits []SplitRow `json:"splits"`
```

3c. Replace the category-filter clause in `ListTransactions` (currently at query.go:70-73):

```go
	if f.CategoryID != nil {
		args = append(args, *f.CategoryID)
		sb.WriteString(fmt.Sprintf(" AND t.category_id = $%d", len(args)))
	}
```

with the split-aware version (one arg, placeholder used twice):

```go
	if f.CategoryID != nil {
		args = append(args, *f.CategoryID)
		sb.WriteString(fmt.Sprintf(` AND ((t.category_id = $%d AND NOT EXISTS (
				SELECT 1 FROM transaction_splits sp WHERE sp.transaction_id = t.id))
			OR EXISTS (
				SELECT 1 FROM transaction_splits sp
				WHERE sp.transaction_id = t.id AND sp.category_id = $%d))`, len(args), len(args)))
	}
```

3d. At the end of `ListTransactions`, after the scan loop completes (and after `rows.Err()` is checked), attach splits in one batch:

```go
	ids := make([]int64, len(out))
	for i := range out {
		ids[i] = out[i].ID
	}
	splits, err := s.SplitsByTxnIDs(ctx, ids)
	if err != nil {
		return nil, err
	}
	for i := range out {
		if sp, ok := splits[out[i].ID]; ok {
			out[i].Splits = sp
		} else {
			out[i].Splits = []SplitRow{}
		}
	}
	return out, nil
```

(Adjust the existing final `return` accordingly — the function previously returned right after the scan loop.)

3e. Add `GetTransaction` (same 14 columns and order as `ListTransactions`'s SELECT):

```go
// GetTransaction fetches one transaction by id with splits attached.
func (s *Store) GetTransaction(ctx context.Context, id int64) (*TxnRow, error) {
	var r TxnRow
	err := s.Pool.QueryRow(ctx, `
		SELECT t.id, t.source, t.account_id, a.name, to_char(t.posted,'YYYY-MM-DD'),
		       t.amount::text, t.description, t.payee, t.pending,
		       t.category_id, c.name, t.owner_override,
		       COALESCE(t.owner_override, a.owner), t.transfer_peer_id
		FROM transactions t
		JOIN accounts a ON a.id = t.account_id
		LEFT JOIN categories c ON c.id = t.category_id
		WHERE t.id = $1`, id).Scan(
		&r.ID, &r.Source, &r.AccountID, &r.AccountName, &r.Posted,
		&r.Amount, &r.Description, &r.Payee, &r.Pending,
		&r.CategoryID, &r.CategoryName, &r.OwnerOverride,
		&r.EffectiveOwner, &r.TransferPeerID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	splits, err := s.SplitsByTxnIDs(ctx, []int64{id})
	if err != nil {
		return nil, err
	}
	if sp, ok := splits[id]; ok {
		r.Splits = sp
	} else {
		r.Splits = []SplitRow{}
	}
	return &r, nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/store/ -v
```

Expected: PASS — new tests plus all pre-existing (existing ListTransactions tests confirm the `[]SplitRow{}` default doesn't break scans).

- [ ] **Step 5: Commit**

```bash
git add internal/store/query.go internal/store/query_test.go
git commit -m "feat(store): split-aware transaction listing, category filter, GetTransaction

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 4: Sync stale-split cleanup

**Files:**
- Modify: `internal/ingest/sync.go`
- Test: `internal/ingest/sync_test.go` (append)

- [ ] **Step 1: Write the failing test**

Append to `internal/ingest/sync_test.go` (uses the ingest package's `testDB(t) *store.Store` helper from `testdb_test.go:17`):

```go
func TestCleanStaleSplits(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()

	if err := s.UpsertAccounts(ctx, []store.Account{{
		ID: "acct-stale", Name: "Stale Test", Org: "test", Owner: "scott",
	}}); err != nil {
		t.Fatalf("seed account: %v", err)
	}
	if _, err := s.UpsertTransactions(ctx, []store.Txn{{
		Source: "simplefin", ExternalID: "stale-1", AccountID: "acct-stale",
		Posted: time.Date(2026, 7, 10, 0, 0, 0, 0, time.UTC),
		Amount: "-50.00", Description: "stale split test", Payee: "STALE TEST",
	}}); err != nil {
		t.Fatalf("seed txn: %v", err)
	}
	var id int64
	if err := s.Pool.QueryRow(ctx,
		`SELECT id FROM transactions WHERE source='simplefin' AND external_id='stale-1'`).Scan(&id); err != nil {
		t.Fatalf("lookup id: %v", err)
	}
	var dining, groceries int
	if err := s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Dining'`).Scan(&dining); err != nil {
		t.Fatalf("dining id: %v", err)
	}
	if err := s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Groceries'`).Scan(&groceries); err != nil {
		t.Fatalf("groceries id: %v", err)
	}
	if err := s.ReplaceSplits(ctx, id, []store.SplitInput{
		{CategoryID: dining, Amount: "-30.00"},
		{CategoryID: groceries, Amount: "-20.00"},
	}); err != nil {
		t.Fatalf("replace: %v", err)
	}

	// Consistent splits survive cleanup.
	n, err := CleanStaleSplits(ctx, s)
	if err != nil {
		t.Fatalf("clean (consistent): %v", err)
	}
	if n != 0 {
		t.Fatalf("want 0 deleted while consistent, got %d", n)
	}

	// Simulate a sync re-upsert changing the parent amount → splits are stale.
	if _, err := s.Pool.Exec(ctx, `UPDATE transactions SET amount = '-60.00' WHERE id = $1`, id); err != nil {
		t.Fatalf("mutate amount: %v", err)
	}
	n, err = CleanStaleSplits(ctx, s)
	if err != nil {
		t.Fatalf("clean (stale): %v", err)
	}
	if n != 2 {
		t.Fatalf("want 2 stale split rows deleted, got %d", n)
	}
	var remain int
	if err := s.Pool.QueryRow(ctx,
		`SELECT count(*) FROM transaction_splits WHERE transaction_id = $1`, id).Scan(&remain); err != nil {
		t.Fatalf("count: %v", err)
	}
	if remain != 0 {
		t.Fatalf("stale splits remain: %d", remain)
	}
}
```

If `sync_test.go`'s import block lacks any of `context`, `time`, or the `store` package import, add them.

- [ ] **Step 2: Run test to verify it fails**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/ingest/ -run TestCleanStaleSplits -v
```

Expected: COMPILE FAILURE — `undefined: CleanStaleSplits`.

- [ ] **Step 3: Implement in `internal/ingest/sync.go`**

3a. Add the function:

```go
// CleanStaleSplits deletes split sets whose sum no longer equals the parent
// amount — the parent was re-upserted with a changed amount by sync.
func CleanStaleSplits(ctx context.Context, s *store.Store) (int, error) {
	tag, err := s.Pool.Exec(ctx, `DELETE FROM transaction_splits
		WHERE transaction_id IN (
		  SELECT sp.transaction_id
		  FROM transaction_splits sp
		  JOIN transactions t ON t.id = sp.transaction_id
		  GROUP BY sp.transaction_id, t.amount
		  HAVING SUM(sp.amount) <> t.amount)`)
	if err != nil {
		return 0, err
	}
	return int(tag.RowsAffected()), nil
}
```

3b. Extend `SyncResult`:

```go
type SyncResult struct {
	Upserted      int
	Categorized   int
	Paired        int
	Swept         int
	SplitsDeleted int
}
```

3c. In `Sync`, immediately after the `UpsertTransactions` block (before `ApplyRules`):

```go
	if res.SplitsDeleted, err = CleanStaleSplits(ctx, s); err != nil {
		return fail(err)
	}
```

3d. Replace the status/detail assembly at the end of `Sync` (currently `status := "ok"; detail := ""; if len(set.Errors) > 0 { status = "partial"; detail = strings.Join(set.Errors, "; ") }`) with:

```go
	status := "ok"
	var details []string
	if res.SplitsDeleted > 0 {
		details = append(details, fmt.Sprintf("stale splits deleted: %d", res.SplitsDeleted))
	}
	if len(set.Errors) > 0 {
		status = "partial"
		details = append(details, set.Errors...)
	}
	detail := strings.Join(details, "; ")
```

(`fmt` and `strings` are already imported in sync.go.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/ingest/ -v
```

Expected: PASS — new test plus every pre-existing ingest test (existing `Sync` tests don't assert on the exact zero-value detail string, and `SplitsDeleted=0` keeps detail unchanged for them; if one does string-compare an empty detail, it still passes because no splits exist in its fixtures).

- [ ] **Step 5: Commit**

```bash
git add internal/ingest/sync.go internal/ingest/sync_test.go
git commit -m "feat(ingest): delete stale splits when sync changes a parent amount

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 5: Split API endpoints — `PUT`/`DELETE /api/transactions/{id}/splits`

**Files:**
- Create: `internal/api/splits.go`
- Create: `internal/api/splits_test.go`
- Modify: `internal/api/server.go` (routes)

- [ ] **Step 1: Write the failing tests**

Create `internal/api/splits_test.go` (api package; uses the api test helpers `testStore`, `seedTxn`, `mustDate`):

```go
package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func putSplitsReq(t *testing.T, h http.Handler, id string, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPut, "/api/transactions/"+id+"/splits",
		bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	return w
}

func splitCatIDs(t *testing.T, s *store) (int, int) { // adjust: see note below
	t.Helper()
	return 0, 0
}

func TestPutSplits(t *testing.T) {
	s := testStore(t)
	h := New(s).Handler()
	ctx := t.Context()

	id := seedTxn(t, s, "acct-sp", "scott", "sp-1",
		time.Date(2026, 7, 10, 0, 0, 0, 0, time.UTC), "-50.00", "venmo dinner")

	var dining, groceries int
	if err := s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Dining'`).Scan(&dining); err != nil {
		t.Fatalf("dining: %v", err)
	}
	if err := s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Groceries'`).Scan(&groceries); err != nil {
		t.Fatalf("groceries: %v", err)
	}

	body := func(a, b string) string {
		return `{"splits":[{"category_id":` + itoa(dining) + `,"amount":"` + a +
			`","note":""},{"category_id":` + itoa(groceries) + `,"amount":"` + b + `","note":""}]}`
	}

	t.Run("happy path returns updated transaction", func(t *testing.T) {
		w := putSplitsReq(t, h, itoa64(id), body("-30.00", "-20.00"))
		if w.Code != http.StatusOK {
			t.Fatalf("status %d: %s", w.Code, w.Body.String())
		}
		var resp struct {
			Transaction struct {
				ID     int64 `json:"id"`
				Splits []struct {
					Category string `json:"category"`
					Amount   string `json:"amount"`
				} `json:"splits"`
			} `json:"transaction"`
		}
		if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
			t.Fatalf("decode: %v", err)
		}
		if resp.Transaction.ID != id || len(resp.Transaction.Splits) != 2 {
			t.Fatalf("bad envelope: %s", w.Body.String())
		}
	})

	t.Run("sum mismatch is 400 with amounts in message", func(t *testing.T) {
		w := putSplitsReq(t, h, itoa64(id), body("-30.00", "-15.00"))
		if w.Code != http.StatusBadRequest {
			t.Fatalf("status %d", w.Code)
		}
		var e struct {
			Error string `json:"error"`
		}
		if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil {
			t.Fatalf("decode: %v", err)
		}
		if e.Error == "" {
			t.Fatal("empty error message")
		}
	})

	t.Run("non-integer id is 400", func(t *testing.T) {
		w := putSplitsReq(t, h, "abc", body("-30.00", "-20.00"))
		if w.Code != http.StatusBadRequest {
			t.Fatalf("status %d", w.Code)
		}
	})

	t.Run("unknown transaction is 404", func(t *testing.T) {
		w := putSplitsReq(t, h, "999999", body("-30.00", "-20.00"))
		if w.Code != http.StatusNotFound {
			t.Fatalf("status %d", w.Code)
		}
	})

	t.Run("bad JSON is 400", func(t *testing.T) {
		w := putSplitsReq(t, h, itoa64(id), `{"splits": nope}`)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("status %d", w.Code)
		}
	})

	t.Run("unknown category is 400", func(t *testing.T) {
		w := putSplitsReq(t, h, itoa64(id),
			`{"splits":[{"category_id":424242,"amount":"-30.00","note":""},{"category_id":424243,"amount":"-20.00","note":""}]}`)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("status %d: %s", w.Code, w.Body.String())
		}
	})
}

func TestDeleteSplits(t *testing.T) {
	s := testStore(t)
	h := New(s).Handler()

	id := seedTxn(t, s, "acct-del", "scott", "del-1",
		time.Date(2026, 7, 10, 0, 0, 0, 0, time.UTC), "-50.00", "delete me")

	req := httptest.NewRequest(http.MethodDelete, "/api/transactions/"+itoa64(id)+"/splits", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	// Idempotent — second delete also 200.
	w = httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodDelete, "/api/transactions/"+itoa64(id)+"/splits", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("second delete status %d", w.Code)
	}
}
```

Implementation notes for the engineer writing this test file:
- Delete the `splitCatIDs` stub above — it exists only in this plan text as a marker; the tests query category ids inline.
- `itoa`/`itoa64` may not exist in the api test package. Check first; if absent, add to this file:

```go
func itoa(n int) string   { return strconv.Itoa(n) }
func itoa64(n int64) string { return strconv.FormatInt(n, 10) }
```

  and import `strconv`. If `t.Context()` is unavailable at the repo's Go version, use `context.Background()` and import `context`.
- Check the actual `testStore`/`seedTxn` signatures in the api package's existing test helper file before writing — match them exactly (the table in the Context section records `seedTxn(t, s, acct, owner, extID, posted, amount, desc) int64`).

- [ ] **Step 2: Run tests to verify they fail**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/api/ -run 'TestPutSplits|TestDeleteSplits' -v
```

Expected: FAIL — 404s from the mux (routes not registered).

- [ ] **Step 3: Implement `internal/api/splits.go`**

```go
package api

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strconv"

	"github.com/jackc/pgx/v5/pgconn"

	"vollmint/internal/store"
)

func (s *Server) registerSplits() {
	s.mux.HandleFunc("PUT /api/transactions/{id}/splits", s.handlePutSplits)
	s.mux.HandleFunc("DELETE /api/transactions/{id}/splits", s.handleDeleteSplits)
}

func (s *Server) handlePutSplits(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		writeErr(w, http.StatusBadRequest, "id must be an integer")
		return
	}
	var body struct {
		Splits []store.SplitInput `json:"splits"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	err = s.store.ReplaceSplits(r.Context(), id, body.Splits)
	switch {
	case err == nil:
	case errors.Is(err, store.ErrNotFound):
		writeErr(w, http.StatusNotFound, "transaction not found")
		return
	case errors.Is(err, store.ErrSplitTooFew),
		errors.Is(err, store.ErrSplitParent),
		errors.Is(err, store.ErrSplitSign),
		errors.Is(err, store.ErrInvalidAmount):
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	default:
		var sumErr *store.SplitSumError
		if errors.As(err, &sumErr) {
			writeErr(w, http.StatusBadRequest, sumErr.Error())
			return
		}
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23503" {
			writeErr(w, http.StatusBadRequest, "unknown category_id")
			return
		}
		log.Printf("put splits: %v", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}

	txn, err := s.store.GetTransaction(r.Context(), id)
	if err != nil {
		log.Printf("get transaction after split: %v", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"transaction": txn})
}

func (s *Server) handleDeleteSplits(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		writeErr(w, http.StatusBadRequest, "id must be an integer")
		return
	}
	if err := s.store.DeleteSplits(r.Context(), id); err != nil {
		log.Printf("delete splits: %v", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
```

Adjust the module path in the `store` import to match the repo's actual module name (check `go.mod` — the existing files in `internal/api` import it already; copy their import line). The error-variable names (`ErrSplitTooFew`, `ErrSplitParent`, `ErrSplitSign`, `SplitSumError`) are defined in Task 2's `internal/store/splits.go`.

In `internal/api/server.go`, in `routes()`, add after `s.registerTransactions()`:

```go
	s.registerSplits()
```

(`s.registerStatic()` must remain last.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/api/ -v
```

Expected: PASS — all new subtests plus every pre-existing api test.

- [ ] **Step 5: Commit**

```bash
git add internal/api/splits.go internal/api/splits_test.go internal/api/server.go
git commit -m "feat(api): PUT/DELETE /api/transactions/{id}/splits

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 6: Split-aware report aggregates — `SpendByCategory` + `Summary` vices

**Files:**
- Modify: `internal/report/report.go`
- Test: `internal/report/report_test.go` (append)

- [ ] **Step 1: Write the failing tests**

Append to `internal/report/report_test.go`. These use the report package's `testStore` and `seedSpend` helpers, plus `store.SplitInput`/`ReplaceSplits` directly:

```go
func TestSpendByCategorySplitAware(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()

	// One -50.00 Dining transaction, split -30 Dining / -20 Groceries.
	seedSpend(t, s, "acct-split", "scott", "split-rpt-1",
		time.Date(2026, 7, 10, 0, 0, 0, 0, time.UTC), "-50.00", "Dining")

	var id int64
	if err := s.Pool.QueryRow(ctx,
		`SELECT id FROM transactions WHERE external_id='split-rpt-1'`).Scan(&id); err != nil {
		t.Fatalf("lookup: %v", err)
	}
	var dining, groceries int
	if err := s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Dining'`).Scan(&dining); err != nil {
		t.Fatalf("dining: %v", err)
	}
	if err := s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Groceries'`).Scan(&groceries); err != nil {
		t.Fatalf("groceries: %v", err)
	}
	if err := s.ReplaceSplits(ctx, id, []store.SplitInput{
		{CategoryID: dining, Amount: "-30.00"},
		{CategoryID: groceries, Amount: "-20.00"},
	}); err != nil {
		t.Fatalf("replace: %v", err)
	}

	rows, err := SpendByCategory(ctx, s, "household", "2026-07")
	if err != nil {
		t.Fatalf("SpendByCategory: %v", err)
	}
	got := map[string]string{}
	for _, r := range rows {
		got[r.Category] = r.Spent
	}
	if got["Dining"] != "30.00" {
		t.Fatalf("Dining = %q, want 30.00 (split part, not parent 50.00)", got["Dining"])
	}
	if got["Groceries"] != "20.00" {
		t.Fatalf("Groceries = %q, want 20.00", got["Groceries"])
	}
}

func TestSummaryVicesSplitAware(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()

	// -50.00 categorized Dining (a vice). Split: -30 Dining (vice) / -20 Groceries (not).
	seedSpend(t, s, "acct-vice", "scott", "vice-1",
		time.Date(2026, 7, 12, 0, 0, 0, 0, time.UTC), "-50.00", "Dining")
	var id int64
	if err := s.Pool.QueryRow(ctx,
		`SELECT id FROM transactions WHERE external_id='vice-1'`).Scan(&id); err != nil {
		t.Fatalf("lookup: %v", err)
	}
	var dining, groceries int
	if err := s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Dining'`).Scan(&dining); err != nil {
		t.Fatalf("dining: %v", err)
	}
	if err := s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Groceries'`).Scan(&groceries); err != nil {
		t.Fatalf("groceries: %v", err)
	}
	if err := s.ReplaceSplits(ctx, id, []store.SplitInput{
		{CategoryID: dining, Amount: "-30.00"},
		{CategoryID: groceries, Amount: "-20.00"},
	}); err != nil {
		t.Fatalf("replace: %v", err)
	}

	sum, err := Summary(ctx, s, "household", "2026-07")
	if err != nil {
		t.Fatalf("Summary: %v", err)
	}
	if sum.Out != "50.00" {
		t.Fatalf("Out = %q, want 50.00 — split must not change totals", sum.Out)
	}
	if sum.Vices != "30.00" {
		t.Fatalf("Vices = %q, want 30.00 — only the Dining part is a vice", sum.Vices)
	}
}
```

Adapt the exact call signatures (`SpendByCategory(ctx, s, view, month)`, `Summary(...)`, row field names `Category`/`Spent`, summary fields `Out`/`Vices`) to the real ones in `internal/report/report.go` — read the function signatures before writing; existing report tests in the same file show the calling convention. Add missing imports (`context`, `time`, and the repo's `internal/store` package) if the file lacks them. `Dining` is seeded `is_vice=true` in the 0001 migration.

- [ ] **Step 2: Run tests to verify they fail**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/report/ -run 'TestSpendByCategorySplitAware|TestSummaryVicesSplitAware' -v
```

Expected: FAIL — `Dining = "50.00", want 30.00` (parent attribution) and `Vices = "50.00", want 30.00`.

- [ ] **Step 3: Implement in `internal/report/report.go`**

3a. `SpendByCategory`: replace the aggregate query. Current shape joins `categories c ON c.id = t.category_id` and sums `(-SUM(t.amount))::text`. New query (preserve the function's existing owner-filter interpolation — shown as `[+ own]` where `ownerFilter`'s fragment is appended, exactly as the current code does):

```sql
SELECT c.id, c.name, (-SUM(COALESCE(sp.amount, t.amount)))::text, c.is_vice,
       COALESCE(b.amount::text, '')
FROM transactions t
JOIN accounts a ON a.id = t.account_id
LEFT JOIN transaction_splits sp ON sp.transaction_id = t.id
JOIN categories c ON c.id = COALESCE(sp.category_id, t.category_id)
LEFT JOIN budgets b ON b.category_id = c.id AND b.month = $1::date
WHERE t.amount < 0
  AND t.posted >= $1::date AND t.posted < ($1::date + interval '1 month')
  AND t.transfer_peer_id IS NULL AND c.kind <> 'transfer' [+ own]
GROUP BY c.id, c.name, c.is_vice, b.amount
ORDER BY (-SUM(COALESCE(sp.amount, t.amount))) DESC, c.name
```

Split parts share the parent's sign, so `t.amount < 0` correctly gates both split and unsplit rows. Keep the current scan loop unchanged — the column list and order are identical to before.

3b. `Summary`: the current single query computes In/Out/Vices with a `FILTER (WHERE c.is_vice ...)` column. Change it to two queries:

- Main query: drop the Vices column and its category join if it was only needed for vices; scan only In and Out (raw transaction rows — parts sum to the parent so totals are unaffected by splits).
- Second query for Vices, split-aware:

```sql
SELECT COALESCE(-SUM(COALESCE(sp.amount, t.amount)), 0)::text
FROM transactions t
JOIN accounts a ON a.id = t.account_id
LEFT JOIN transaction_splits sp ON sp.transaction_id = t.id
JOIN categories c ON c.id = COALESCE(sp.category_id, t.category_id)
WHERE c.is_vice AND t.amount < 0
  AND t.posted >= $1::date AND t.posted < ($1::date + interval '1 month')
  AND t.transfer_peer_id IS NULL AND c.kind <> 'transfer' [+ own]
```

Both queries take `$1 = month + "-01"` plus the ownerFilter args — build once with `full := append([]any{month + "-01"}, args...)` and pass to both. Wrap errors distinctly: `fmt.Errorf("summary totals: %w", err)` / `fmt.Errorf("summary vices: %w", err)`. Leave the budget-total part of Summary (if present) untouched. `MonthlyFlow` and `Recurring` are deliberately untouched.

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/report/ -v
```

Expected: PASS — the two new tests plus every pre-existing report test (unsplit fixtures behave identically under the LEFT JOIN because `sp.*` is NULL and COALESCE falls through).

- [ ] **Step 5: Commit**

```bash
git add internal/report/report.go internal/report/report_test.go
git commit -m "feat(report): split-aware category spend and vices totals

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 7: Money/text helpers — `internal/report/text.go`

**Files:**
- Create: `internal/report/text.go`
- Create: `internal/report/text_test.go`

Pure functions, no DB. These are used by Tasks 10 (insight card bodies) and 8 indirectly.

- [ ] **Step 1: Write the failing tests**

Create `internal/report/text_test.go`:

```go
package report

import "testing"

func TestCents(t *testing.T) {
	cases := []struct {
		in   string
		want int64
	}{
		{"0", 0},
		{"1", 100},
		{"1.5", 150},
		{"1.50", 150},
		{"-50.00", -5000},
		{"-0.01", -1},
		{"1234.56", 123456},
		{"128.42", 12842},
	}
	for _, c := range cases {
		if got := cents(c.in); got != c.want {
			t.Errorf("cents(%q) = %d, want %d", c.in, got, c.want)
		}
	}
}

func TestCentsToDec(t *testing.T) {
	cases := []struct {
		in   int64
		want string
	}{
		{0, "0.00"},
		{150, "1.50"},
		{-5000, "-50.00"},
		{-1, "-0.01"},
		{123456, "1234.56"},
	}
	for _, c := range cases {
		if got := centsToDec(c.in); got != c.want {
			t.Errorf("centsToDec(%d) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestUSD(t *testing.T) {
	cases := []struct {
		in   int64
		want string
	}{
		{0, "$0.00"},
		{150, "$1.50"},
		{123456, "$1,234.56"},
		{100000000, "$1,000,000.00"},
		{-5000, "-$50.00"},
	}
	for _, c := range cases {
		if got := usd(c.in); got != c.want {
			t.Errorf("usd(%d) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestTitleCase(t *testing.T) {
	cases := []struct{ in, want string }{
		{"VERIZON WIRELESS", "Verizon Wireless"},
		{"netflix", "Netflix"},
		{"", ""},
		{"APPLE TV+", "Apple Tv+"},
	}
	for _, c := range cases {
		if got := titleCase(c.in); got != c.want {
			t.Errorf("titleCase(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/report/ -run 'TestCents|TestCentsToDec|TestUSD|TestTitleCase' -v
```

Expected: COMPILE FAILURE — `undefined: cents` etc.

- [ ] **Step 3: Implement `internal/report/text.go`**

```go
package report

import (
	"strconv"
	"strings"
	"unicode"
)

// cents parses a decimal money string ("−50.00", "1.5") into integer cents.
// Inputs come from Postgres numeric ::text casts, so they are well-formed;
// malformed input returns 0.
func cents(s string) int64 {
	neg := strings.HasPrefix(s, "-")
	s = strings.TrimPrefix(s, "-")
	whole, frac, _ := strings.Cut(s, ".")
	if len(frac) > 2 {
		frac = frac[:2]
	}
	for len(frac) < 2 {
		frac += "0"
	}
	w, err := strconv.ParseInt(whole, 10, 64)
	if err != nil {
		return 0
	}
	f, err := strconv.ParseInt(frac, 10, 64)
	if err != nil {
		return 0
	}
	c := w*100 + f
	if neg {
		c = -c
	}
	return c
}

// centsToDec renders integer cents as a plain decimal string ("-50.00").
func centsToDec(c int64) string {
	neg := c < 0
	if neg {
		c = -c
	}
	s := strconv.FormatInt(c/100, 10) + "." + pad2(c%100)
	if neg {
		return "-" + s
	}
	return s
}

// usd renders integer cents as "$1,234.56" (negatives as "-$…").
func usd(c int64) string {
	neg := c < 0
	if neg {
		c = -c
	}
	whole := strconv.FormatInt(c/100, 10)
	var b strings.Builder
	lead := len(whole) % 3
	if lead == 0 {
		lead = 3
	}
	b.WriteString(whole[:lead])
	for i := lead; i < len(whole); i += 3 {
		b.WriteByte(',')
		b.WriteString(whole[i : i+3])
	}
	out := "$" + b.String() + "." + pad2(c%100)
	if neg {
		return "-" + out
	}
	return out
}

func pad2(n int64) string {
	if n < 10 {
		return "0" + strconv.FormatInt(n, 10)
	}
	return strconv.FormatInt(n, 10)
}

// titleCase lowercases then capitalizes the first rune of each word.
func titleCase(s string) string {
	words := strings.Fields(strings.ToLower(s))
	for i, w := range words {
		r := []rune(w)
		r[0] = unicode.ToUpper(r[0])
		words[i] = string(r)
	}
	return strings.Join(words, " ")
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/report/ -run 'TestCents|TestCentsToDec|TestUSD|TestTitleCase' -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/report/text.go internal/report/text_test.go
git commit -m "feat(report): integer-cents money helpers and titleCase

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 8: Bill forecasting — `internal/report.Forecast`

**Files:**
- Create: `internal/report/forecast.go`
- Create: `internal/report/forecast_test.go`

- [ ] **Step 1: Write the failing tests**

Create `internal/report/forecast_test.go`. It defines `seedBill` — a variant of `seedSpend` with an explicit payee (`seedSpend` sets `Payee: extID`, which makes same-payee-across-months data impossible). Copy `seedSpend`'s body and change only the `Payee` field:

```go
package report

import (
	"context"
	"testing"
	"time"
)

// seedBill seeds one categorized transaction with an explicit payee.
// Mirrors seedSpend but payee is a parameter so the same payee can recur
// across months (forecast detection groups by payee).
func seedBill(t *testing.T, s *storeT, acct, owner, extID string, posted time.Time, amount, payee, catName string) {
	t.Helper()
	// Copy the body of seedSpend from this package's test helpers, with
	// Payee: payee instead of Payee: extID. Keep account upsert, txn upsert,
	// and the categories UPDATE identical.
	_ = catName
}

func TestForecastDetectsMonthlyBill(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()

	// VERIZON: charged on ~the 14th in Apr, May, Jun — 3 distinct months,
	// present in 2+ of the 3 months before July.
	seedBill(t, s, "acct-fc", "scott", "vz-apr", time.Date(2026, 4, 14, 0, 0, 0, 0, time.UTC), "-120.00", "VERIZON WIRELESS", "Utilities")
	seedBill(t, s, "acct-fc", "scott", "vz-may", time.Date(2026, 5, 15, 0, 0, 0, 0, time.UTC), "-121.00", "VERIZON WIRELESS", "Utilities")
	seedBill(t, s, "acct-fc", "scott", "vz-jun", time.Date(2026, 6, 13, 0, 0, 0, 0, time.UTC), "-128.42", "VERIZON WIRELESS", "Utilities")

	f, err := Forecast(ctx, s, "household", "2026-07")
	if err != nil {
		t.Fatalf("Forecast: %v", err)
	}
	if len(f.Bills) != 1 {
		t.Fatalf("want 1 bill, got %d: %+v", len(f.Bills), f.Bills)
	}
	b := f.Bills[0]
	if b.Payee != "VERIZON WIRELESS" || b.Category != "Utilities" {
		t.Fatalf("wrong bill: %+v", b)
	}
	if b.PredictedDay != 14 {
		t.Fatalf("predicted day %d, want 14 (median of 14,15,13)", b.PredictedDay)
	}
	if b.ExpectedAmount != "128.42" {
		t.Fatalf("expected amount %q, want latest charge 128.42", b.ExpectedAmount)
	}
	if b.Paid {
		t.Fatal("no July charge — should be unpaid")
	}
	if f.RemainingExpected != "128.42" {
		t.Fatalf("remaining %q, want 128.42", f.RemainingExpected)
	}
}

func TestForecastExcludesDeadAndP2P(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()

	// Dead: 3 months long ago, nothing in the 3 months before July.
	seedBill(t, s, "acct-fc", "scott", "gym-1", time.Date(2025, 10, 5, 0, 0, 0, 0, time.UTC), "-40.00", "OLD GYM", "Health")
	seedBill(t, s, "acct-fc", "scott", "gym-2", time.Date(2025, 11, 5, 0, 0, 0, 0, time.UTC), "-40.00", "OLD GYM", "Health")
	seedBill(t, s, "acct-fc", "scott", "gym-3", time.Date(2025, 12, 5, 0, 0, 0, 0, time.UTC), "-40.00", "OLD GYM", "Health")

	// P2P: monthly Venmo, would otherwise qualify.
	seedBill(t, s, "acct-fc", "scott", "vm-1", time.Date(2026, 4, 3, 0, 0, 0, 0, time.UTC), "-25.00", "Venmo Payment", "Dining")
	seedBill(t, s, "acct-fc", "scott", "vm-2", time.Date(2026, 5, 3, 0, 0, 0, 0, time.UTC), "-25.00", "Venmo Payment", "Dining")
	seedBill(t, s, "acct-fc", "scott", "vm-3", time.Date(2026, 6, 3, 0, 0, 0, 0, time.UTC), "-25.00", "Venmo Payment", "Dining")

	f, err := Forecast(ctx, s, "household", "2026-07")
	if err != nil {
		t.Fatalf("Forecast: %v", err)
	}
	if len(f.Bills) != 0 {
		t.Fatalf("want 0 bills (dead + P2P excluded), got %+v", f.Bills)
	}
	if f.RemainingExpected != "0" {
		t.Fatalf("remaining %q, want 0", f.RemainingExpected)
	}
}

func TestForecastPaidMatching(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()

	seedBill(t, s, "acct-fc", "scott", "nf-apr", time.Date(2026, 4, 20, 0, 0, 0, 0, time.UTC), "-17.99", "NETFLIX", "Subscriptions")
	seedBill(t, s, "acct-fc", "scott", "nf-may", time.Date(2026, 5, 20, 0, 0, 0, 0, time.UTC), "-17.99", "NETFLIX", "Subscriptions")
	seedBill(t, s, "acct-fc", "scott", "nf-jun", time.Date(2026, 6, 20, 0, 0, 0, 0, time.UTC), "-17.99", "NETFLIX", "Subscriptions")
	// Paid this month on the 19th.
	seedBill(t, s, "acct-fc", "scott", "nf-jul", time.Date(2026, 7, 19, 0, 0, 0, 0, time.UTC), "-17.99", "NETFLIX", "Subscriptions")

	// A second, unpaid bill so ordering is observable.
	seedBill(t, s, "acct-fc", "scott", "sp-apr", time.Date(2026, 4, 25, 0, 0, 0, 0, time.UTC), "-11.99", "SPOTIFY", "Subscriptions")
	seedBill(t, s, "acct-fc", "scott", "sp-may", time.Date(2026, 5, 25, 0, 0, 0, 0, time.UTC), "-11.99", "SPOTIFY", "Subscriptions")
	seedBill(t, s, "acct-fc", "scott", "sp-jun", time.Date(2026, 6, 25, 0, 0, 0, 0, time.UTC), "-11.99", "SPOTIFY", "Subscriptions")

	f, err := Forecast(ctx, s, "household", "2026-07")
	if err != nil {
		t.Fatalf("Forecast: %v", err)
	}
	if len(f.Bills) != 2 {
		t.Fatalf("want 2 bills, got %+v", f.Bills)
	}
	// Unpaid first, paid sinks below.
	if f.Bills[0].Payee != "SPOTIFY" || f.Bills[0].Paid {
		t.Fatalf("first bill should be unpaid SPOTIFY: %+v", f.Bills[0])
	}
	nf := f.Bills[1]
	if !nf.Paid || nf.PaidDate != "2026-07-19" || nf.PaidAmount != "17.99" {
		t.Fatalf("NETFLIX paid fields wrong: %+v", nf)
	}
	if f.RemainingExpected != "11.99" {
		t.Fatalf("remaining %q, want 11.99 (only Spotify unpaid)", f.RemainingExpected)
	}
}
```

`storeT` in the `seedBill` signature is a stand-in for whatever type `testStore` returns in this package (`*store.Store`) — use the real type and delete the `_ = catName` line once the body is copied in from `seedSpend`.

- [ ] **Step 2: Run tests to verify they fail**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/report/ -run TestForecast -v
```

Expected: COMPILE FAILURE — `undefined: Forecast`.

- [ ] **Step 3: Implement `internal/report/forecast.go`**

```go
package report

import (
	"context"
	"fmt"

	"vollmint/internal/store"
)

type ForecastBill struct {
	Payee          string `json:"payee"`
	CategoryID     *int   `json:"category_id"`
	Category       string `json:"category"`
	PredictedDay   int    `json:"predicted_day"`
	ExpectedAmount string `json:"expected_amount"`
	Paid           bool   `json:"paid"`
	PaidDate       string `json:"paid_date"`
	PaidAmount     string `json:"paid_amount"`
}

type ForecastResult struct {
	Month             string         `json:"month"`
	View              string         `json:"view"`
	Bills             []ForecastBill `json:"bills"`
	RemainingExpected string         `json:"remaining_expected"`
}

// Forecast predicts this month's recurring bills from payee history.
// P2P payees (Venmo/Zelle) are excluded — same-payee grouping is meaningless
// for them; splits are the tool there.
func Forecast(ctx context.Context, s *store.Store, view, month string) (ForecastResult, error) {
	res := ForecastResult{Month: month, View: view,
		Bills: []ForecastBill{}, RemainingExpected: "0"}

	own, args := ownerFilter(view, 2)
	full := append([]any{month + "-01"}, args...)

	rows, err := s.Pool.Query(ctx, `
WITH spend AS (
  SELECT t.payee, -t.amount AS mag, t.posted, t.pending,
         date_trunc('month', t.posted)::date AS m, t.category_id
  FROM transactions t
  JOIN accounts a ON a.id = t.account_id
  LEFT JOIN categories c ON c.id = t.category_id
  WHERE t.amount < 0 AND t.payee <> ''
    AND t.transfer_peer_id IS NULL
    AND (c.kind IS NULL OR c.kind <> 'transfer')
    AND t.payee NOT ILIKE '%venmo%' AND t.payee NOT ILIKE '%zelle%'`+own+`
),
hist AS (
  SELECT * FROM spend
  WHERE NOT pending AND posted < ($1::date + interval '1 month')
),
cadence AS (
  SELECT payee FROM hist GROUP BY payee
  HAVING count(DISTINCT m) >= 3
     AND count(DISTINCT m) FILTER (
           WHERE m >= ($1::date - interval '3 months') AND m < $1::date) >= 2
),
med AS (
  SELECT payee,
         round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(day FROM posted)))::int AS pday
  FROM hist
  WHERE posted >= ($1::date - interval '6 months') AND posted < $1::date
  GROUP BY payee
),
latest AS (
  SELECT DISTINCT ON (payee) payee, mag AS expected
  FROM hist ORDER BY payee, posted DESC
),
catmode AS (
  SELECT payee, mode() WITHIN GROUP (ORDER BY category_id) AS category_id
  FROM hist WHERE category_id IS NOT NULL GROUP BY payee
),
paid AS (
  SELECT DISTINCT ON (payee) payee, posted, mag
  FROM spend
  WHERE NOT pending AND posted >= $1::date AND posted < ($1::date + interval '1 month')
  ORDER BY payee, posted ASC
)
SELECT cad.payee, cm.category_id, COALESCE(c.name, ''),
       COALESCE(md.pday, 1), COALESCE(lt.expected::text, '0'),
       (p.payee IS NOT NULL),
       COALESCE(to_char(p.posted, 'YYYY-MM-DD'), ''), COALESCE(p.mag::text, ''),
       COALESCE(SUM(CASE WHEN p.payee IS NULL THEN lt.expected ELSE 0 END) OVER (), 0)::text
FROM cadence cad
LEFT JOIN med md ON md.payee = cad.payee
LEFT JOIN latest lt ON lt.payee = cad.payee
LEFT JOIN catmode cm ON cm.payee = cad.payee
LEFT JOIN categories c ON c.id = cm.category_id
LEFT JOIN paid p ON p.payee = cad.payee
ORDER BY (p.payee IS NOT NULL),
         CASE WHEN p.payee IS NULL THEN COALESCE(md.pday, 1) END,
         p.posted, cad.payee`, full...)
	if err != nil {
		return res, fmt.Errorf("forecast: %w", err)
	}
	defer rows.Close()

	for rows.Next() {
		var b ForecastBill
		var remaining string
		if err := rows.Scan(&b.Payee, &b.CategoryID, &b.Category,
			&b.PredictedDay, &b.ExpectedAmount, &b.Paid,
			&b.PaidDate, &b.PaidAmount, &remaining); err != nil {
			return res, fmt.Errorf("forecast scan: %w", err)
		}
		res.RemainingExpected = remaining
		res.Bills = append(res.Bills, b)
	}
	if err := rows.Err(); err != nil {
		return res, fmt.Errorf("forecast rows: %w", err)
	}
	return res, nil
}
```

Notes for the engineer:
- Fix the module path in the `store` import to match `go.mod` (copy from `report.go`).
- Check `ownerFilter`'s actual return contract in `report.go` — it returns a SQL fragment (starting with ` AND …`) plus args, with `$2`-based numbering here because `$1` is the month. Match how `Recurring`/`Summary` call it. If the fragment references the alias `a` (accounts) that's satisfied — the spend CTE joins accounts.
- `remaining_expected` uses a window `SUM(CASE …) OVER ()` because `FILTER` isn't allowed on window aggregates; every row carries the same total, and scanning it per-row is intentional. Zero result rows leave the default `"0"`.
- The numeric `::text` on the window sum may render `128.42` — but if the column renders with trailing zeros differently than expected (e.g. `128.4200`), cast as `round(...,2)::text`. Run the test; adjust only if it fails on formatting.

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/report/ -run TestForecast -v
```

Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add internal/report/forecast.go internal/report/forecast_test.go
git commit -m "feat(report): recurring-bill forecast with cadence detection

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 9: `GET /api/forecast`

**Files:**
- Create: `internal/api/forecast.go`
- Create: `internal/api/forecast_test.go`
- Modify: `internal/api/server.go` (routes)

- [ ] **Step 1: Write the failing tests**

Create `internal/api/forecast_test.go`:

```go
package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestGetForecast(t *testing.T) {
	s := testStore(t)
	h := New(s).Handler()

	// Three months of the same payee → forecastable. seedTxn sets payee from
	// desc-or-extID depending on helper; if seedTxn can't set an explicit
	// payee, insert via s.UpsertTransactions directly (mirror Task 8's seedBill).
	for i, d := range []time.Time{
		time.Date(2026, 4, 14, 0, 0, 0, 0, time.UTC),
		time.Date(2026, 5, 14, 0, 0, 0, 0, time.UTC),
		time.Date(2026, 6, 14, 0, 0, 0, 0, time.UTC),
	} {
		seedForecastTxn(t, s, "acct-fc-api", "scott", "vz-api-"+itoa(i), d, "-120.00", "VERIZON WIRELESS")
	}

	req := httptest.NewRequest(http.MethodGet, "/api/forecast?view=household&month=2026-07", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Forecast struct {
			Month             string `json:"month"`
			Bills             []any  `json:"bills"`
			RemainingExpected string `json:"remaining_expected"`
		} `json:"forecast"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Forecast.Month != "2026-07" || len(resp.Forecast.Bills) != 1 {
		t.Fatalf("bad envelope: %s", w.Body.String())
	}
}

func TestGetForecastRequiresMonth(t *testing.T) {
	s := testStore(t)
	h := New(s).Handler()

	req := httptest.NewRequest(http.MethodGet, "/api/forecast?view=household", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status %d, want 400 for missing month", w.Code)
	}
}

func TestGetForecastEmptyIsValid(t *testing.T) {
	s := testStore(t)
	h := New(s).Handler()

	req := httptest.NewRequest(http.MethodGet, "/api/forecast?view=household&month=2026-07", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d", w.Code)
	}
	var resp struct {
		Forecast struct {
			Bills             []any  `json:"bills"`
			RemainingExpected string `json:"remaining_expected"`
		} `json:"forecast"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Forecast.Bills == nil {
		t.Fatal(`bills must be [], not null`)
	}
	if resp.Forecast.RemainingExpected != "0" {
		t.Fatalf("remaining %q, want 0", resp.Forecast.RemainingExpected)
	}
}
```

`seedForecastTxn` is an api-package helper to write in this file: copy the body of the api package's existing `seedTxn` helper, change the `Payee` field to the explicit parameter (exactly the Task 8 `seedBill` treatment), keep the return type. If `itoa` wasn't added in Task 5, add it here.

- [ ] **Step 2: Run tests to verify they fail**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/api/ -run TestGetForecast -v
```

Expected: FAIL — 404 from the mux.

- [ ] **Step 3: Implement `internal/api/forecast.go`**

```go
package api

import (
	"log"
	"net/http"

	"vollmint/internal/report"
)

func (s *Server) registerForecast() {
	s.mux.HandleFunc("GET /api/forecast", s.handleGetForecast)
}

func (s *Server) handleGetForecast(w http.ResponseWriter, r *http.Request) {
	view, month, ok := requireViewMonth(w, r)
	if !ok {
		return
	}
	f, err := report.Forecast(r.Context(), s.store, view, month)
	if err != nil {
		log.Printf("forecast: %v", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"forecast": f})
}
```

(Fix the module path on the `report` import to match `go.mod`; copy from `internal/api/summary.go`.)

In `server.go` `routes()`, after `s.registerSplits()`:

```go
	s.registerForecast()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/api/ -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/api/forecast.go internal/api/forecast_test.go internal/api/server.go
git commit -m "feat(api): GET /api/forecast

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 10: Insight generators — `internal/report/insights.go`

**Files:**
- Create: `internal/report/insights.go`
- Create: `internal/report/insights_test.go`

- [ ] **Step 1: Write the failing tests**

Create `internal/report/insights_test.go` (uses `seedSpend`, `setBudget` from report test helpers and `seedBill` from Task 8's `forecast_test.go` — same package, so it's visible):

```go
package report

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestInsightCategorySpike(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()

	// 3-month Dining average = $100; July = $200 → 2.0x and +$100 → fires.
	seedSpend(t, s, "acct-ins", "scott", "d-apr", time.Date(2026, 4, 10, 0, 0, 0, 0, time.UTC), "-100.00", "Dining")
	seedSpend(t, s, "acct-ins", "scott", "d-may", time.Date(2026, 5, 10, 0, 0, 0, 0, time.UTC), "-100.00", "Dining")
	seedSpend(t, s, "acct-ins", "scott", "d-jun", time.Date(2026, 6, 10, 0, 0, 0, 0, time.UTC), "-100.00", "Dining")
	seedSpend(t, s, "acct-ins", "scott", "d-jul", time.Date(2026, 7, 10, 0, 0, 0, 0, time.UTC), "-200.00", "Dining")

	// Groceries: flat — must NOT fire.
	seedSpend(t, s, "acct-ins", "scott", "g-apr", time.Date(2026, 4, 11, 0, 0, 0, 0, time.UTC), "-80.00", "Groceries")
	seedSpend(t, s, "acct-ins", "scott", "g-may", time.Date(2026, 5, 11, 0, 0, 0, 0, time.UTC), "-80.00", "Groceries")
	seedSpend(t, s, "acct-ins", "scott", "g-jun", time.Date(2026, 6, 11, 0, 0, 0, 0, time.UTC), "-80.00", "Groceries")
	seedSpend(t, s, "acct-ins", "scott", "g-jul", time.Date(2026, 7, 11, 0, 0, 0, 0, time.UTC), "-82.00", "Groceries")

	now := time.Date(2026, 8, 15, 0, 0, 0, 0, time.UTC) // viewing a past month
	items, err := InsightCategorySpikes(ctx, s, "household", "2026-07", now)
	if err != nil {
		t.Fatalf("spikes: %v", err)
	}
	if len(items) != 1 {
		t.Fatalf("want 1 insight, got %+v", items)
	}
	in := items[0]
	if in.Type != "category_spike" || in.Title != "Dining is running hot" {
		t.Fatalf("wrong card: %+v", in)
	}
	if !strings.Contains(in.Body, "$200.00") || !strings.Contains(in.Body, "$100.00") {
		t.Fatalf("body missing amounts: %q", in.Body)
	}
	if in.Amount != "100.00" {
		t.Fatalf("amount %q, want delta 100.00", in.Amount)
	}
}

func TestInsightBudgetBreachBeatsSpike(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()

	seedSpend(t, s, "acct-ins", "scott", "b-apr", time.Date(2026, 4, 10, 0, 0, 0, 0, time.UTC), "-100.00", "Dining")
	seedSpend(t, s, "acct-ins", "scott", "b-may", time.Date(2026, 5, 10, 0, 0, 0, 0, time.UTC), "-100.00", "Dining")
	seedSpend(t, s, "acct-ins", "scott", "b-jun", time.Date(2026, 6, 10, 0, 0, 0, 0, time.UTC), "-100.00", "Dining")
	seedSpend(t, s, "acct-ins", "scott", "b-jul", time.Date(2026, 7, 10, 0, 0, 0, 0, time.UTC), "-200.00", "Dining")
	setBudget(t, s, "Dining", "2026-07", "150.00")

	// Current month: body mentions days left.
	now := time.Date(2026, 7, 20, 0, 0, 0, 0, time.UTC)
	items, err := InsightCategorySpikes(ctx, s, "household", "2026-07", now)
	if err != nil {
		t.Fatalf("spikes: %v", err)
	}
	if len(items) != 1 {
		t.Fatalf("breach must replace spike (one card per category): %+v", items)
	}
	in := items[0]
	if in.Type != "budget_breach" || in.Title != "Dining is over budget" {
		t.Fatalf("wrong card: %+v", in)
	}
	if !strings.Contains(in.Body, "$50.00 over") || !strings.Contains(in.Body, "$150.00 budget") {
		t.Fatalf("body: %q", in.Body)
	}
	if !strings.Contains(in.Body, "11 days left") { // July has 31 days; 31-20=11
		t.Fatalf("current-month body must count days left: %q", in.Body)
	}
	if in.Amount != "50.00" {
		t.Fatalf("amount %q, want overage 50.00", in.Amount)
	}

	// Past month: no days-left clause.
	past := time.Date(2026, 8, 15, 0, 0, 0, 0, time.UTC)
	items, err = InsightCategorySpikes(ctx, s, "household", "2026-07", past)
	if err != nil {
		t.Fatalf("spikes past: %v", err)
	}
	if strings.Contains(items[0].Body, "days left") {
		t.Fatalf("past month must not mention days left: %q", items[0].Body)
	}
}

func TestInsightSubscriptions(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()

	// Netflix: price increase 15.49 → 17.99 (+16%, +$2.50). Streaming.
	seedBill(t, s, "acct-sub", "scott", "nx-apr", time.Date(2026, 4, 20, 0, 0, 0, 0, time.UTC), "-15.49", "NETFLIX", "Subscriptions")
	seedBill(t, s, "acct-sub", "scott", "nx-may", time.Date(2026, 5, 20, 0, 0, 0, 0, time.UTC), "-15.49", "NETFLIX", "Subscriptions")
	seedBill(t, s, "acct-sub", "scott", "nx-jun", time.Date(2026, 6, 20, 0, 0, 0, 0, time.UTC), "-17.99", "NETFLIX", "Subscriptions")

	// Hulu: stable. Streaming → overlap with Netflix.
	seedBill(t, s, "acct-sub", "scott", "hl-apr", time.Date(2026, 4, 22, 0, 0, 0, 0, time.UTC), "-15.49", "HULU", "Subscriptions")
	seedBill(t, s, "acct-sub", "scott", "hl-may", time.Date(2026, 5, 22, 0, 0, 0, 0, time.UTC), "-15.49", "HULU", "Subscriptions")
	seedBill(t, s, "acct-sub", "scott", "hl-jun", time.Date(2026, 6, 22, 0, 0, 0, 0, time.UTC), "-15.49", "HULU", "Subscriptions")

	items, err := InsightSubscriptions(ctx, s, "household", "2026-07")
	if err != nil {
		t.Fatalf("subs: %v", err)
	}
	byType := map[string]Insight{}
	for _, in := range items {
		byType[in.Type] = in
	}

	total, ok := byType["subscription_total"]
	if !ok {
		t.Fatalf("missing subscription_total: %+v", items)
	}
	if !strings.Contains(total.Body, "$33.48/month") || !strings.Contains(total.Body, "2 recurring") {
		t.Fatalf("total body: %q", total.Body)
	}

	inc, ok := byType["price_increase"]
	if !ok {
		t.Fatalf("missing price_increase: %+v", items)
	}
	if inc.Title != "Netflix price went up" ||
		!strings.Contains(inc.Body, "$15.49") || !strings.Contains(inc.Body, "$17.99") ||
		!strings.Contains(inc.Body, "+$2.50") {
		t.Fatalf("increase card: %+v", inc)
	}
	if inc.Amount != "2.50" {
		t.Fatalf("increase amount %q", inc.Amount)
	}

	ovl, ok := byType["subscription_overlap"]
	if !ok {
		t.Fatalf("missing subscription_overlap: %+v", items)
	}
	if ovl.Title != "Overlapping streaming subscriptions" ||
		!strings.Contains(ovl.Body, "2 streaming services") ||
		!strings.Contains(ovl.Body, "Netflix") || !strings.Contains(ovl.Body, "Hulu") {
		t.Fatalf("overlap card: %+v", ovl)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/report/ -run TestInsight -v
```

Expected: COMPILE FAILURE — `undefined: InsightCategorySpikes` etc.

- [ ] **Step 3: Implement `internal/report/insights.go`**

```go
package report

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	"vollmint/internal/store"
)

type Insight struct {
	Type   string `json:"type"`
	Title  string `json:"title"`
	Body   string `json:"body"`
	Amount string `json:"amount"`
}

// InsightCategorySpikes emits one card per category: budget breach when a
// budget is exceeded, else a spike card when spent >= 1.25x the 3-month
// average and at least $50 over it. Cap 5, sorted by delta descending.
func InsightCategorySpikes(ctx context.Context, s *store.Store, view, month string, now time.Time) ([]Insight, error) {
	own, args := ownerFilter(view, 2)
	full := append([]any{month + "-01"}, args...)

	rows, err := s.Pool.Query(ctx, `
WITH eff AS (
  SELECT COALESCE(sp.category_id, t.category_id) AS cat_id,
         COALESCE(sp.amount, t.amount) AS amt,
         date_trunc('month', t.posted)::date AS m
  FROM transactions t
  JOIN accounts a ON a.id = t.account_id
  LEFT JOIN transaction_splits sp ON sp.transaction_id = t.id
  WHERE t.amount < 0 AND t.transfer_peer_id IS NULL
    AND t.posted >= ($1::date - interval '3 months')
    AND t.posted < ($1::date + interval '1 month')`+own+`
),
cur AS (SELECT cat_id, -SUM(amt) AS spent FROM eff WHERE m = $1::date GROUP BY cat_id),
prev AS (SELECT cat_id, -SUM(amt)/3 AS avg3 FROM eff WHERE m < $1::date GROUP BY cat_id)
SELECT c.id, c.name, cur.spent::text, round(COALESCE(prev.avg3, 0), 2)::text,
       COALESCE(b.amount::text, '')
FROM cur
JOIN categories c ON c.id = cur.cat_id
LEFT JOIN prev ON prev.cat_id = cur.cat_id
LEFT JOIN budgets b ON b.category_id = c.id AND b.month = $1::date
WHERE c.kind <> 'transfer'
ORDER BY c.name`, full...)
	if err != nil {
		return nil, fmt.Errorf("category spikes: %w", err)
	}
	defer rows.Close()

	type carded struct {
		in    Insight
		delta int64
	}
	var cards []carded
	current := month == now.Format("2006-01")
	mt, _ := time.Parse("2006-01", month)
	daysIn := time.Date(mt.Year(), mt.Month()+1, 0, 0, 0, 0, 0, time.UTC).Day()

	for rows.Next() {
		var id int
		var name, spent, avg3, budget string
		if err := rows.Scan(&id, &name, &spent, &avg3, &budget); err != nil {
			return nil, fmt.Errorf("spike scan: %w", err)
		}
		spentC, avgC := cents(spent), cents(avg3)

		if budget != "" {
			budgetC := cents(budget)
			if spentC > budgetC {
				over := spentC - budgetC
				body := fmt.Sprintf("%s is %s over its %s budget.", name, usd(over), usd(budgetC))
				if current {
					left := daysIn - now.Day()
					body = fmt.Sprintf("%s is %s over its %s budget with %d days left in the month.",
						name, usd(over), usd(budgetC), left)
				}
				cards = append(cards, carded{Insight{
					Type: "budget_breach", Title: name + " is over budget",
					Body: body, Amount: centsToDec(over)}, over})
				continue
			}
		}
		if 4*spentC >= 5*avgC && spentC-avgC >= 5000 {
			delta := spentC - avgC
			cards = append(cards, carded{Insight{
				Type:  "category_spike",
				Title: name + " is running hot",
				Body: fmt.Sprintf("You've spent %s on %s this month — %s above your 3-month average of %s.",
					usd(spentC), name, usd(delta), usd(avgC)),
				Amount: centsToDec(delta)}, delta})
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("spike rows: %w", err)
	}

	sort.SliceStable(cards, func(i, j int) bool { return cards[i].delta > cards[j].delta })
	if len(cards) > 5 {
		cards = cards[:5]
	}
	out := make([]Insight, len(cards))
	for i, c := range cards {
		out[i] = c.in
	}
	return out, nil
}

var overlapGroups = []struct {
	display string
	keys    []string
}{
	{"streaming", []string{"netflix", "hulu", "disney", "max", "paramount", "peacock", "youtube premium", "apple tv"}},
	{"music", []string{"spotify", "apple music", "tidal", "pandora"}},
	{"cloud storage", []string{"dropbox", "google one", "icloud", "onedrive"}},
	{"AI", []string{"anthropic", "claude.ai", "openai", "chatgpt"}},
}

// InsightSubscriptions audits recurring charges: total burn, price
// increases, and overlapping same-purpose services.
func InsightSubscriptions(ctx context.Context, s *store.Store, view, month string) ([]Insight, error) {
	own, args := ownerFilter(view, 2)
	full := append([]any{month + "-01"}, args...)

	rows, err := s.Pool.Query(ctx, `
WITH spend AS (
  SELECT t.payee, -t.amount AS mag, t.posted, t.pending,
         date_trunc('month', t.posted)::date AS m, t.category_id
  FROM transactions t
  JOIN accounts a ON a.id = t.account_id
  LEFT JOIN categories c ON c.id = t.category_id
  WHERE t.amount < 0 AND t.payee <> ''
    AND t.transfer_peer_id IS NULL
    AND (c.kind IS NULL OR c.kind <> 'transfer')
    AND t.payee NOT ILIKE '%venmo%' AND t.payee NOT ILIKE '%zelle%'`+own+`
),
hist AS (
  SELECT * FROM spend
  WHERE NOT pending AND posted < ($1::date + interval '1 month')
),
cadence AS (
  SELECT payee FROM hist GROUP BY payee
  HAVING count(DISTINCT m) >= 3
     AND count(DISTINCT m) FILTER (
           WHERE m >= ($1::date - interval '3 months') AND m < $1::date) >= 2
),
ranked AS (
  SELECT payee, mag, posted,
         row_number() OVER (PARTITION BY payee ORDER BY posted DESC) AS rn
  FROM hist
),
stats AS (
  SELECT payee, percentile_cont(0.5) WITHIN GROUP (ORDER BY mag) AS med
  FROM hist GROUP BY payee
),
catmode AS (
  SELECT payee, mode() WITHIN GROUP (ORDER BY category_id) AS category_id
  FROM hist WHERE category_id IS NOT NULL GROUP BY payee
)
SELECT cad.payee, COALESCE(c.name, ''), l.mag::text,
       COALESCE(p.mag::text, ''), round(st.med::numeric, 2)::text
FROM cadence cad
JOIN ranked l ON l.payee = cad.payee AND l.rn = 1
LEFT JOIN ranked p ON p.payee = cad.payee AND p.rn = 2
JOIN stats st ON st.payee = cad.payee
LEFT JOIN catmode cm ON cm.payee = cad.payee
LEFT JOIN categories c ON c.id = cm.category_id
ORDER BY cad.payee`, full...)
	if err != nil {
		return nil, fmt.Errorf("subscription audit: %w", err)
	}
	defer rows.Close()

	type sub struct {
		payee, category      string
		latC, prevC, medC    int64
		hasPrev, subLike     bool
	}
	var all []sub
	for rows.Next() {
		var payee, category, latest, prev, med string
		if err := rows.Scan(&payee, &category, &latest, &prev, &med); err != nil {
			return nil, fmt.Errorf("subscription scan: %w", err)
		}
		s := sub{payee: payee, category: category,
			latC: cents(latest), medC: cents(med), hasPrev: prev != ""}
		if s.hasPrev {
			s.prevC = cents(prev)
		}
		diff := s.latC - s.medC
		if diff < 0 {
			diff = -diff
		}
		s.subLike = category == "Subscriptions" || diff*10 <= s.medC
		all = append(all, s)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("subscription rows: %w", err)
	}

	var out []Insight

	// Total burn over subscription-like payees.
	var subs []sub
	var totalC int64
	for _, s := range all {
		if s.subLike {
			subs = append(subs, s)
			totalC += s.latC
		}
	}
	if len(subs) > 0 {
		top := make([]sub, len(subs))
		copy(top, subs)
		sort.SliceStable(top, func(i, j int) bool { return top[i].latC > top[j].latC })
		if len(top) > 3 {
			top = top[:3]
		}
		var names []string
		for _, s := range top {
			names = append(names, fmt.Sprintf("%s (%s)", titleCase(s.payee), usd(s.latC)))
		}
		noun := "recurring charges"
		if len(subs) == 1 {
			noun = "recurring charge"
		}
		out = append(out, Insight{
			Type:  "subscription_total",
			Title: "Recurring charges add up",
			Body: fmt.Sprintf("You're carrying %s/month across %d %s. Largest: %s.",
				usd(totalC), len(subs), noun, strings.Join(names, ", ")),
			Amount: centsToDec(totalC),
		})
	}

	// Price increases — any cadence payee, not just sub-like.
	for _, s := range all {
		if !s.hasPrev || s.latC <= s.prevC {
			continue
		}
		diff := s.latC - s.prevC
		if diff*20 > s.prevC && diff >= 100 {
			out = append(out, Insight{
				Type:  "price_increase",
				Title: titleCase(s.payee) + " price went up",
				Body: fmt.Sprintf("%s went from %s to %s (+%s).",
					titleCase(s.payee), usd(s.prevC), usd(s.latC), usd(diff)),
				Amount: centsToDec(diff),
			})
		}
	}

	// Overlaps within the subscription-like set.
	for _, g := range overlapGroups {
		var matched []sub
		for _, s := range subs {
			low := strings.ToLower(s.payee)
			for _, k := range g.keys {
				if strings.Contains(low, k) {
					matched = append(matched, s)
					break
				}
			}
		}
		if len(matched) >= 2 {
			var names []string
			var sumC int64
			for _, m := range matched {
				names = append(names, titleCase(m.payee))
				sumC += m.latC
			}
			out = append(out, Insight{
				Type:  "subscription_overlap",
				Title: "Overlapping " + g.display + " subscriptions",
				Body: fmt.Sprintf("You're paying for %d %s services (%s) — %s/month combined.",
					len(matched), g.display, strings.Join(names, ", "), usd(sumC)),
				Amount: centsToDec(sumC),
			})
		}
	}

	return out, nil
}

// Insights combines all generators, sorted by money at stake descending.
func Insights(ctx context.Context, s *store.Store, view, month string, now time.Time) ([]Insight, error) {
	spikes, err := InsightCategorySpikes(ctx, s, view, month, now)
	if err != nil {
		return nil, err
	}
	subs, err := InsightSubscriptions(ctx, s, view, month)
	if err != nil {
		return nil, err
	}
	all := append(spikes, subs...)
	sort.SliceStable(all, func(i, j int) bool {
		return cents(all[i].Amount) > cents(all[j].Amount)
	})
	return all, nil
}
```

(Fix the `store` import path from `go.mod`. The local variable `s` inside the scan loop shadows the `*store.Store` parameter — rename the loop variable to `sb` if `go vet` complains or it reads badly; the plan keeps the field-struct name `sub` distinct so only the inner variable needs care.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/report/ -v
```

Expected: PASS — all insight tests plus everything prior.

- [ ] **Step 5: Commit**

```bash
git add internal/report/insights.go internal/report/insights_test.go
git commit -m "feat(report): category-spike and subscription-audit insight generators

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 11: `GET /api/insights`

**Files:**
- Create: `internal/api/insights.go`
- Create: `internal/api/insights_test.go`
- Modify: `internal/api/server.go` (routes)

- [ ] **Step 1: Write the failing tests**

Create `internal/api/insights_test.go`:

```go
package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestGetInsightsRequiresMonth(t *testing.T) {
	s := testStore(t)
	h := New(s).Handler()

	req := httptest.NewRequest(http.MethodGet, "/api/insights?view=household", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status %d, want 400 for missing month", w.Code)
	}
}

func TestGetInsightsEmptyIsValid(t *testing.T) {
	s := testStore(t)
	h := New(s).Handler()

	req := httptest.NewRequest(http.MethodGet, "/api/insights?view=household&month=2026-07", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Insights []any `json:"insights"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.Insights == nil {
		t.Fatal(`insights must be [], not null`)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/api/ -run TestGetInsights -v
```

Expected: FAIL — 404 from the mux.

- [ ] **Step 3: Implement `internal/api/insights.go`**

```go
package api

import (
	"log"
	"net/http"
	"time"

	"vollmint/internal/report"
)

func (s *Server) registerInsights() {
	s.mux.HandleFunc("GET /api/insights", s.handleGetInsights)
}

func (s *Server) handleGetInsights(w http.ResponseWriter, r *http.Request) {
	view, month, ok := requireViewMonth(w, r)
	if !ok {
		return
	}
	items, err := report.Insights(r.Context(), s.store, view, month, time.Now())
	if err != nil {
		log.Printf("insights: %v", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	if items == nil {
		items = []report.Insight{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"insights": items})
}
```

(Fix the module path on the `report` import to match `go.mod`.)

In `server.go` `routes()`, after `s.registerForecast()`:

```go
	s.registerInsights()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/api/ -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/api/insights.go internal/api/insights_test.go internal/api/server.go
git commit -m "feat(api): GET /api/insights

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 12: Web API client — splits, forecast, insights + format helpers

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/format.ts`
- Modify: `web/src/api.test.ts` (new cases + `splits: []` on existing Txn fixtures)
- Modify: `web/src/format.test.ts`
- Modify: `web/src/components/Transactions.test.tsx` (`splits: []` on existing Txn fixtures)

- [ ] **Step 1: Write the failing tests**

Add to `web/src/api.test.ts` (follow the file's existing fetch-mock pattern — a mocked global `fetch` returning `{ ok: true, json: ... }`, then assert on the call URL/init):

```ts
it('putSplits PUTs the splits array and unwraps the envelope', async () => {
  const txn = { id: 7 } // shape irrelevant to the client
  mockFetch({ transaction: txn })
  const res = await putSplits(7, [
    { category_id: 2, amount: '-30.00', note: '' },
    { category_id: 3, amount: '-20.00', note: 'tickets' },
  ])
  const [url, init] = lastFetchCall()
  expect(url).toBe('/api/transactions/7/splits')
  expect(init.method).toBe('PUT')
  expect(JSON.parse(init.body as string)).toEqual({
    splits: [
      { category_id: 2, amount: '-30.00', note: '' },
      { category_id: 3, amount: '-20.00', note: 'tickets' },
    ],
  })
  expect(res.transaction).toEqual(txn)
})

it('deleteSplits DELETEs the splits resource', async () => {
  mockFetch({ status: 'ok' })
  await deleteSplits(7)
  const [url, init] = lastFetchCall()
  expect(url).toBe('/api/transactions/7/splits')
  expect(init.method).toBe('DELETE')
})

it('getForecast requests view and month', async () => {
  mockFetch({ forecast: { month: '2026-07', view: 'household', bills: [], remaining_expected: '0' } })
  const res = await getForecast('household', '2026-07')
  const [url] = lastFetchCall()
  expect(url).toBe('/api/forecast?view=household&month=2026-07')
  expect(res.forecast.bills).toEqual([])
})

it('getInsights requests view and month', async () => {
  mockFetch({ insights: [] })
  const res = await getInsights('scott', '2026-06')
  const [url] = lastFetchCall()
  expect(url).toBe('/api/insights?view=scott&month=2026-06')
  expect(res.insights).toEqual([])
})
```

If the file doesn't already have `mockFetch`/`lastFetchCall` helpers under those names, use whatever inline mock idiom its existing tests use — copy an existing test (e.g. the `deleteRule` one) and adapt. Do not invent a new mocking style.

Add to `web/src/format.test.ts`:

```ts
describe('titleCase', () => {
  it('title-cases an uppercase payee', () => {
    expect(titleCase('VERIZON WIRELESS')).toBe('Verizon Wireless')
  })
  it('handles single words', () => {
    expect(titleCase('NETFLIX')).toBe('Netflix')
  })
})

describe('monthDayLabel', () => {
  it('formats a month + day', () => {
    expect(monthDayLabel('2026-07', 14)).toBe('Jul 14')
  })
})

describe('dateLabel', () => {
  it('formats an ISO date', () => {
    expect(dateLabel('2026-07-13')).toBe('Jul 13')
  })
})
```

**Also update every existing `Txn` fixture** in `web/src/api.test.ts` and `web/src/components/Transactions.test.tsx` to include `splits: []` — the `Txn` type gains a required field in Step 3 and TypeScript will fail the build otherwise.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd web && npx vitest run
```

Expected: FAIL — `putSplits`/`getForecast`/`titleCase` etc. are not exported.

- [ ] **Step 3: Implement**

In `web/src/api.ts`, add types (near the existing `Txn` type):

```ts
export interface Split {
  id: number
  category_id: number
  category: string
  amount: string
  note: string
}

export interface SplitInput {
  category_id: number
  amount: string
  note: string
}

export interface ForecastBill {
  payee: string
  category_id: number | null
  category: string
  predicted_day: number
  expected_amount: string
  paid: boolean
  paid_date: string
  paid_amount: string
}

export interface Forecast {
  month: string
  view: string
  bills: ForecastBill[]
  remaining_expected: string
}

export interface Insight {
  type: string
  title: string
  body: string
  amount: string
}
```

Add to the existing `Txn` interface:

```ts
  splits: Split[]
```

Add functions (mirror the style of the existing `patchTransaction`/`deleteRule` — same `req` helper, same `jsonInit` helper if that's what the file uses; match the actual helper names in the file):

```ts
export function putSplits(id: number, splits: SplitInput[]): Promise<{ transaction: Txn }> {
  return req(`/api/transactions/${id}/splits`, jsonInit('PUT', { splits }))
}

export function deleteSplits(id: number): Promise<{ status: string }> {
  return req(`/api/transactions/${id}/splits`, { method: 'DELETE' })
}

export function getForecast(view: View, month: string): Promise<{ forecast: Forecast }> {
  return req(`/api/forecast?view=${view}&month=${month}`)
}

export function getInsights(view: View, month: string): Promise<{ insights: Insight[] }> {
  return req(`/api/insights?view=${view}&month=${month}`)
}
```

In `web/src/format.ts`, add:

```ts
const SHORT_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function titleCase(s: string): string {
  return s
    .toLowerCase()
    .split(/\s+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ')
}

/** "2026-07" + 14 → "Jul 14" (caller prefixes "~" for predictions) */
export function monthDayLabel(month: string, day: number): string {
  const m = Number(month.slice(5, 7))
  return `${SHORT_MONTHS[m - 1]} ${day}`
}

/** "2026-07-13" → "Jul 13" */
export function dateLabel(iso: string): string {
  const m = Number(iso.slice(5, 7))
  return `${SHORT_MONTHS[m - 1]} ${Number(iso.slice(8, 10))}`
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd web && npx vitest run
```

Expected: PASS — all suites, including the pre-existing ones with updated fixtures.

- [ ] **Step 5: Commit**

```bash
git add web/src/api.ts web/src/format.ts web/src/api.test.ts web/src/format.test.ts web/src/components/Transactions.test.tsx
git commit -m "feat(web): api client for splits/forecast/insights + date format helpers

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 13: SplitEditor component + Transactions integration

**Files:**
- Create: `web/src/components/SplitEditor.tsx`
- Create: `web/src/components/SplitEditor.test.tsx`
- Modify: `web/src/components/Transactions.tsx`
- Modify: `web/src/components/Transactions.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/SplitEditor.test.tsx` (follow the render/mocking idiom of the existing `Transactions.test.tsx` — Testing Library + `vi.mock('../api', ...)`):

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SplitEditor, toCents, fromCents } from './SplitEditor'
import type { Txn, Category } from '../api'
import * as api from '../api'

vi.mock('../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api')>()
  return { ...mod, putSplits: vi.fn() }
})

const cats: Category[] = [
  { id: 1, name: 'Dining', kind: 'spend', is_vice: true },
  { id: 2, name: 'Groceries', kind: 'spend', is_vice: false },
]

const txn: Txn = {
  id: 7,
  source: 'simplefin',
  account_id: 1,
  account_name: 'Checking',
  posted: '2026-07-10',
  amount: '-50.00',
  description: 'VENMO PAYMENT',
  payee: 'VENMO',
  pending: false,
  category_id: 1,
  category: 'Dining',
  owner_override: null,
  effective_owner: 'scott',
  transfer_peer_id: null,
  splits: [],
}
```

**Fixture caveat:** the `Txn` and `Category` literals above must match the real interfaces in `web/src/api.ts` — copy a fixture from `Transactions.test.tsx` and adjust rather than trusting this field list; the plan's field names may drift from the actual type.

```tsx
describe('toCents', () => {
  it('parses dollars and cents', () => {
    expect(toCents('32.50')).toBe(3250)
    expect(toCents('50')).toBe(5000)
    expect(toCents('0.05')).toBe(5)
  })
  it('rejects garbage', () => {
    expect(toCents('')).toBeNull()
    expect(toCents('abc')).toBeNull()
    expect(toCents('1.234')).toBeNull()
    expect(toCents('-5')).toBeNull()
  })
})

describe('fromCents', () => {
  it('renders cents as dollars', () => {
    expect(fromCents(3250)).toBe('32.50')
    expect(fromCents(5)).toBe('0.05')
  })
})

describe('SplitEditor', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the remainder and disables Save until it hits zero', async () => {
    render(<SplitEditor txn={txn} cats={cats} onSaved={() => {}} onCancel={() => {}} />)
    // Seeded: row 1 = parent category + full 50.00, row 2 empty → remainder 0
    // until we change row 1.
    const amounts = screen.getAllByLabelText(/amount for part/)
    fireEvent.change(amounts[0], { target: { value: '30.00' } })
    expect(screen.getByText(/remaining: \$20\.00/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled()

    fireEvent.change(amounts[1], { target: { value: '20.00' } })
    const catSelects = screen.getAllByLabelText(/category for part/)
    fireEvent.change(catSelects[1], { target: { value: '2' } })
    expect(screen.getByRole('button', { name: /save/i })).toBeEnabled()
  })

  it('applies the parent sign on save', async () => {
    const onSaved = vi.fn()
    vi.mocked(api.putSplits).mockResolvedValue({ transaction: txn })
    render(<SplitEditor txn={txn} cats={cats} onSaved={onSaved} onCancel={() => {}} />)

    const amounts = screen.getAllByLabelText(/amount for part/)
    fireEvent.change(amounts[0], { target: { value: '30.00' } })
    fireEvent.change(amounts[1], { target: { value: '20.00' } })
    const catSelects = screen.getAllByLabelText(/category for part/)
    fireEvent.change(catSelects[1], { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(api.putSplits).toHaveBeenCalledWith(7, [
      { category_id: 1, amount: '-30.00', note: '' },
      { category_id: 2, amount: '-20.00', note: '' },
    ])
  })
})
```

Add to `web/src/components/Transactions.test.tsx` (reusing its existing mock setup; remember every Txn fixture now needs `splits: []`):

```tsx
it('shows a split badge with part names instead of the category select', async () => {
  // One split txn: -50 into Dining -30 / Groceries -20
  const split: Txn = {
    ...baseTxn, // adapt to the file's fixture idiom
    id: 9,
    amount: '-50.00',
    splits: [
      { id: 1, category_id: 1, category: 'Dining', amount: '-30.00', note: '' },
      { id: 2, category_id: 2, category: 'Groceries', amount: '-20.00', note: '' },
    ],
  }
  // mock getTransactions to return [split], render, then:
  expect(await screen.findByText('Split · Dining + Groceries')).toBeInTheDocument()
  expect(screen.queryByLabelText(`category for ${split.payee || split.description}`)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /unsplit/i })).toBeInTheDocument()
})

it('hides the Split action for pending and transfer rows', async () => {
  // mock getTransactions to return [ {...baseTxn, id: 10, pending: true, splits: []},
  //                                  {...baseTxn, id: 11, transfer_peer_id: 99, splits: []} ]
  // render, wait for rows, then:
  expect(screen.queryByRole('button', { name: /^split$/i })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd web && npx vitest run
```

Expected: FAIL — `./SplitEditor` module not found; new Transactions cases fail.

- [ ] **Step 3: Implement**

Create `web/src/components/SplitEditor.tsx`:

```tsx
import { useState } from 'react'
import type { Txn, Category, SplitInput } from '../api'
import { putSplits } from '../api'

const amountRe = /^\d+(\.\d{1,2})?$/

/** "32.5" → 3250; null when not a valid positive dollar amount. */
export function toCents(v: string): number | null {
  const s = v.trim()
  if (!amountRe.test(s)) return null
  const [whole, frac = ''] = s.split('.')
  return Number(whole) * 100 + Number(frac.padEnd(2, '0') || '0')
}

export function fromCents(c: number): string {
  const sign = c < 0 ? '-' : ''
  const abs = Math.abs(c)
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`
}

interface Draft {
  category_id: number | ''
  amount: string
  note: string
}

export function SplitEditor({
  txn,
  cats,
  onSaved,
  onCancel,
}: {
  txn: Txn
  cats: Category[]
  onSaved: () => void
  onCancel: () => void
}) {
  const negative = txn.amount.startsWith('-')
  const parentCents = Math.abs(toCents(txn.amount.replace('-', '')) ?? 0)

  const seed: Draft[] =
    txn.splits.length > 0
      ? txn.splits.map((sp) => ({
          category_id: sp.category_id,
          amount: sp.amount.replace('-', ''),
          note: sp.note,
        }))
      : [
          { category_id: txn.category_id ?? '', amount: txn.amount.replace('-', ''), note: '' },
          { category_id: '', amount: '', note: '' },
        ]

  const [drafts, setDrafts] = useState<Draft[]>(seed)
  const [err, setErr] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const partCents = drafts.map((d) => toCents(d.amount))
  const validSum = partCents.reduce<number>((acc, c) => acc + (c ?? 0), 0)
  const remainder = parentCents - validSum
  const allValid = drafts.every((d, i) => d.category_id !== '' && partCents[i] !== null && partCents[i]! > 0)
  const canSave = remainder === 0 && allValid && !saving

  const update = (i: number, patch: Partial<Draft>) =>
    setDrafts(drafts.map((d, j) => (j === i ? { ...d, ...patch } : d)))

  const save = async () => {
    setSaving(true)
    setErr(null)
    const splits: SplitInput[] = drafts.map((d) => ({
      category_id: d.category_id as number,
      amount: (negative ? '-' : '') + fromCents(toCents(d.amount)!),
      note: d.note,
    }))
    try {
      await putSplits(txn.id, splits)
      onSaved()
    } catch (e) {
      setErr((e as Error).message)
      setSaving(false)
    }
  }

  return (
    <div style={{ padding: '0.75rem', background: 'var(--panel, rgba(128,128,128,0.08))' }}>
      {drafts.map((d, i) => (
        <div key={i} style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.4rem', alignItems: 'center' }}>
          <select
            value={d.category_id}
            onChange={(e) => update(i, { category_id: e.target.value ? Number(e.target.value) : '' })}
            aria-label={`category for part ${i + 1}`}
          >
            <option value="">— category —</option>
            {cats.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <input
            value={d.amount}
            onChange={(e) => update(i, { amount: e.target.value })}
            placeholder="0.00"
            aria-label={`amount for part ${i + 1}`}
            style={{ width: '6rem', textAlign: 'right' }}
          />
          <input
            value={d.note}
            onChange={(e) => update(i, { note: e.target.value })}
            placeholder="note (optional)"
            aria-label={`note for part ${i + 1}`}
          />
          {drafts.length > 2 && (
            <button type="button" onClick={() => setDrafts(drafts.filter((_, j) => j !== i))}>
              ✕
            </button>
          )}
        </div>
      ))}
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
        <button type="button" onClick={() => setDrafts([...drafts, { category_id: '', amount: '', note: '' }])}>
          + Add part
        </button>
        <span style={{ color: remainder === 0 ? 'var(--good)' : 'var(--danger)' }}>
          Remaining: {fromCents(remainder).startsWith('-') ? '-$' + fromCents(remainder).slice(1) : '$' + fromCents(remainder)}
        </span>
        <button type="button" onClick={save} disabled={!canSave}>
          Save
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {err && <p style={{ color: 'var(--danger)' }}>Error: {err}</p>}
    </div>
  )
}
```

Modify `web/src/components/Transactions.tsx`. The changes, against the file as it exists today (5-column table; verify against the live file before editing):

1. Imports:

```tsx
import { Fragment, useEffect, useState } from 'react'
import { getTransactions, getCategories, patchTransaction, deleteSplits } from '../api'
import { SplitEditor } from './SplitEditor'
```

2. New state next to the existing ones:

```tsx
const [editing, setEditing] = useState<number | null>(null)
```

3. Header row: add a sixth `<th></th>` after Category (actions column).

4. Replace the `rows.map((t) => ...)` body. Each row becomes a `Fragment` pair — the data row plus, when `editing === t.id`, an editor row:

```tsx
{rows.map((t) => {
  const canSplit = !t.pending && t.transfer_peer_id === null
  const isSplit = t.splits.length > 0
  return (
    <Fragment key={t.id}>
      <tr>
        <td>{t.posted}</td>
        <td>{t.payee || t.description}</td>
        <td>{t.account_name}</td>
        <td style={{ textAlign: 'right', color: t.amount.startsWith('-') ? 'var(--text)' : 'var(--good)' }}>
          {money(t.amount)}
        </td>
        <td>
          {isSplit ? (
            <div>
              <span>
                {t.splits.length === 2
                  ? 'Split · ' + t.splits.map((sp) => sp.category).join(' + ')
                  : `Split (${t.splits.length})`}
              </span>
              {t.splits.map((sp) => (
                <div key={sp.id} style={{ color: 'var(--muted)', fontSize: '0.85em' }}>
                  {sp.category}: {money(sp.amount)}
                  {sp.note ? ` — ${sp.note}` : ''}
                </div>
              ))}
            </div>
          ) : (
            <select
              value={t.category_id ?? ''}
              onChange={(e) => recategorize(t.id, Number(e.target.value))}
              aria-label={`category for ${t.payee || t.description}`}
            >
              <option value="" disabled>
                — uncategorized —
              </option>
              {cats.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )}
        </td>
        <td>
          {canSplit && (
            <button type="button" onClick={() => setEditing(editing === t.id ? null : t.id)}>
              Split
            </button>
          )}
          {isSplit && (
            <button
              type="button"
              onClick={async () => {
                try {
                  await deleteSplits(t.id)
                } catch (e) {
                  setErr((e as Error).message)
                } finally {
                  load()
                }
              }}
            >
              Unsplit
            </button>
          )}
        </td>
      </tr>
      {editing === t.id && (
        <tr>
          <td colSpan={6}>
            <SplitEditor
              txn={t}
              cats={cats}
              onSaved={() => {
                setEditing(null)
                load()
              }}
              onCancel={() => setEditing(null)}
            />
          </td>
        </tr>
      )}
    </Fragment>
  )
})}
```

5. Empty-state row: `colSpan={5}` → `colSpan={6}`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd web && npx vitest run
```

Expected: PASS — SplitEditor suite plus updated Transactions suite.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/SplitEditor.tsx web/src/components/SplitEditor.test.tsx web/src/components/Transactions.tsx web/src/components/Transactions.test.tsx
git commit -m "feat(web): inline split editor on the transactions page

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 14: UpcomingBills dashboard panel

**Files:**
- Create: `web/src/components/UpcomingBills.tsx`
- Create: `web/src/components/UpcomingBills.test.tsx`
- Modify: `web/src/components/Dashboard.tsx`

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/UpcomingBills.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { UpcomingBills } from './UpcomingBills'
import * as api from '../api'
import type { Forecast } from '../api'

vi.mock('../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api')>()
  return { ...mod, getForecast: vi.fn() }
})

const forecast: Forecast = {
  month: '2026-07',
  view: 'household',
  remaining_expected: '128.42',
  bills: [
    {
      payee: 'VERIZON WIRELESS', category_id: 12, category: 'Utilities',
      predicted_day: 14, expected_amount: '128.42',
      paid: false, paid_date: '', paid_amount: '',
    },
    {
      payee: 'NETFLIX', category_id: 3, category: 'Subscriptions',
      predicted_day: 20, expected_amount: '17.99',
      paid: true, paid_date: '2026-07-20', paid_amount: '17.99',
    },
  ],
}

describe('UpcomingBills', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders unpaid bills with a predicted date and paid bills with a checkmark', async () => {
    vi.mocked(api.getForecast).mockResolvedValue({ forecast })
    render(<UpcomingBills view="household" month="2026-07" />)
    expect(await screen.findByText(/Upcoming bills/)).toBeInTheDocument()
    expect(screen.getByText(/\$128\.42 remaining expected/)).toBeInTheDocument()
    expect(screen.getByText('Verizon Wireless')).toBeInTheDocument()
    expect(screen.getByText('~Jul 14')).toBeInTheDocument()
    expect(screen.getByText('Netflix')).toBeInTheDocument()
    expect(screen.getByText(/Jul 20 ✓/)).toBeInTheDocument()
  })

  it('shows an empty state when no bills are detected', async () => {
    vi.mocked(api.getForecast).mockResolvedValue({
      forecast: { month: '2026-07', view: 'household', bills: [], remaining_expected: '0' },
    })
    render(<UpcomingBills view="household" month="2026-07" />)
    expect(await screen.findByText('No recurring bills detected yet.')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd web && npx vitest run src/components/UpcomingBills.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `web/src/components/UpcomingBills.tsx`:

```tsx
import { useEffect, useState } from 'react'
import type { View, Forecast } from '../api'
import { getForecast } from '../api'
import { money, titleCase, monthDayLabel, dateLabel } from '../format'

export function UpcomingBills({ view, month }: { view: View; month: string }) {
  const [forecast, setForecast] = useState<Forecast | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    getForecast(view, month)
      .then((d) => live && setForecast(d.forecast))
      .catch((e) => live && setErr(e.message))
    return () => {
      live = false
    }
  }, [view, month])

  if (err) return <p style={{ color: 'var(--danger)' }}>Error: {err}</p>
  if (!forecast) return <p style={{ color: 'var(--muted)' }}>Loading…</p>

  return (
    <section>
      <h2>
        Upcoming bills — {money(forecast.remaining_expected)} remaining expected
      </h2>
      {forecast.bills.length === 0 ? (
        <p style={{ color: 'var(--muted)' }}>No recurring bills detected yet.</p>
      ) : (
        <table>
          <tbody>
            {forecast.bills.map((b) => (
              <tr key={b.payee} style={b.paid ? { opacity: 0.55 } : undefined}>
                <td>{titleCase(b.payee)}</td>
                <td style={{ color: 'var(--muted)' }}>{b.category}</td>
                <td>{b.paid ? `${dateLabel(b.paid_date)} ✓` : `~${monthDayLabel(month, b.predicted_day)}`}</td>
                <td style={{ textAlign: 'right' }}>{money(b.expected_amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
```

(The `money` helper renders decimal strings as `$x.xx` — check its signature in `web/src/format.ts` before use; forecast amounts are positive magnitudes so no sign handling is needed. The server pre-sorts bills — unpaid by day, then paid — so the component renders in order.)

In `web/src/components/Dashboard.tsx`, inside the existing `<div className="grid" style={{ gap: '1.5rem' }}>`, after the "Spending by category" `<section>`, add:

```tsx
<UpcomingBills view={view} month={month} />
```

with the import:

```tsx
import { UpcomingBills } from './UpcomingBills'
```

(Verify the Dashboard's actual prop names for view/month against the live file — mirror how it passes them to its existing children.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd web && npx vitest run
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/UpcomingBills.tsx web/src/components/UpcomingBills.test.tsx web/src/components/Dashboard.tsx
git commit -m "feat(web): upcoming-bills dashboard panel

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

---

### Task 15: InsightCards component on Trends

**Files:**
- Create: `web/src/components/InsightCards.tsx`
- Create: `web/src/components/InsightCards.test.tsx`
- Modify: `web/src/components/Trends.tsx`

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/InsightCards.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { InsightCards } from './InsightCards'
import * as api from '../api'

vi.mock('../api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api')>()
  return { ...mod, getInsights: vi.fn() }
})

describe('InsightCards', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders one card per insight with the type icon', async () => {
    vi.mocked(api.getInsights).mockResolvedValue({
      insights: [
        {
          type: 'category_spike',
          title: 'Dining is running hot',
          body: "You've spent $200.00 on Dining this month — $100.00 above your 3-month average of $100.00.",
          amount: '100.00',
        },
        {
          type: 'price_increase',
          title: 'Netflix price went up',
          body: 'Netflix went from $15.49 to $17.99 (+$2.50).',
          amount: '2.50',
        },
      ],
    })
    render(<InsightCards view="household" month="2026-07" />)
    expect(await screen.findByText('Dining is running hot')).toBeInTheDocument()
    expect(screen.getByText('📈')).toBeInTheDocument()
    expect(screen.getByText('Netflix price went up')).toBeInTheDocument()
    expect(screen.getByText('⬆️')).toBeInTheDocument()
  })

  it('renders nothing when there are no insights', async () => {
    vi.mocked(api.getInsights).mockResolvedValue({ insights: [] })
    const { container } = render(<InsightCards view="household" month="2026-07" />)
    await vi.waitFor(() => expect(api.getInsights).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd web && npx vitest run src/components/InsightCards.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `web/src/components/InsightCards.tsx`:

```tsx
import { useEffect, useState } from 'react'
import type { View, Insight } from '../api'
import { getInsights } from '../api'

const ICONS: Record<string, string> = {
  category_spike: '📈',
  budget_breach: '⚠️',
  subscription_total: '💳',
  price_increase: '⬆️',
  subscription_overlap: '🔁',
}

export function InsightCards({ view, month }: { view: View; month: string }) {
  const [items, setItems] = useState<Insight[]>([])

  useEffect(() => {
    let live = true
    getInsights(view, month)
      .then((d) => live && setItems(d.insights))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [view, month])

  if (items.length === 0) return null

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
        gap: '1rem',
        marginTop: '1.5rem',
      }}
    >
      {items.map((in_, i) => (
        <div
          key={i}
          style={{
            border: '1px solid var(--border, rgba(128,128,128,0.3))',
            borderRadius: '8px',
            padding: '0.9rem',
          }}
        >
          <span style={{ fontSize: '1.4em' }}>{ICONS[in_.type] ?? '💡'}</span>
          <h3 style={{ margin: '0.4rem 0' }}>{in_.title}</h3>
          <p style={{ color: 'var(--muted)', margin: 0 }}>{in_.body}</p>
        </div>
      ))}
    </div>
  )
}
```

In `web/src/components/Trends.tsx`, below the `<BarChart>` block (inside the component's returned JSX, after the chart), add:

```tsx
<InsightCards view={view} month={month} />
```

with the import:

```tsx
import { InsightCards } from './InsightCards'
```

**Caveat:** Trends' props are the months-window driven chart — check what view/month values the component actually has in scope. If Trends receives `view` but derives its own month window and has no single `month` prop, pass the app's current selected month down from the parent the same way Dashboard receives it (add a `month` prop to Trends in `App.tsx`, mirroring Dashboard's wiring). Encode whatever is true of the live file; do not guess.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd web && npx vitest run
```

Expected: PASS — full web suite.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/InsightCards.tsx web/src/components/InsightCards.test.tsx web/src/components/Trends.tsx
git commit -m "feat(web): insight cards on the trends page

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX"
```

(If `App.tsx` needed the month-prop wiring, add it to the `git add` list.)

---

### Task 16: Final verification pass

**Files:** none created — verification only.

- [ ] **Step 1: Full Go suite + vet**

```bash
export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' go test ./...
go vet ./...
```

Expected: all packages `ok`, vet silent.

- [ ] **Step 2: Full web suite + production build**

```bash
cd web && npx vitest run && npm run build
cd .. && git checkout web/dist/index.html
```

Expected: all Vitest suites pass; `npm run build` completes with no TypeScript errors. The `git checkout` restores the tracked `web/dist/index.html` placeholder that the build overwrites — do not commit the built file.

- [ ] **Step 3: Working tree clean**

```bash
git status --short
```

Expected: empty output. If any step above required a fix, commit it with a `fix:` message (explicit `git add` by filename) before finishing.

---

## After the PR merges (not a task — requires Scott)

Do **not** do any of this without Scott's explicit word — Scott merges all PRs.

1. Scott merges the vollmint PR.
2. Tag the release from updated main: `git checkout main && git pull && git tag v0.3.0 && git push origin v0.3.0`.
3. The tag push triggers the Harbor CI image build (retrigger a dind-race failure with `gh pr close N && gh pr reopen N` — not applicable to tag builds; for those re-run the workflow via `gh run rerun`).
4. Open a cluster PR in `k8s-vollminlab-cluster` bumping the vollmint OCIRepository/image tag to `v0.3.0`. The spec (`docs/superpowers/specs/vollmint-forecasting-splits-insights-design.md`) and this plan file ride along in that cluster PR (both currently uncommitted on disk).
5. After Flux reconciles: verify rollout (`kubectl rollout status deploy/vollmint -n vollmint`), check `/api/forecast` and `/api/insights` return 200 through the ingress, and confirm goose applied migration 0003 in the startup logs.

## Out of scope (deliberate, YAGNI)

- **Net-worth / balance snapshots** — phase-6 candidate, needs schema + daily capture job.
- **Insight dismiss/read state** — no persistence; cards recompute per request.
- **Small-charge-bleed and merchant-concentration insights** — cut from generator scope.
- **Per-user authorization** — the app remains single-household behind Authentik forward-auth.
- **Amount tolerance on forecast paid-matching** — payee identity is the match key; amount shown is informational.
- **Forecast/insight caching or schema** — both computed on the fly; revisit only if latency becomes real.
