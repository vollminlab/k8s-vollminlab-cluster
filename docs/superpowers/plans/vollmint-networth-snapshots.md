# vollmint Net Worth (Balance Snapshots) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily account-balance snapshots captured at sync time, manual accounts (401k, home value, mortgage principal), and a Monarch-style "Net Worth" page with per-account drill-down.

**Architecture:** New `account_balance_snapshots` table + `accounts.is_manual` column (migration 0004). The sync upserts one snapshot per account keyed on SimpleFIN's `balance_date` right after `UpsertAccounts`. A `generate_series` + `LATERAL` carry-forward query in `internal/report` powers `GET /api/networth`; two small store methods power manual-account create/edit endpoints. React page follows the Trends idiom (fixed-size recharts, view-prop filtering).

**Tech Stack:** Go 1.26 stdlib mux + pgx/goose, Postgres 16, React 18 + TypeScript + Vite + recharts + vitest.

**Spec:** `docs/superpowers/specs/vollmint-networth-snapshots-design.md` (same repo).

---

## Context for every task (read first)

- **Worktree:** `/home/vollmin/repos/vollminlab/vollmint/.worktrees/feat-networth`, branch `feat/networth-snapshots`. All file paths below are relative to this worktree root. Never touch `main`.
- **Go env:** `export PATH=$PATH:/usr/local/go/bin`
- **Go tests:** `export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable'` then `go test ./... -count=1` (a Postgres 16 dev container is already running on :5433). Run single packages with `go test ./internal/store/ -count=1 -run TestName -v`.
- **SPA tests:** `cd web && npm test` (vitest; `npm install` already done).
- **Money is decimal strings end to end** — never `float64` in Go, never `number` in TS interfaces. SQL casts to `::text` on SELECT. The only allowed `Number()` in TSX is chart geometry / display totals, always commented as such.
- **JSON shape conventions:** errors are `{"error":"msg"}` via `writeErr`; list fields are never `null` (guard `if x == nil { x = []T{} }` before `writeJSON`).
- **Commit style:** explicit `git add <files>` by name (never `-A`/`.`), message trailer on every commit:

```
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QNYieb3KXVmxTbz7pT5HRX
```

- **Seed data quirk:** the test harness keeps a seeded `venmo` account (`owner='scott'`, `balance` NULL, active). It appears in account lists for `scott`/`household` views but never in series/totals (no snapshots — NULL balance). Never assert exact account-list lengths in `scott`/`household` view tests; assert membership instead.

---

### Task 1: Migration 0004 + test-harness reset updates

**Files:**
- Create: `internal/migrate/migrations/0004_balance_snapshots.sql`
- Modify: `internal/store/testdb_test.go` (TRUNCATE list)
- Modify: `internal/api/testdb_test.go` (TRUNCATE list)

The FK on `account_balance_snapshots.account_id` has **no ON DELETE CASCADE** (accounts are never deleted in prod — deactivated instead). Both test harnesses run `DELETE FROM accounts WHERE id <> 'venmo'` — snapshot rows would block that delete, so the snapshots table must join the TRUNCATE that runs first.

- [ ] **Step 1: Write the migration**

Create `internal/migrate/migrations/0004_balance_snapshots.sql`:

```sql
-- +goose Up
CREATE TABLE account_balance_snapshots (
    account_id    text NOT NULL REFERENCES accounts(id),
    snapshot_date date NOT NULL,
    balance       numeric(14,2) NOT NULL,
    PRIMARY KEY (account_id, snapshot_date)
);

ALTER TABLE accounts ADD COLUMN is_manual boolean NOT NULL DEFAULT false;

-- +goose Down
ALTER TABLE accounts DROP COLUMN is_manual;
DROP TABLE account_balance_snapshots;
```

- [ ] **Step 2: Update both test harness resets**

In `internal/store/testdb_test.go` AND `internal/api/testdb_test.go`, change the first TRUNCATE statement (identical in both files):

```go
`TRUNCATE transactions, sync_runs, budgets, account_balance_snapshots RESTART IDENTITY CASCADE`,
```

(was `TRUNCATE transactions, sync_runs, budgets RESTART IDENTITY CASCADE`).

- [ ] **Step 3: Verify migration applies and suite still passes**

Run: `go test ./... -count=1`
Expected: all packages PASS (goose applies 0004 on first harness run; no behavior change yet).

- [ ] **Step 4: Commit**

```bash
git add internal/migrate/migrations/0004_balance_snapshots.sql internal/store/testdb_test.go internal/api/testdb_test.go
git commit -m "feat: add account_balance_snapshots table + accounts.is_manual (migration 0004)"
```

---

### Task 2: Store — `CaptureBalanceSnapshots`

**Files:**
- Create: `internal/store/snapshots.go`
- Create: `internal/store/snapshots_test.go`

- [ ] **Step 1: Write the failing tests**

Create `internal/store/snapshots_test.go`:

```go
package store

import (
	"context"
	"testing"
)

func snapCount(t *testing.T, s *Store, id string) int {
	t.Helper()
	var n int
	if err := s.Pool.QueryRow(context.Background(),
		`SELECT count(*) FROM account_balance_snapshots WHERE account_id = $1`, id).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func snapBalance(t *testing.T, s *Store, id, date string) string {
	t.Helper()
	var bal string
	if err := s.Pool.QueryRow(context.Background(),
		`SELECT balance::text FROM account_balance_snapshots WHERE account_id = $1 AND snapshot_date = $2::date`,
		id, date).Scan(&bal); err != nil {
		t.Fatal(err)
	}
	return bal
}

func TestCaptureBalanceSnapshots(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()

	// a1 has a balance+date; a2 has neither (like Venmo) and must be skipped.
	if err := s.UpsertAccounts(ctx, []Account{
		{ID: "a1", Name: "A1", Org: "t", Owner: "scott", Balance: "100.00", BalanceDate: day("2026-07-20")},
		{ID: "a2", Name: "A2", Org: "t", Owner: "scott"},
	}); err != nil {
		t.Fatal(err)
	}

	if err := s.CaptureBalanceSnapshots(ctx); err != nil {
		t.Fatal(err)
	}
	if got := snapCount(t, s, "a1"); got != 1 {
		t.Fatalf("a1 snapshots = %d, want 1", got)
	}
	if got := snapCount(t, s, "a2"); got != 0 {
		t.Fatalf("a2 (no balance) snapshots = %d, want 0", got)
	}
	if got := snapBalance(t, s, "a1", "2026-07-20"); got != "100.00" {
		t.Fatalf("a1 balance = %s, want 100.00", got)
	}

	// Same balance_date, new balance (evening sync) — overwrites, no new row.
	if err := s.UpsertAccounts(ctx, []Account{
		{ID: "a1", Name: "A1", Org: "t", Owner: "scott", Balance: "150.00", BalanceDate: day("2026-07-20")},
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.CaptureBalanceSnapshots(ctx); err != nil {
		t.Fatal(err)
	}
	if got := snapCount(t, s, "a1"); got != 1 {
		t.Fatalf("after same-date recapture: snapshots = %d, want 1", got)
	}
	if got := snapBalance(t, s, "a1", "2026-07-20"); got != "150.00" {
		t.Fatalf("after same-date recapture: balance = %s, want 150.00", got)
	}

	// New balance_date — second row appears, first row untouched.
	if err := s.UpsertAccounts(ctx, []Account{
		{ID: "a1", Name: "A1", Org: "t", Owner: "scott", Balance: "175.00", BalanceDate: day("2026-07-21")},
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.CaptureBalanceSnapshots(ctx); err != nil {
		t.Fatal(err)
	}
	if got := snapCount(t, s, "a1"); got != 2 {
		t.Fatalf("after new-date capture: snapshots = %d, want 2", got)
	}
	if got := snapBalance(t, s, "a1", "2026-07-21"); got != "175.00" {
		t.Fatalf("new-date balance = %s, want 175.00", got)
	}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `go test ./internal/store/ -count=1 -run TestCaptureBalanceSnapshots -v`
Expected: FAIL to compile — `s.CaptureBalanceSnapshots undefined`.

- [ ] **Step 3: Implement**

Create `internal/store/snapshots.go`:

```go
package store

import "context"

// CaptureBalanceSnapshots upserts one balance snapshot per account, keyed on
// the account's balance_date rather than today — a stale feed keeps
// overwriting its last real date instead of fabricating fresh history.
// Idempotent: the evening sync overwrites the morning row for the same date.
// Accounts with no balance or no balance_date (e.g. Venmo) are skipped.
func (s *Store) CaptureBalanceSnapshots(ctx context.Context) error {
	_, err := s.Pool.Exec(ctx, `
		INSERT INTO account_balance_snapshots (account_id, snapshot_date, balance)
		SELECT id, balance_date, balance
		FROM accounts
		WHERE balance IS NOT NULL AND balance_date IS NOT NULL
		ON CONFLICT (account_id, snapshot_date)
			DO UPDATE SET balance = EXCLUDED.balance`)
	return err
}
```

- [ ] **Step 4: Run to verify pass**

Run: `go test ./internal/store/ -count=1 -run TestCaptureBalanceSnapshots -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/store/snapshots.go internal/store/snapshots_test.go
git commit -m "feat: capture daily balance snapshots keyed on balance_date"
```

---

### Task 3: Sync integration — capture after `UpsertAccounts`

**Files:**
- Modify: `internal/ingest/sync.go`

No new test: the capture statement itself is covered by Task 2's store test, and the ingest package has no fake-SimpleFIN e2e harness to assert call ordering. The change is a three-line delegation; compile + full suite is the gate.

- [ ] **Step 1: Add the capture call**

In `internal/ingest/sync.go`, find:

```go
	if err := s.UpsertAccounts(ctx, accts); err != nil {
		return fail(err)
	}
```

Immediately after that block (before the `UpsertTransactions` call), insert:

```go
	// Snapshot capture must never hold transaction ingestion hostage — log
	// and continue on failure.
	if err := s.CaptureBalanceSnapshots(ctx); err != nil {
		log.Printf("balance snapshot capture: %v", err)
	}
```

Add `"log"` to the file's imports if not already present.

- [ ] **Step 2: Verify build + full suite**

Run: `go build ./... && go test ./... -count=1`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add internal/ingest/sync.go
git commit -m "feat: capture balance snapshots during sync (non-fatal on error)"
```

---

### Task 4: `GET /api/networth` — report queries + handler + tests

**Files:**
- Create: `internal/report/networth.go`
- Create: `internal/api/networth.go`
- Create: `internal/api/networth_test.go`
- Modify: `internal/api/server.go` (add `s.registerNetWorth()` to `routes()`)

Key semantics (from spec):
- Continuous daily axis from range start to `CURRENT_DATE`; per account, each date carries the last snapshot **on or before** that date (carry-forward). Dates where *no* account has any snapshot yet produce no series point (no fabricated history; empty DB ⇒ empty `series`).
- `range`: `1m`→30 days, `3m`→91, `6m`→182, `1y`→365, `all`→from earliest snapshot. Default `3m`. Invalid → 400.
- `view` filters `accounts.owner` **directly** — accounts have no `owner_override` column, so do NOT reuse `report.ownerFilter` (its `COALESCE(t.owner_override, a.owner)` fragment is wrong here).
- Only `active = true` accounts, in both series and account list.

- [ ] **Step 1: Write the failing handler tests**

Create `internal/api/networth_test.go`:

```go
package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/vollminlab/vollmint/internal/store"
)

// seedAccount inserts a bare active account (no balance, no snapshots).
func seedAccount(t *testing.T, s *store.Store, id, owner string) {
	t.Helper()
	if _, err := s.Pool.Exec(context.Background(), `
		INSERT INTO accounts (id, name, org, currency, owner)
		VALUES ($1, $1, 't', 'USD', $2)`, id, owner); err != nil {
		t.Fatal(err)
	}
}

// seedSnap upserts a snapshot daysAgo days before CURRENT_DATE.
func seedSnap(t *testing.T, s *store.Store, id string, daysAgo int, balance string) {
	t.Helper()
	if _, err := s.Pool.Exec(context.Background(), `
		INSERT INTO account_balance_snapshots (account_id, snapshot_date, balance)
		VALUES ($1, CURRENT_DATE - $2::int, $3::numeric)
		ON CONFLICT (account_id, snapshot_date) DO UPDATE SET balance = EXCLUDED.balance`,
		id, daysAgo, balance); err != nil {
		t.Fatal(err)
	}
}

type networthBody struct {
	Series []struct {
		Date     string            `json:"date"`
		Total    string            `json:"total"`
		Accounts map[string]string `json:"accounts"`
	} `json:"series"`
	Accounts []struct {
		ID          string `json:"id"`
		Name        string `json:"name"`
		Owner       string `json:"owner"`
		IsManual    bool   `json:"is_manual"`
		Balance     string `json:"balance"`
		BalanceDate string `json:"balance_date"`
	} `json:"accounts"`
}

func getNetworth(t *testing.T, srv *Server, query string) (int, networthBody) {
	t.Helper()
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/networth"+query, nil)
	srv.Handler().ServeHTTP(rec, req)
	var body networthBody
	if rec.Code == http.StatusOK {
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
			t.Fatalf("decode: %v body=%s", err, rec.Body.String())
		}
	}
	return rec.Code, body
}

func TestNetWorthCarryForward(t *testing.T) {
	s := testStore(t)
	seedAccount(t, s, "a1", "scott")
	seedSnap(t, s, "a1", 4, "100.00")
	seedSnap(t, s, "a1", 1, "200.00")
	srv := New(s)

	code, body := getNetworth(t, srv, "?view=scott&range=1m")
	if code != http.StatusOK {
		t.Fatalf("status = %d", code)
	}
	// Axis starts at a1's first snapshot (4 days ago), ends today: 5 points.
	if len(body.Series) != 5 {
		t.Fatalf("series len = %d, want 5: %+v", len(body.Series), body.Series)
	}
	if body.Series[0].Total != "100.00" {
		t.Errorf("day-4 total = %s, want 100.00", body.Series[0].Total)
	}
	// Day-3 has no snapshot — carry-forward from day-4.
	if body.Series[1].Total != "100.00" {
		t.Errorf("day-3 (carry-forward) total = %s, want 100.00", body.Series[1].Total)
	}
	if body.Series[3].Total != "200.00" {
		t.Errorf("day-1 total = %s, want 200.00", body.Series[3].Total)
	}
	// Today has no snapshot — carries forward day-1's value.
	if body.Series[4].Total != "200.00" {
		t.Errorf("today (carry-forward) total = %s, want 200.00", body.Series[4].Total)
	}
	if got := body.Series[4].Accounts["a1"]; got != "200.00" {
		t.Errorf("today a1 = %s, want 200.00", got)
	}
}

func TestNetWorthViewFilter(t *testing.T) {
	s := testStore(t)
	seedAccount(t, s, "a1", "scott")
	seedAccount(t, s, "a2", "nikki")
	seedSnap(t, s, "a1", 0, "100.00")
	seedSnap(t, s, "a2", 0, "50.00")
	srv := New(s)

	code, body := getNetworth(t, srv, "?view=scott&range=1m")
	if code != http.StatusOK {
		t.Fatalf("status = %d", code)
	}
	last := body.Series[len(body.Series)-1]
	if last.Total != "100.00" {
		t.Errorf("scott total = %s, want 100.00", last.Total)
	}
	if _, ok := last.Accounts["a2"]; ok {
		t.Error("nikki account a2 leaked into scott series")
	}
	ids := map[string]bool{}
	for _, a := range body.Accounts {
		ids[a.ID] = true
	}
	if !ids["a1"] || ids["a2"] {
		t.Errorf("scott account list wrong: %v", ids)
	}

	code, body = getNetworth(t, srv, "?view=household&range=1m")
	if code != http.StatusOK {
		t.Fatalf("status = %d", code)
	}
	last = body.Series[len(body.Series)-1]
	if last.Total != "150.00" {
		t.Errorf("household total = %s, want 150.00", last.Total)
	}
}

func TestNetWorthExcludesInactive(t *testing.T) {
	s := testStore(t)
	seedAccount(t, s, "a1", "scott")
	seedAccount(t, s, "dead", "scott")
	seedSnap(t, s, "a1", 0, "100.00")
	seedSnap(t, s, "dead", 0, "999.00")
	if _, err := s.Pool.Exec(context.Background(),
		`UPDATE accounts SET active = false WHERE id = 'dead'`); err != nil {
		t.Fatal(err)
	}
	srv := New(s)

	_, body := getNetworth(t, srv, "?view=scott&range=1m")
	last := body.Series[len(body.Series)-1]
	if last.Total != "100.00" {
		t.Errorf("total = %s, want 100.00 (inactive excluded)", last.Total)
	}
	for _, a := range body.Accounts {
		if a.ID == "dead" {
			t.Error("inactive account listed")
		}
	}
}

func TestNetWorthEmptyAndValidation(t *testing.T) {
	s := testStore(t)
	seedAccount(t, s, "a1", "scott")
	srv := New(s)

	// No snapshots anywhere: empty (non-null) series, populated accounts.
	code, body := getNetworth(t, srv, "?view=scott")
	if code != http.StatusOK {
		t.Fatalf("status = %d", code)
	}
	if body.Series == nil || len(body.Series) != 0 {
		t.Errorf("series = %v, want empty non-null", body.Series)
	}
	ids := map[string]bool{}
	for _, a := range body.Accounts {
		ids[a.ID] = true
	}
	if !ids["a1"] {
		t.Error("account list missing a1")
	}

	if code, _ := getNetworth(t, srv, "?view=bogus"); code != http.StatusBadRequest {
		t.Errorf("bad view status = %d, want 400", code)
	}
	if code, _ := getNetworth(t, srv, "?view=scott&range=2w"); code != http.StatusBadRequest {
		t.Errorf("bad range status = %d, want 400", code)
	}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `go test ./internal/api/ -count=1 -run TestNetWorth -v`
Expected: FAIL to compile (no `registerNetWorth`/handler yet).

- [ ] **Step 3: Implement the report queries**

Create `internal/report/networth.go`:

```go
package report

import (
	"context"
	"fmt"

	"github.com/vollminlab/vollmint/internal/store"
)

// NetWorthPoint is one day of the net-worth series. Total and per-account
// balances are decimal strings. Accounts maps account id -> carried-forward
// balance; accounts with no snapshot on or before the date are absent.
type NetWorthPoint struct {
	Date     string            `json:"date"` // YYYY-MM-DD
	Total    string            `json:"total"`
	Accounts map[string]string `json:"accounts"`
}

// NetWorthAccount is one row of the current-accounts list on the Net Worth
// page. Balance/BalanceDate are "" when the account has never reported one.
type NetWorthAccount struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Owner       string `json:"owner"`
	IsManual    bool   `json:"is_manual"`
	Balance     string `json:"balance"`
	BalanceDate string `json:"balance_date"` // YYYY-MM-DD
}

// accountOwnerFilter filters accounts by owner for scott/nikki/joint views
// using placeholder $argN; household applies no filter. Unlike ownerFilter it
// matches a.owner directly — accounts have no owner_override column.
func accountOwnerFilter(view string, argN int) (string, []any) {
	switch view {
	case "scott", "nikki", "joint":
		return fmt.Sprintf(" AND a.owner = $%d", argN), []any{view}
	}
	return "", nil
}

// NetWorthSeries returns the daily net-worth series for the last `days` days
// (0 = since the earliest snapshot). Each account's value on a date is its
// last snapshot on or before that date; dates before every account's first
// snapshot yield no point at all — history is never fabricated.
func NetWorthSeries(ctx context.Context, s *store.Store, view string, days int) ([]NetWorthPoint, error) {
	own, args := accountOwnerFilter(view, 2)
	q := `
		WITH bounds AS (
		  SELECT CASE
		    WHEN $1::int > 0 THEN CURRENT_DATE - ($1::int - 1)
		    ELSE COALESCE((SELECT MIN(snapshot_date) FROM account_balance_snapshots), CURRENT_DATE)
		  END AS start
		),
		days AS (
		  SELECT generate_series((SELECT start FROM bounds), CURRENT_DATE, interval '1 day')::date AS d
		),
		acct AS (
		  SELECT a.id FROM accounts a WHERE a.active` + own + `
		),
		pts AS (
		  SELECT days.d, acct.id AS account_id, sn.balance
		  FROM days
		  CROSS JOIN acct
		  JOIN LATERAL (
		    SELECT balance FROM account_balance_snapshots s
		    WHERE s.account_id = acct.id AND s.snapshot_date <= days.d
		    ORDER BY s.snapshot_date DESC LIMIT 1
		  ) sn ON true
		)
		SELECT to_char(pts.d, 'YYYY-MM-DD'), pts.account_id, pts.balance::text,
		       SUM(pts.balance) OVER (PARTITION BY pts.d)::text
		FROM pts
		ORDER BY pts.d, pts.account_id`
	full := append([]any{days}, args...)
	rows, err := s.Pool.Query(ctx, q, full...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]NetWorthPoint, 0)
	for rows.Next() {
		var date, id, bal, total string
		if err := rows.Scan(&date, &id, &bal, &total); err != nil {
			return nil, err
		}
		if len(out) == 0 || out[len(out)-1].Date != date {
			out = append(out, NetWorthPoint{Date: date, Total: total, Accounts: map[string]string{}})
		}
		out[len(out)-1].Accounts[id] = bal
	}
	return out, rows.Err()
}

// NetWorthAccounts lists active accounts for the view with their current
// balances, for the account list under the chart.
func NetWorthAccounts(ctx context.Context, s *store.Store, view string) ([]NetWorthAccount, error) {
	own, args := accountOwnerFilter(view, 1)
	q := `
		SELECT a.id, a.name, a.owner, a.is_manual,
		       COALESCE(a.balance::text, ''),
		       COALESCE(to_char(a.balance_date, 'YYYY-MM-DD'), '')
		FROM accounts a
		WHERE a.active` + own + `
		ORDER BY a.name`
	rows, err := s.Pool.Query(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]NetWorthAccount, 0)
	for rows.Next() {
		var a NetWorthAccount
		if err := rows.Scan(&a.ID, &a.Name, &a.Owner, &a.IsManual, &a.Balance, &a.BalanceDate); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}
```

- [ ] **Step 4: Implement the handler + route registration**

Create `internal/api/networth.go`:

```go
package api

import (
	"log"
	"net/http"

	"github.com/vollminlab/vollmint/internal/report"
)

// rangeDays maps the networth range param to a day count; 0 = all history.
var rangeDays = map[string]int{"1m": 30, "3m": 91, "6m": 182, "1y": 365, "all": 0}

func (s *Server) registerNetWorth() {
	s.mux.HandleFunc("GET /api/networth", s.handleNetWorth)
}

func (s *Server) handleNetWorth(w http.ResponseWriter, r *http.Request) {
	view := r.URL.Query().Get("view")
	if view == "" {
		view = "household"
	}
	if !validView(view) {
		writeErr(w, http.StatusBadRequest, "invalid view")
		return
	}
	rng := r.URL.Query().Get("range")
	if rng == "" {
		rng = "3m"
	}
	days, ok := rangeDays[rng]
	if !ok {
		writeErr(w, http.StatusBadRequest, "range must be one of 1m, 3m, 6m, 1y, all")
		return
	}
	series, err := report.NetWorthSeries(r.Context(), s.store, view, days)
	if err != nil {
		log.Printf("networth series: %v", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	accounts, err := report.NetWorthAccounts(r.Context(), s.store, view)
	if err != nil {
		log.Printf("networth accounts: %v", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	if series == nil {
		series = []report.NetWorthPoint{}
	}
	if accounts == nil {
		accounts = []report.NetWorthAccount{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"series": series, "accounts": accounts})
}
```

In `internal/api/server.go`, inside `routes()`, add `s.registerNetWorth()` on its own line after `s.registerSync()` and before `s.registerStatic()` (static must stay last — it owns the catch-all `/`).

- [ ] **Step 5: Run to verify pass**

Run: `go test ./internal/api/ -count=1 -run TestNetWorth -v`
Expected: PASS (all 4 tests).

- [ ] **Step 6: Full suite**

Run: `go test ./... -count=1`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add internal/report/networth.go internal/api/networth.go internal/api/networth_test.go internal/api/server.go
git commit -m "feat: GET /api/networth — daily carry-forward net-worth series"
```

---

### Task 5: Store — manual account create + balance update

**Files:**
- Modify: `internal/store/snapshots.go` (append manual-account code)
- Modify: `internal/store/snapshots_test.go` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `internal/store/snapshots_test.go`:

```go
func TestCreateManualAccount(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()

	id, err := s.CreateManualAccount(ctx, "My 401k!", "scott", "412000.00")
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	if id != "manual-my-401k" {
		t.Fatalf("id = %s, want manual-my-401k", id)
	}

	var org, currency, owner, balance string
	var isManual, active bool
	var dateIsToday bool
	if err := s.Pool.QueryRow(ctx, `
		SELECT org, currency, owner, balance::text, is_manual, active,
		       balance_date = CURRENT_DATE
		FROM accounts WHERE id = $1`, id).
		Scan(&org, &currency, &owner, &balance, &isManual, &active, &dateIsToday); err != nil {
		t.Fatal(err)
	}
	if org != "Manual" || currency != "USD" || owner != "scott" || balance != "412000.00" ||
		!isManual || !active || !dateIsToday {
		t.Errorf("row = org=%s currency=%s owner=%s balance=%s manual=%v active=%v today=%v",
			org, currency, owner, balance, isManual, active, dateIsToday)
	}
	if got := snapCount(t, s, id); got != 1 {
		t.Errorf("first snapshot rows = %d, want 1", got)
	}

	// Same derived id -> conflict.
	if _, err := s.CreateManualAccount(ctx, "my 401K", "scott", "1.00"); err != ErrConflict {
		t.Errorf("duplicate err = %v, want ErrConflict", err)
	}
	// Negative balance is a liability — allowed.
	if _, err := s.CreateManualAccount(ctx, "Mortgage", "joint", "-250000.00"); err != nil {
		t.Errorf("negative balance: %v", err)
	}
	// Garbage balance rejected before any SQL.
	if _, err := s.CreateManualAccount(ctx, "Bad", "scott", "12.345"); err != ErrInvalidAmount {
		t.Errorf("bad balance err = %v, want ErrInvalidAmount", err)
	}
}

func TestUpdateManualBalance(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()

	id, err := s.CreateManualAccount(ctx, "401k", "scott", "412000.00")
	if err != nil {
		t.Fatal(err)
	}
	if err := s.UpdateManualBalance(ctx, id, "415250.00"); err != nil {
		t.Fatalf("update: %v", err)
	}
	var balance string
	var dateIsToday bool
	if err := s.Pool.QueryRow(ctx,
		`SELECT balance::text, balance_date = CURRENT_DATE FROM accounts WHERE id = $1`, id).
		Scan(&balance, &dateIsToday); err != nil {
		t.Fatal(err)
	}
	if balance != "415250.00" || !dateIsToday {
		t.Errorf("balance = %s today=%v, want 415250.00 true", balance, dateIsToday)
	}
	// Create-day snapshot was upserted, not duplicated: still one row, new value.
	if got := snapCount(t, s, id); got != 1 {
		t.Errorf("snapshot rows = %d, want 1", got)
	}

	// Synced accounts are owned by the sync — reject edits.
	if err := s.UpsertAccounts(ctx, []Account{
		{ID: "synced-1", Name: "S", Org: "t", Owner: "scott", Balance: "10.00", BalanceDate: day("2026-07-20")},
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.UpdateManualBalance(ctx, "synced-1", "99.00"); err != ErrNotManual {
		t.Errorf("synced err = %v, want ErrNotManual", err)
	}
	if err := s.UpdateManualBalance(ctx, "nope", "1.00"); err != ErrNotFound {
		t.Errorf("unknown err = %v, want ErrNotFound", err)
	}
	if err := s.UpdateManualBalance(ctx, id, "abc"); err != ErrInvalidAmount {
		t.Errorf("bad balance err = %v, want ErrInvalidAmount", err)
	}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `go test ./internal/store/ -count=1 -run 'TestCreateManualAccount|TestUpdateManualBalance' -v`
Expected: FAIL to compile — methods and sentinels undefined.

- [ ] **Step 3: Implement**

Append to `internal/store/snapshots.go` (extend the imports to `"context"`, `"errors"`, `"fmt"`, `"regexp"`, `"strings"`, and `"github.com/jackc/pgx/v5"`):

```go
// ErrNotManual marks balance edits aimed at synced accounts — the sync owns
// those balances and would silently overwrite the edit.
var ErrNotManual = errors.New("account is not manual")

// ErrConflict marks a manual-account id collision.
var ErrConflict = errors.New("already exists")

var manualSlugRe = regexp.MustCompile(`[^a-z0-9]+`)

// ManualAccountID derives the deterministic id for a manual account name:
// lowercase, runs of non-alphanumerics collapsed to '-', edges trimmed.
// Returns "manual-" (invalid) when the name has no letters or digits.
func ManualAccountID(name string) string {
	slug := manualSlugRe.ReplaceAllString(strings.ToLower(name), "-")
	return "manual-" + strings.Trim(slug, "-")
}

// CreateManualAccount inserts a manual account and its first snapshot in one
// transaction and returns the derived id. Negative balances are liabilities.
func (s *Store) CreateManualAccount(ctx context.Context, name, owner, balance string) (string, error) {
	if !amountRe.MatchString(balance) {
		return "", ErrInvalidAmount
	}
	id := ManualAccountID(name)
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return "", fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback(ctx)
	tag, err := tx.Exec(ctx, `
		INSERT INTO accounts (id, name, org, currency, owner, balance, balance_date, is_manual)
		VALUES ($1, $2, 'Manual', 'USD', $3, $4, CURRENT_DATE, true)
		ON CONFLICT (id) DO NOTHING`, id, name, owner, balance)
	if err != nil {
		return "", fmt.Errorf("insert manual account %s: %w", id, err)
	}
	if tag.RowsAffected() == 0 {
		return "", ErrConflict
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO account_balance_snapshots (account_id, snapshot_date, balance)
		VALUES ($1, CURRENT_DATE, $2)`, id, balance); err != nil {
		return "", fmt.Errorf("first snapshot %s: %w", id, err)
	}
	if err := tx.Commit(ctx); err != nil {
		return "", fmt.Errorf("commit tx: %w", err)
	}
	return id, nil
}

// UpdateManualBalance sets a manual account's balance as of today and upserts
// today's snapshot in the same transaction, so the graph reflects the edit
// immediately. Synced accounts are rejected with ErrNotManual.
func (s *Store) UpdateManualBalance(ctx context.Context, id, balance string) error {
	if !amountRe.MatchString(balance) {
		return ErrInvalidAmount
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("begin tx: %w", err)
	}
	defer tx.Rollback(ctx)
	var isManual bool
	err = tx.QueryRow(ctx, `SELECT is_manual FROM accounts WHERE id = $1`, id).Scan(&isManual)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return fmt.Errorf("lookup account %s: %w", id, err)
	}
	if !isManual {
		return ErrNotManual
	}
	if _, err := tx.Exec(ctx,
		`UPDATE accounts SET balance = $2, balance_date = CURRENT_DATE WHERE id = $1`,
		id, balance); err != nil {
		return fmt.Errorf("update balance %s: %w", id, err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO account_balance_snapshots (account_id, snapshot_date, balance)
		VALUES ($1, CURRENT_DATE, $2)
		ON CONFLICT (account_id, snapshot_date) DO UPDATE SET balance = EXCLUDED.balance`,
		id, balance); err != nil {
		return fmt.Errorf("upsert snapshot %s: %w", id, err)
	}
	return tx.Commit(ctx)
}
```

Note: `amountRe`, `ErrInvalidAmount`, `ErrNotFound` already exist in the store package (`store.go` / `query.go`) — reuse them, do not redeclare.

- [ ] **Step 4: Run to verify pass**

Run: `go test ./internal/store/ -count=1 -run 'TestCreateManualAccount|TestUpdateManualBalance' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/store/snapshots.go internal/store/snapshots_test.go
git commit -m "feat: manual account creation + balance updates with snapshot writes"
```

---

### Task 6: API — `POST /api/accounts/manual` + `PUT /api/accounts/{id}/balance`

**Files:**
- Modify: `internal/api/networth.go` (add handlers + routes)
- Modify: `internal/api/networth_test.go` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `internal/api/networth_test.go` (add `"strings"` to its imports):

```go
func doJSON(t *testing.T, srv *Server, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	srv.Handler().ServeHTTP(rec, req)
	return rec
}

func TestCreateManualAccountEndpoint(t *testing.T) {
	s := testStore(t)
	srv := New(s)

	rec := doJSON(t, srv, http.MethodPost, "/api/accounts/manual",
		`{"name":"401k","owner":"scott","balance":"412000.00"}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d body=%s", rec.Code, rec.Body.String())
	}
	var created struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &created); err != nil {
		t.Fatal(err)
	}
	if created.ID != "manual-401k" {
		t.Errorf("id = %s, want manual-401k", created.ID)
	}

	// Validation matrix.
	for name, tc := range map[string]struct {
		body string
		want int
	}{
		"duplicate":    {`{"name":"401k","owner":"scott","balance":"1.00"}`, http.StatusConflict},
		"bad owner":    {`{"name":"X","owner":"household","balance":"1.00"}`, http.StatusBadRequest},
		"bad balance":  {`{"name":"Y","owner":"scott","balance":"1.2.3"}`, http.StatusBadRequest},
		"empty name":   {`{"name":"  ","owner":"scott","balance":"1.00"}`, http.StatusBadRequest},
		"symbols name": {`{"name":"!!!","owner":"scott","balance":"1.00"}`, http.StatusBadRequest},
		"bad json":     {`{`, http.StatusBadRequest},
	} {
		rec := doJSON(t, srv, http.MethodPost, "/api/accounts/manual", tc.body)
		if rec.Code != tc.want {
			t.Errorf("%s: status = %d, want %d (body=%s)", name, rec.Code, tc.want, rec.Body.String())
		}
	}
}

func TestUpdateManualBalanceEndpoint(t *testing.T) {
	s := testStore(t)
	if _, err := s.CreateManualAccount(context.Background(), "401k", "scott", "412000.00"); err != nil {
		t.Fatal(err)
	}
	seedAccount(t, s, "synced-1", "scott")
	srv := New(s)

	rec := doJSON(t, srv, http.MethodPut, "/api/accounts/manual-401k/balance", `{"balance":"415250.00"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", rec.Code, rec.Body.String())
	}
	var balance string
	if err := s.Pool.QueryRow(context.Background(),
		`SELECT balance::text FROM accounts WHERE id = 'manual-401k'`).Scan(&balance); err != nil {
		t.Fatal(err)
	}
	if balance != "415250.00" {
		t.Errorf("balance = %s, want 415250.00", balance)
	}

	if rec := doJSON(t, srv, http.MethodPut, "/api/accounts/synced-1/balance", `{"balance":"1.00"}`); rec.Code != http.StatusBadRequest {
		t.Errorf("synced: status = %d, want 400", rec.Code)
	}
	if rec := doJSON(t, srv, http.MethodPut, "/api/accounts/nope/balance", `{"balance":"1.00"}`); rec.Code != http.StatusNotFound {
		t.Errorf("unknown: status = %d, want 404", rec.Code)
	}
	if rec := doJSON(t, srv, http.MethodPut, "/api/accounts/manual-401k/balance", `{"balance":"xx"}`); rec.Code != http.StatusBadRequest {
		t.Errorf("bad balance: status = %d, want 400", rec.Code)
	}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `go test ./internal/api/ -count=1 -run 'TestCreateManualAccountEndpoint|TestUpdateManualBalanceEndpoint' -v`
Expected: FAIL — 404s (routes not registered).

- [ ] **Step 3: Implement**

In `internal/api/networth.go`: extend imports to include `"encoding/json"`, `"errors"`, `"strings"`, and `"github.com/vollminlab/vollmint/internal/store"`. Extend `registerNetWorth`:

```go
func (s *Server) registerNetWorth() {
	s.mux.HandleFunc("GET /api/networth", s.handleNetWorth)
	s.mux.HandleFunc("POST /api/accounts/manual", s.handleCreateManualAccount)
	s.mux.HandleFunc("PUT /api/accounts/{id}/balance", s.handleUpdateManualBalance)
}
```

Append the handlers:

```go
func (s *Server) handleCreateManualAccount(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name    string `json:"name"`
		Owner   string `json:"owner"`
		Balance string `json:"balance"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	name := strings.TrimSpace(body.Name)
	if store.ManualAccountID(name) == "manual-" {
		writeErr(w, http.StatusBadRequest, "name must contain a letter or number")
		return
	}
	switch body.Owner {
	case "scott", "nikki", "joint":
	default:
		writeErr(w, http.StatusBadRequest, "owner must be scott, nikki, or joint")
		return
	}
	id, err := s.store.CreateManualAccount(r.Context(), name, body.Owner, body.Balance)
	switch {
	case errors.Is(err, store.ErrInvalidAmount):
		writeErr(w, http.StatusBadRequest, "balance must be a decimal amount")
	case errors.Is(err, store.ErrConflict):
		writeErr(w, http.StatusConflict, "an account with this name already exists")
	case err != nil:
		log.Printf("create manual account: %v", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
	default:
		writeJSON(w, http.StatusCreated, map[string]string{"id": id})
	}
}

func (s *Server) handleUpdateManualBalance(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Balance string `json:"balance"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	err := s.store.UpdateManualBalance(r.Context(), r.PathValue("id"), body.Balance)
	switch {
	case errors.Is(err, store.ErrInvalidAmount):
		writeErr(w, http.StatusBadRequest, "balance must be a decimal amount")
	case errors.Is(err, store.ErrNotFound):
		writeErr(w, http.StatusNotFound, "account not found")
	case errors.Is(err, store.ErrNotManual):
		writeErr(w, http.StatusBadRequest, "balance edits are only allowed on manual accounts")
	case err != nil:
		log.Printf("update manual balance: %v", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
	default:
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	}
}
```

- [ ] **Step 4: Run to verify pass, then full suite**

Run: `go test ./internal/api/ -count=1 -run 'TestCreateManualAccountEndpoint|TestUpdateManualBalanceEndpoint' -v && go test ./... -count=1`
Expected: PASS everywhere.

- [ ] **Step 5: Commit**

```bash
git add internal/api/networth.go internal/api/networth_test.go
git commit -m "feat: manual account API endpoints (create + balance update)"
```

---

### Task 7: SPA — API client, route, nav

**Files:**
- Modify: `web/src/api.ts` (append types + fetchers)
- Modify: `web/src/App.tsx` (import + route)
- Modify: `web/src/components/Nav.tsx` (nav link)
- Modify: `web/src/App.test.tsx` (nav assertion)
- Create: `web/src/components/NetWorth.tsx` (minimal stub — fleshed out in Task 8)

- [ ] **Step 1: Append to `web/src/api.ts`**

```ts
export type NetWorthRange = '1m' | '3m' | '6m' | '1y' | 'all'

export interface NetWorthPoint {
  date: string
  total: string
  accounts: Record<string, string>
}

export interface NetWorthAccount {
  id: string
  name: string
  owner: string
  is_manual: boolean
  balance: string
  balance_date: string
}

export interface NetWorthResponse {
  series: NetWorthPoint[]
  accounts: NetWorthAccount[]
}

export function getNetWorth(view: View, range: NetWorthRange): Promise<NetWorthResponse> {
  return req(`/api/networth${buildQuery({ view, range })}`)
}

export function createManualAccount(body: {
  name: string
  owner: string
  balance: string
}): Promise<{ id: string }> {
  return req('/api/accounts/manual', jsonInit('POST', body))
}

export function updateManualBalance(id: string, balance: string): Promise<{ status: string }> {
  return req(`/api/accounts/${encodeURIComponent(id)}/balance`, jsonInit('PUT', { balance }))
}
```

- [ ] **Step 2: Create the stub page**

Create `web/src/components/NetWorth.tsx`:

```tsx
import type { View } from '../api'

export function NetWorth({ view }: { view: View }) {
  return <h2>Net Worth</h2>
}
```

(The full component replaces this in Task 8 — the stub exists so routing/nav land green in this commit. The unused `view` prop is intentional; Task 8 uses it.)

If `tsc` rejects the unused `view` parameter (`noUnusedParameters`), rename it to `_view` in the stub only; Task 8's full component uses `view`.

- [ ] **Step 3: Wire route + nav**

In `web/src/App.tsx`:
- Add import: `import { NetWorth } from './components/NetWorth'`
- Add route inside `<Routes>` after the Trends route:

```tsx
<Route path="/networth" element={<NetWorth view={view} />} />
```

In `web/src/components/Nav.tsx`, add after `{link('/trends', 'Trends')}`:

```tsx
{link('/networth', 'Net Worth')}
```

In `web/src/App.test.tsx`, the test `renders the nav with all six pages`: rename to `renders the nav with all seven pages` and add:

```tsx
expect(screen.getByRole('link', { name: 'Net Worth' })).toBeInTheDocument()
```

- [ ] **Step 4: Run the SPA suite + typecheck**

Run: `cd web && npm test && npm run build`
Expected: all tests PASS, `tsc -b && vite build` clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/api.ts web/src/App.tsx web/src/components/Nav.tsx web/src/App.test.tsx web/src/components/NetWorth.tsx
git commit -m "feat: net-worth API client, route, and nav entry"
```

---

### Task 8: SPA — full Net Worth page

**Files:**
- Modify: `web/src/components/NetWorth.tsx` (replace stub with full component)
- Create: `web/src/components/NetWorth.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/NetWorth.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { NetWorth } from './NetWorth'

const fixture = {
  series: [
    { date: '2026-08-01', total: '1500.00', accounts: { 'act-1': '2000.00', 'manual-mortgage': '-500.00' } },
    { date: '2026-08-02', total: '1600.00', accounts: { 'act-1': '2100.00', 'manual-mortgage': '-500.00' } },
  ],
  accounts: [
    {
      id: 'act-1', name: 'Ally Checking', owner: 'scott',
      is_manual: false, balance: '2100.00', balance_date: '2026-08-02',
    },
    {
      id: 'manual-mortgage', name: 'Mortgage', owner: 'joint',
      is_manual: true, balance: '-500.00', balance_date: '2026-08-02',
    },
  ],
}

function stubFetch(body: unknown = fixture) {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => body })
}

beforeEach(() => {
  vi.stubGlobal('fetch', stubFetch())
})

describe('NetWorth', () => {
  it('fetches the 3m range by default and renders the summary', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<NetWorth view="household" />)
    await waitFor(() => expect(screen.getByText('Ally Checking')).toBeInTheDocument())
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/api/networth')
    expect(url).toContain('view=household')
    expect(url).toContain('range=3m')
    expect(screen.getByText('Assets')).toBeInTheDocument()
    // These amounts appear in BOTH the summary and an account row — use getAllByText.
    expect(screen.getAllByText('$2,100.00').length).toBeGreaterThan(0)  // assets
    expect(screen.getAllByText('-$500.00').length).toBeGreaterThan(0)   // liabilities
    expect(screen.getByText('$1,600.00')).toBeInTheDocument()           // net worth (summary only)
  })

  it('refetches when a range button is clicked', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<NetWorth view="household" />)
    await waitFor(() => expect(screen.getByText('Ally Checking')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '1Y' }))
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => c[0] as string)
      expect(urls.some((u) => u.includes('range=1y'))).toBe(true)
    })
  })

  it('drills down to one account and back', async () => {
    render(<NetWorth view="household" />)
    await waitFor(() => expect(screen.getByText('Ally Checking')).toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Net Worth' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Ally Checking' }))
    expect(screen.getByRole('heading', { name: 'Ally Checking' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'All accounts' }))
    expect(screen.getByRole('heading', { name: 'Net Worth' })).toBeInTheDocument()
  })

  it('saves an inline manual balance edit via PUT', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<NetWorth view="household" />)
    await waitFor(() => expect(screen.getByText('Mortgage')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('new balance'), { target: { value: '-490.00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      const put = fetchMock.mock.calls.find((c) => (c[1] as RequestInit)?.method === 'PUT')
      expect(put).toBeTruthy()
      expect(put![0]).toContain('/api/accounts/manual-mortgage/balance')
      expect(put![1]!.body).toBe(JSON.stringify({ balance: '-490.00' }))
    })
  })

  it('adds a manual account via POST', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<NetWorth view="household" />)
    await waitFor(() => expect(screen.getByText('Mortgage')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('new account name'), { target: { value: '401k' } })
    fireEvent.change(screen.getByLabelText('new account owner'), { target: { value: 'scott' } })
    fireEvent.change(screen.getByLabelText('new account balance'), { target: { value: '412000.00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    await waitFor(() => {
      const post = fetchMock.mock.calls.find((c) => (c[1] as RequestInit)?.method === 'POST')
      expect(post).toBeTruthy()
      expect(post![0]).toContain('/api/accounts/manual')
      expect(post![1]!.body).toBe(
        JSON.stringify({ name: '401k', owner: 'scott', balance: '412000.00' }),
      )
    })
  })

  it('shows a stale hint for manual balances older than 60 days', async () => {
    const stale = {
      series: fixture.series,
      accounts: [
        { ...fixture.accounts[1], balance_date: '2020-01-01' },
      ],
    }
    vi.stubGlobal('fetch', stubFetch(stale))
    render(<NetWorth view="household" />)
    await waitFor(() => expect(screen.getByText('Mortgage')).toBeInTheDocument())
    expect(screen.getByText('update?')).toBeInTheDocument()
  })

  it('renders the empty state when there are no snapshots', async () => {
    vi.stubGlobal('fetch', stubFetch({ series: [], accounts: fixture.accounts }))
    render(<NetWorth view="household" />)
    await waitFor(() => expect(screen.getByText('Ally Checking')).toBeInTheDocument())
    expect(screen.getByText(/No balance history yet/)).toBeInTheDocument()
  })

  it('surfaces API errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ error: 'boom' }) }),
    )
    render(<NetWorth view="household" />)
    await waitFor(() => expect(screen.getByText('Error: boom')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web && npm test -- NetWorth`
Expected: FAIL — the stub renders none of the expected UI.

- [ ] **Step 3: Implement the full component**

Replace `web/src/components/NetWorth.tsx` entirely with:

```tsx
import { useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts'
import type { NetWorthAccount, NetWorthRange, NetWorthResponse, View } from '../api'
import { createManualAccount, getNetWorth, updateManualBalance } from '../api'
import { money, titleCase } from '../format'

const RANGES: NetWorthRange[] = ['1m', '3m', '6m', '1y', 'all']
const RANGE_LABELS: Record<NetWorthRange, string> = {
  '1m': '1M', '3m': '3M', '6m': '6M', '1y': '1Y', all: 'All',
}

// Manual balances older than this many days get a nudge to update.
const STALE_DAYS = 60

function isStale(a: NetWorthAccount): boolean {
  if (!a.is_manual || !a.balance_date) return false
  const age = (Date.now() - new Date(a.balance_date + 'T00:00:00').getTime()) / 86400000
  return age > STALE_DAYS
}

// Fixed-size chart: ResponsiveContainer renders nothing under jsdom (zero
// width), and 1000px fits the app's 1100px container.
export function NetWorth({ view }: { view: View }) {
  const [range, setRange] = useState<NetWorthRange>('3m')
  const [data, setData] = useState<NetWorthResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  // inline manual-balance edit
  const [editId, setEditId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')

  // add-account form
  const [newName, setNewName] = useState('')
  const [newOwner, setNewOwner] = useState('scott')
  const [newBalance, setNewBalance] = useState('')
  const [formErr, setFormErr] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setLoading(true)
    getNetWorth(view, range)
      .then((d) => { if (live) { setData(d); setErr(null) } })
      .catch((e) => { if (live) setErr(e.message) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [view, range, reloadKey])

  if (err) return <p style={{ color: 'var(--danger)' }}>Error: {err}</p>

  const accounts = data?.accounts ?? []
  const series = data?.series ?? []
  const selectedAccount = accounts.find((a) => a.id === selected)

  // Number() below is display totals / chart geometry only — money strings
  // stay strings everywhere else and render through money().
  const assets = accounts.reduce((sum, a) => {
    const n = Number(a.balance)
    return sum + (n > 0 ? n : 0)
  }, 0)
  const liabilities = accounts.reduce((sum, a) => {
    const n = Number(a.balance)
    return sum + (n < 0 ? n : 0)
  }, 0)
  const chartData = series.map((p) => ({
    date: p.date,
    value: selected
      ? p.accounts[selected] !== undefined ? Number(p.accounts[selected]) : null
      : Number(p.total),
  }))

  const saveEdit = async (id: string) => {
    try {
      setFormErr(null)
      await updateManualBalance(id, editValue)
      setEditId(null)
      setReloadKey((k) => k + 1)
    } catch (e) {
      setFormErr((e as Error).message)
    }
  }

  const addAccount = async () => {
    try {
      setFormErr(null)
      await createManualAccount({ name: newName, owner: newOwner, balance: newBalance })
      setNewName('')
      setNewBalance('')
      setReloadKey((k) => k + 1)
    } catch (e) {
      setFormErr((e as Error).message)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: '2rem', marginBottom: '1rem' }}>
        <div>
          <div style={{ color: 'var(--muted)' }}>Assets</div>
          <div style={{ fontSize: '1.2rem' }}>{money(assets.toFixed(2))}</div>
        </div>
        <div>
          <div style={{ color: 'var(--muted)' }}>Liabilities</div>
          <div style={{ fontSize: '1.2rem' }}>{money(liabilities.toFixed(2))}</div>
        </div>
        <div>
          <div style={{ color: 'var(--muted)' }}>Net worth</div>
          <div style={{ fontSize: '1.2rem', fontWeight: 700 }}>{money((assets + liabilities).toFixed(2))}</div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.5rem' }}>
        <h2 style={{ margin: 0 }}>{selectedAccount ? selectedAccount.name : 'Net Worth'}</h2>
        {selected && (
          <button onClick={() => setSelected(null)}>All accounts</button>
        )}
        <div style={{ display: 'flex', gap: '0.4rem', marginLeft: 'auto' }}>
          {RANGES.map((r) => (
            <button key={r} onClick={() => setRange(r)} aria-pressed={r === range}>
              {RANGE_LABELS[r]}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p style={{ color: 'var(--muted)' }}>Loading…</p>
      ) : series.length === 0 ? (
        <p style={{ color: 'var(--muted)' }}>
          No balance history yet — snapshots start accruing with the next sync.
        </p>
      ) : (
        <LineChart width={1000} height={360} data={chartData}>
          <CartesianGrid stroke="#262a33" />
          <XAxis dataKey="date" stroke="var(--muted)" />
          <YAxis stroke="var(--muted)" domain={['auto', 'auto']} />
          <Tooltip
            formatter={(v) => money(Number(v).toFixed(2))}
            contentStyle={{ background: 'var(--panel)', border: '1px solid #262a33' }}
          />
          <Line
            type="monotone"
            dataKey="value"
            name={selectedAccount ? selectedAccount.name : 'Net Worth'}
            stroke="var(--accent)"
            dot={false}
          />
        </LineChart>
      )}

      {formErr && <p style={{ color: 'var(--danger)' }}>Error: {formErr}</p>}

      <table style={{ width: '100%', marginTop: '1rem' }}>
        <tbody>
          {accounts.map((a) => (
            <tr key={a.id}>
              <td>
                <button onClick={() => setSelected(a.id)} style={{ fontWeight: a.id === selected ? 700 : 400 }}>
                  {a.name}
                </button>
              </td>
              <td style={{ color: 'var(--muted)' }}>{titleCase(a.owner)}</td>
              <td style={{ textAlign: 'right' }}>{a.balance ? money(a.balance) : '—'}</td>
              <td style={{ color: 'var(--muted)' }}>
                {a.balance_date}
                {isStale(a) && <span style={{ marginLeft: '0.4rem', color: 'var(--warn)' }}>update?</span>}
              </td>
              <td>
                {a.is_manual && editId !== a.id && (
                  <button onClick={() => { setEditId(a.id); setEditValue(a.balance) }}>Edit</button>
                )}
                {a.is_manual && editId === a.id && (
                  <span style={{ display: 'inline-flex', gap: '0.3rem' }}>
                    <input
                      aria-label="new balance"
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      style={{ width: '8rem' }}
                    />
                    <button onClick={() => saveEdit(a.id)}>Save</button>
                    <button onClick={() => setEditId(null)}>Cancel</button>
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form
        onSubmit={(e) => { e.preventDefault(); addAccount() }}
        style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem', alignItems: 'center' }}
      >
        <input
          aria-label="new account name"
          placeholder="Name (e.g. 401k)"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <select aria-label="new account owner" value={newOwner} onChange={(e) => setNewOwner(e.target.value)}>
          <option value="scott">Scott</option>
          <option value="nikki">Nikki</option>
          <option value="joint">Joint</option>
        </select>
        <input
          aria-label="new account balance"
          placeholder="Balance (negative = liability)"
          value={newBalance}
          onChange={(e) => setNewBalance(e.target.value)}
        />
        <button type="submit">Add account</button>
      </form>
    </div>
  )
}
```

Notes:
- `titleCase` exists in `web/src/format.ts`; if its signature differs from `(s: string) => string`, render the owner raw (`{a.owner}`) instead and adjust nothing else.
- If CSS variable `--warn` doesn't exist in `web/src/index.css`, use `var(--muted)` for the stale hint instead.

- [ ] **Step 4: Run to verify pass, then full SPA suite + build**

Run: `cd web && npm test && npm run build`
Expected: all tests PASS (existing 59 + new), build clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/NetWorth.tsx web/src/components/NetWorth.test.tsx
git commit -m "feat: Net Worth page — chart, drill-down, manual accounts"
```

---

### Task 9: Full verification sweep

**Files:** none (verification only; fix-ups if anything fails)

- [ ] **Step 1: Full Go suite**

Run: `go test ./... -count=1`
Expected: all PASS.

- [ ] **Step 2: Full SPA suite + production build**

Run: `cd web && npm test && npm run build`
Expected: all PASS, `tsc -b && vite build` clean.

- [ ] **Step 3: Production binary build**

Run: `./scripts/build.sh`
Expected: `./bin/vollmint` produced without error.

- [ ] **Step 4: Commit any fix-ups (only if steps 1–3 required changes)**

```bash
git add <specific files changed>
git commit -m "fix: <what was fixed>"
```

---

## Out of scope for this plan (later, separate PRs)

- Tagging `v0.4.0` (drives CI image + chart publish) — after PR merge, on Scott's word.
- k8s repo OCIRepository tag bump `0.3.2 → 0.4.0` — separate PR in `k8s-vollminlab-cluster` after the chart publishes.
