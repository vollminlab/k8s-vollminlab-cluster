# vollmint API + Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the HTTP REST API (`vollmint serve`) and the React/TypeScript single-page dashboard on top of the existing vollmint backend, delivering the four core reports and per-charge drill-down described in the spec.

**Architecture:** A new `internal/api` package exposes a stdlib `net/http` server (Go 1.22+ method+pattern routing, no third-party router). Read/report queries live in a new `internal/report` package and new read methods on `internal/store`; all money math is done in Postgres `numeric` and returned as decimal **strings** — no `float64` ever touches a value. The React SPA is built with Vite, compiled to `web/dist`, and embedded into the Go binary via `go:embed`; `vollmint serve` serves the API under `/api/*`, Prometheus metrics at `/metrics`, an unauthenticated `/healthz`, and the SPA (with client-side-routing fallback) for everything else.

**Tech Stack:** Go 1.26 stdlib `net/http` + `encoding/json`, `github.com/prometheus/client_golang`, existing `jackc/pgx/v5`; React 18 + TypeScript + Vite + Vitest + React Testing Library; Recharts for bars.

**Prerequisite:** Plan 1 (vollmint-backend) is merged to `main`. This plan starts from a clean `main` in `~/repos/vollminlab/vollmint`. Create the branch `feat/api-frontend` before Task 1.

---

## Context for the implementer (read once)

You are working in `~/repos/vollminlab/vollmint`, module `github.com/vollminlab/vollmint`. The backend already provides:

- `internal/store` — `Store{Pool *pgxpool.Pool}`, `store.New(ctx, url)`, `Close()`. Money is stored as Postgres `numeric` and everything upstream treats amounts as decimal **strings** validated by `^-?\d+(\.\d{1,2})?$`. `UpsertAccounts`, `UpsertTransactions` exist. This plan **adds read methods** to this package.
- `internal/ingest` — `ApplyRules(ctx, s) (int, error)` (re-runnable category rules over uncategorized rows), `ImportVenmo(ctx, s, r io.Reader) (*SyncResult, error)`, `MatchTransfers(ctx, s) (int, error)`, `Sync(...)`. `SyncResult{Upserted, Categorized, Paired, Swept int}`.
- `internal/migrate` — `Up(url)` applies embedded goose migrations.
- `cmd/vollmint/main.go` — dispatches `claim|sync|import-venmo`. This plan adds a `serve` subcommand.

**Database schema** (from `internal/migrate/migrations/0001_schema.sql`) — the columns you will query:

- `accounts(id text pk, name, org, currency, owner CHECK IN (scott,nikki,joint), balance numeric(14,2), balance_date date, active bool, created_at)`
- `transactions(id bigserial pk, source, external_id, account_id→accounts, posted date, amount numeric(12,2), description, payee, pending bool, category_id→categories, owner_override CHECK IN (scott,nikki,joint), transfer_peer_id→transactions, raw jsonb, created_at, updated_at, UNIQUE(source,external_id))`
- `categories(id serial pk, name UNIQUE, parent_id→categories, kind CHECK IN (spend,income,transfer,savings), is_vice bool)`
- `category_rules(id serial pk, priority int, match_type CHECK IN (substring,regex), pattern, category_id→categories)`
- `budgets(category_id→categories, month date, amount numeric(12,2), PRIMARY KEY(category_id, month))`
- `sync_runs(id bigserial pk, kind, started, finished, status CHECK IN (running,ok,partial,failed), window_start, window_end, rows_upserted int, detail)`

Seeded categories include `Transfer` (kind=transfer) and `Needs Venmo detail`; `Dining`, `Vices` have `is_vice=true`.

**Non-negotiable invariants (carry from plan 1):**

1. **Money is a decimal string end-to-end.** Never scan a `numeric` into `float64`. Scan into `string` (pgx supports `numeric → string` via `::text` cast in the query, which you MUST use so the value is exact and predictably formatted). Sum/average in SQL. The one place a float is acceptable is **chart bar width in the browser** (pixel geometry, never a displayed or stored value).
2. **Views are filters, not users.** A transaction's effective owner is `COALESCE(owner_override, account.owner)`. `view=household` applies no owner filter. Single Authentik login; the API trusts the forward-auth proxy and does no auth of its own.
3. **Ingestion/read separation.** The serve process has no SimpleFIN credential. There is **no** sync endpoint. The only write endpoints are: PATCH a transaction, CRUD categories/rules/budgets, and the Venmo CSV upload.
4. **`month` parameter format is `YYYY-MM`.** Resolve it to a first-of-month `date` in SQL and filter `posted >= $m AND posted < ($m + interval '1 month')`.
5. **Transfers are excluded from spend/income totals.** A row is a transfer if `transfer_peer_id IS NOT NULL` OR its category's `kind='transfer'`.

**Environment — every `go` command needs this exact single-line prefix:**

```bash
export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"
```

**DB-backed tests additionally need, on the same line:**

```bash
export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable'
```

Always run tests with `-count=1`. The dev Postgres 16 is at `localhost:5433` (user `postgres`, pass `dev`). Goose printing "no migrations to run" is benign.

**Test DB helper pattern** (copy the established one). Tests that touch the DB must serialize via `internal/testutil.SerializeDB(t, url)` (a `pg_advisory_lock(788401)`), apply migrations, and truncate mutable tables while preserving seed rows — exactly as `internal/store/testdb_test.go` and `internal/ingest/testdb_test.go` already do. You will create `internal/api/testdb_test.go` and `internal/report/testdb_test.go` mirroring that file.

---

## File structure

**Backend (Go):**

- `internal/store/query.go` — new read methods: `ListTransactions`, `GetTransaction`, `UpdateTransaction`, category CRUD, budget get/put, rule CRUD, `SyncStatus`. One file, one responsibility: read/light-write queries for the API. (Kept separate from `store.go` so the ingestion-write surface stays legible.)
- `internal/store/query_test.go` — tests for the above against real Postgres.
- `internal/report/report.go` — aggregation queries: `Summary`, `SpendByCategory`, `Recurring`. Pure read, returns decimal strings.
- `internal/report/report_test.go` — tests.
- `internal/report/testdb_test.go`, `internal/store/testdb_test.go` (exists) — test DB helpers.
- `internal/api/server.go` — `Server` struct, router (`http.ServeMux`), middleware (recover, log), `writeJSON`/`writeErr` helpers, `New(s *store.Store) *Server`, `Handler() http.Handler`.
- `internal/api/transactions.go`, `internal/api/summary.go`, `internal/api/categories.go`, `internal/api/rules.go`, `internal/api/budgets.go`, `internal/api/recurring.go`, `internal/api/imports.go`, `internal/api/sync.go` — one file per resource group; each registers its routes and holds its handlers.
- `internal/api/static.go` — `go:embed` of the SPA and the SPA/`healthz`/`metrics` wiring.
- `internal/api/*_test.go` — handler tests (httptest + real DB).
- `cmd/vollmint/serve.go` — `runServe` subcommand.

**Frontend (React/TS), all under `web/`:**

- `web/package.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/index.html`, `web/.gitignore`
- `web/src/main.tsx`, `web/src/App.tsx` — bootstrap + routing + shared view/month state
- `web/src/api.ts` — typed fetch client + response types
- `web/src/components/Dashboard.tsx`, `Transactions.tsx`, `Budgets.tsx`, `ViewSwitcher.tsx`, `MonthPager.tsx`, `SummaryCards.tsx`, `CategoryBars.tsx`, `NeedsAttention.tsx`
- `web/src/components/*.test.tsx` — Vitest component tests for drill-down filter plumbing
- `web/dist/` — Vite build output, embedded by Go, git-ignored

---

## Task 1: `serve` subcommand + HTTP skeleton + `/healthz`

**Files:**
- Create: `internal/api/server.go`
- Create: `internal/api/server_test.go`
- Create: `cmd/vollmint/serve.go`
- Modify: `cmd/vollmint/main.go` (add `serve` case)

- [ ] **Step 1: Write the failing test**

Create `internal/api/server_test.go`:

```go
package api

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthz(t *testing.T) {
	srv := New(nil) // healthz must not need the DB
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("healthz status = %d, want 200", rec.Code)
	}
	if got := rec.Body.String(); got != "ok" {
		t.Fatalf("healthz body = %q, want %q", got, "ok")
	}
}

func TestUnknownAPIRouteIs404(t *testing.T) {
	srv := New(nil)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/nope", nil)
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("unknown api route = %d, want 404", rec.Code)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && go test ./internal/api/ -run TestHealthz -count=1`
Expected: FAIL — `undefined: New`.

- [ ] **Step 3: Write minimal implementation**

Create `internal/api/server.go`:

```go
// Package api implements vollmint's HTTP surface: the JSON REST API under
// /api, Prometheus /metrics, an unauthenticated /healthz, and the embedded
// React SPA for all other paths. Auth is handled upstream by Authentik
// forward-auth; this server trusts the proxy.
package api

import (
	"encoding/json"
	"log"
	"net/http"
	"runtime/debug"

	"github.com/vollminlab/vollmint/internal/store"
)

// Server holds dependencies and builds the HTTP handler.
type Server struct {
	store *store.Store
	mux   *http.ServeMux
}

// New constructs a Server. store may be nil for tests that only exercise
// routes which do not touch the database (e.g. /healthz).
func New(s *store.Store) *Server {
	srv := &Server{store: s, mux: http.NewServeMux()}
	srv.routes()
	return srv
}

// routes registers every route group. Each group lives in its own file.
func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = w.Write([]byte("ok"))
	})
	s.registerTransactions()
	s.registerSummary()
	s.registerCategories()
	s.registerRules()
	s.registerBudgets()
	s.registerRecurring()
	s.registerImports()
	s.registerSync()
	s.registerStatic() // must be last: it owns the catch-all "/"
}

// Handler returns the fully wrapped handler (recover + log middleware).
func (s *Server) Handler() http.Handler {
	return logMiddleware(recoverMiddleware(s.mux))
}

func recoverMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if v := recover(); v != nil {
				log.Printf("panic serving %s %s: %v\n%s", r.Method, r.URL.Path, v, debug.Stack())
				http.Error(w, "internal server error", http.StatusInternalServerError)
			}
		}()
		next.ServeHTTP(w, r)
	})
}

func logMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sw := &statusWriter{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(sw, r)
		log.Printf("%s %s %d", r.Method, r.URL.Path, sw.status)
	})
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

// writeJSON serializes v as JSON with the given status code.
func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("writeJSON encode error: %v", err)
	}
}

// writeErr sends a JSON error envelope: {"error":"message"}.
func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}
```

Because `routes()` calls `registerStatic()` and the eight `register*` methods that later tasks create, add **temporary stubs now** so the package compiles. Create them at the bottom of `server.go` and delete each stub in the task that implements it for real:

```go
// --- temporary stubs (each replaced by its own task) ---
func (s *Server) registerTransactions() {}
func (s *Server) registerSummary()      {}
func (s *Server) registerCategories()   {}
func (s *Server) registerRules()        {}
func (s *Server) registerBudgets()      {}
func (s *Server) registerRecurring()    {}
func (s *Server) registerImports()      {}
func (s *Server) registerSync()         {}
func (s *Server) registerStatic()       {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && go test ./internal/api/ -count=1`
Expected: PASS (both tests). `/api/nope` 404s because no route matches and the stub static handler registers nothing.

- [ ] **Step 5: Add the `serve` subcommand**

Create `cmd/vollmint/serve.go`:

```go
package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"time"

	"github.com/vollminlab/vollmint/internal/api"
	"github.com/vollminlab/vollmint/internal/migrate"
	"github.com/vollminlab/vollmint/internal/store"
)

func runServe(args []string) error {
	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		return fmt.Errorf("DATABASE_URL is required")
	}
	addr := os.Getenv("LISTEN_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	ctx := context.Background()
	if err := migrate.Up(dbURL); err != nil {
		return fmt.Errorf("migrate: %w", err)
	}
	s, err := store.New(ctx, dbURL)
	if err != nil {
		return err
	}
	defer s.Close()

	srv := &http.Server{
		Addr:              addr,
		Handler:           api.New(s).Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}
	fmt.Printf("vollmint serve listening on %s\n", addr)
	return srv.ListenAndServe()
}
```

Modify `cmd/vollmint/main.go` — add a case to the switch (after `case "sync":`):

```go
	case "serve":
		err = runServe(os.Args[2:])
```

And update the usage string in `usage()`:

```go
	fmt.Fprintln(os.Stderr, "usage: vollmint <claim|sync|import-venmo|serve> [args]")
```

- [ ] **Step 6: Verify build + tests**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && go build ./... && go test ./internal/api/ -count=1`
Expected: build succeeds; tests PASS.

- [ ] **Step 7: Commit**

```bash
git add internal/api/server.go internal/api/server_test.go cmd/vollmint/serve.go cmd/vollmint/main.go
git commit -m "feat(api): serve subcommand, http skeleton, healthz"
```

---

## Task 2: Transactions read model — `store.ListTransactions`

**Files:**
- Create: `internal/store/query.go`
- Create: `internal/store/query_test.go`
- Note: `internal/store/testdb_test.go` already exists — reuse its `testDB(t)` helper.

- [ ] **Step 1: Write the failing test**

Create `internal/store/query_test.go`:

```go
package store

import (
	"context"
	"testing"
	"time"
)

// seedTxn inserts one account (if new) and one categorized/uncategorized txn.
func seedTxn(t *testing.T, s *Store, acct, owner, extID, posted, amount, desc string, catID *int) int64 {
	t.Helper()
	ctx := context.Background()
	if err := s.UpsertAccounts(ctx, []Account{{ID: acct, Name: acct, Org: "t", Owner: owner}}); err != nil {
		t.Fatal(err)
	}
	p, err := time.Parse("2006-01-02", posted)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.UpsertTransactions(ctx, []Txn{{
		Source: "simplefin", ExternalID: extID, AccountID: acct,
		Posted: p, Amount: amount, Description: desc, Payee: desc,
	}}); err != nil {
		t.Fatal(err)
	}
	var id int64
	if err := s.Pool.QueryRow(ctx,
		`SELECT id FROM transactions WHERE source='simplefin' AND external_id=$1`, extID).Scan(&id); err != nil {
		t.Fatal(err)
	}
	if catID != nil {
		if _, err := s.Pool.Exec(ctx,
			`UPDATE transactions SET category_id=$1 WHERE id=$2`, *catID, id); err != nil {
			t.Fatal(err)
		}
	}
	return id
}

func TestListTransactionsViewAndMonthFilter(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	seedTxn(t, s, "ally-s", "scott", "s1", "2026-07-05", "-10.00", "Coffee", nil)
	seedTxn(t, s, "ally-n", "nikki", "n1", "2026-07-06", "-20.00", "Books", nil)
	seedTxn(t, s, "ally-s", "scott", "s2", "2026-06-30", "-30.00", "OldMonth", nil)

	// household + month July → 2 rows (June excluded)
	got, err := s.ListTransactions(ctx, TxnFilter{View: "household", Month: "2026-07"})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("household/July = %d rows, want 2", len(got))
	}
	// amounts are strings with 2 decimals
	if got[0].Amount == "" || got[0].Amount[0] != '-' {
		t.Fatalf("amount not a signed decimal string: %q", got[0].Amount)
	}

	// scott view → only scott-owned
	got, err = s.ListTransactions(ctx, TxnFilter{View: "scott", Month: "2026-07"})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].EffectiveOwner != "scott" {
		t.Fatalf("scott/July = %v, want 1 scott row", got)
	}
}

func TestListTransactionsUncategorizedAndOwnerOverride(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedTxn(t, s, "joint1", "joint", "j1", "2026-07-10", "-40.00", "Dinner", nil)
	// owner_override moves a joint charge into scott's view
	if _, err := s.Pool.Exec(ctx,
		`UPDATE transactions SET owner_override='scott' WHERE id=$1`, id); err != nil {
		t.Fatal(err)
	}
	got, err := s.ListTransactions(ctx, TxnFilter{View: "scott", Month: "2026-07", Uncategorized: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].EffectiveOwner != "scott" {
		t.Fatalf("override into scott view failed: %v", got)
	}
	// joint view must NOT see it anymore (override wins)
	got, _ = s.ListTransactions(ctx, TxnFilter{View: "joint", Month: "2026-07"})
	if len(got) != 0 {
		t.Fatalf("joint view still sees overridden row: %v", got)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestListTransactions -count=1`
Expected: FAIL — `undefined: TxnFilter` / `ListTransactions`.

- [ ] **Step 3: Write minimal implementation**

Create `internal/store/query.go`:

```go
package store

import (
	"context"
	"fmt"
	"strings"
)

// TxnRow is a transaction as the API returns it. Amount is a decimal string.
type TxnRow struct {
	ID             int64   `json:"id"`
	Source         string  `json:"source"`
	AccountID      string  `json:"account_id"`
	AccountName    string  `json:"account_name"`
	Posted         string  `json:"posted"` // YYYY-MM-DD
	Amount         string  `json:"amount"`
	Description    string  `json:"description"`
	Payee          string  `json:"payee"`
	Pending        bool    `json:"pending"`
	CategoryID     *int    `json:"category_id"`
	CategoryName   *string `json:"category_name"`
	OwnerOverride  *string `json:"owner_override"`
	EffectiveOwner string  `json:"effective_owner"`
	TransferPeerID *int64  `json:"transfer_peer_id"`
}

// TxnFilter narrows a transaction listing. Zero values mean "no filter" for
// that field, except View and Month which are required by the API layer.
type TxnFilter struct {
	View          string // scott|nikki|joint|household
	Month         string // YYYY-MM ("" = no month filter)
	CategoryID    *int
	AccountID     string
	Query         string // substring match on payee/description
	Uncategorized bool
}

// ownerClause appends the effective-owner filter for a view. household → none.
// Returns the SQL fragment (may be empty) and any bind arg to append.
func ownerClause(view string, args *[]any) string {
	switch view {
	case "scott", "nikki", "joint":
		*args = append(*args, view)
		return fmt.Sprintf(" AND COALESCE(t.owner_override, a.owner) = $%d", len(*args))
	default: // household or unknown → no owner filter
		return ""
	}
}

// ListTransactions returns transactions matching the filter, newest first.
func (s *Store) ListTransactions(ctx context.Context, f TxnFilter) ([]TxnRow, error) {
	var sb strings.Builder
	args := []any{}
	sb.WriteString(`
		SELECT t.id, t.source, t.account_id, a.name, to_char(t.posted,'YYYY-MM-DD'),
		       t.amount::text, t.description, t.payee, t.pending,
		       t.category_id, c.name, t.owner_override,
		       COALESCE(t.owner_override, a.owner), t.transfer_peer_id
		FROM transactions t
		JOIN accounts a ON a.id = t.account_id
		LEFT JOIN categories c ON c.id = t.category_id
		WHERE 1=1`)
	sb.WriteString(ownerClause(f.View, &args))
	if f.Month != "" {
		args = append(args, f.Month+"-01")
		sb.WriteString(fmt.Sprintf(
			" AND t.posted >= $%d::date AND t.posted < ($%d::date + interval '1 month')", len(args), len(args)))
	}
	if f.CategoryID != nil {
		args = append(args, *f.CategoryID)
		sb.WriteString(fmt.Sprintf(" AND t.category_id = $%d", len(args)))
	}
	if f.AccountID != "" {
		args = append(args, f.AccountID)
		sb.WriteString(fmt.Sprintf(" AND t.account_id = $%d", len(args)))
	}
	if f.Uncategorized {
		sb.WriteString(" AND t.category_id IS NULL")
	}
	if f.Query != "" {
		args = append(args, "%"+f.Query+"%")
		sb.WriteString(fmt.Sprintf(" AND (t.payee ILIKE $%d OR t.description ILIKE $%d)", len(args), len(args)))
	}
	sb.WriteString(" ORDER BY t.posted DESC, t.id DESC")

	rows, err := s.Pool.Query(ctx, sb.String(), args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []TxnRow
	for rows.Next() {
		var r TxnRow
		if err := rows.Scan(&r.ID, &r.Source, &r.AccountID, &r.AccountName, &r.Posted,
			&r.Amount, &r.Description, &r.Payee, &r.Pending,
			&r.CategoryID, &r.CategoryName, &r.OwnerOverride,
			&r.EffectiveOwner, &r.TransferPeerID); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestListTransactions -count=1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/store/query.go internal/store/query_test.go
git commit -m "feat(store): ListTransactions with view/month/category/search filters"
```

---

## Task 3: `GET /api/transactions` handler

**Files:**
- Create: `internal/api/transactions.go`
- Create: `internal/api/testdb_test.go`
- Create: `internal/api/transactions_test.go`
- Modify: `internal/api/server.go` (delete the `registerTransactions` stub)

- [ ] **Step 1: Create the API test DB helper**

Create `internal/api/testdb_test.go` (mirrors `internal/store/testdb_test.go`):

```go
package api

import (
	"context"
	"os"
	"testing"

	"github.com/vollminlab/vollmint/internal/migrate"
	"github.com/vollminlab/vollmint/internal/store"
	"github.com/vollminlab/vollmint/internal/testutil"
)

func testStore(t *testing.T) *store.Store {
	t.Helper()
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		t.Fatal("TEST_DATABASE_URL not set (see plan Context section)")
	}
	testutil.SerializeDB(t, url)
	if err := migrate.Up(url); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	s, err := store.New(context.Background(), url)
	if err != nil {
		t.Fatalf("store.New: %v", err)
	}
	t.Cleanup(s.Close)
	for _, q := range []string{
		`TRUNCATE transactions, sync_runs, budgets RESTART IDENTITY CASCADE`,
		`DELETE FROM accounts WHERE id <> 'venmo'`,
		`DELETE FROM category_rules WHERE priority <> 1000`,
	} {
		if _, err := s.Pool.Exec(context.Background(), q); err != nil {
			t.Fatalf("reset (%s): %v", q, err)
		}
	}
	return s
}

// seedTxn inserts an account + one simplefin txn and returns its id.
func seedTxn(t *testing.T, s *store.Store, acct, owner, extID, posted, amount, desc string) int64 {
	t.Helper()
	ctx := context.Background()
	if err := s.UpsertAccounts(ctx, []store.Account{{ID: acct, Name: acct, Org: "t", Owner: owner}}); err != nil {
		t.Fatal(err)
	}
	p := mustDate(t, posted)
	if _, err := s.UpsertTransactions(ctx, []store.Txn{{
		Source: "simplefin", ExternalID: extID, AccountID: acct,
		Posted: p, Amount: amount, Description: desc, Payee: desc,
	}}); err != nil {
		t.Fatal(err)
	}
	var id int64
	if err := s.Pool.QueryRow(ctx,
		`SELECT id FROM transactions WHERE source='simplefin' AND external_id=$1`, extID).Scan(&id); err != nil {
		t.Fatal(err)
	}
	return id
}
```

Also create `internal/api/date_test.go` for the shared `mustDate` test helper:

```go
package api

import (
	"testing"
	"time"
)

func mustDate(t *testing.T, s string) time.Time {
	t.Helper()
	d, err := time.Parse("2006-01-02", s)
	if err != nil {
		t.Fatalf("bad date %q: %v", s, err)
	}
	return d
}
```

- [ ] **Step 2: Write the failing handler test**

Create `internal/api/transactions_test.go`:

```go
package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestGetTransactions(t *testing.T) {
	s := testStore(t)
	seedTxn(t, s, "ally-s", "scott", "s1", "2026-07-05", "-10.00", "Coffee")
	seedTxn(t, s, "ally-n", "nikki", "n1", "2026-07-06", "-20.00", "Books")
	srv := New(s)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/transactions?view=household&month=2026-07", nil)
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", rec.Code, rec.Body.String())
	}
	var body struct {
		Transactions []struct {
			ID     int64  `json:"id"`
			Amount string `json:"amount"`
		} `json:"transactions"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Transactions) != 2 {
		t.Fatalf("got %d transactions, want 2", len(body.Transactions))
	}
}

func TestGetTransactionsRejectsBadMonth() (skip) {}
```

Replace the placeholder line `func TestGetTransactionsRejectsBadMonth() (skip) {}` with:

```go
func TestGetTransactionsRejectsBadMonth(t *testing.T) {
	srv := New(testStore(t))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/transactions?view=household&month=julyish", nil)
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("bad month status = %d, want 400", rec.Code)
	}
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/api/ -run TestGetTransactions -count=1`
Expected: FAIL — no `/api/transactions` route (stub registers nothing → 404, not 200).

- [ ] **Step 4: Write the handler**

Create `internal/api/transactions.go`:

```go
package api

import (
	"net/http"
	"regexp"
	"strconv"

	"github.com/vollminlab/vollmint/internal/store"
)

var monthRe = regexp.MustCompile(`^\d{4}-\d{2}$`)

// validView reports whether v is an accepted view filter.
func validView(v string) bool {
	switch v {
	case "scott", "nikki", "joint", "household":
		return true
	}
	return false
}

func (s *Server) registerTransactions() {
	s.mux.HandleFunc("GET /api/transactions", s.handleListTransactions)
	s.mux.HandleFunc("PATCH /api/transactions/{id}", s.handlePatchTransaction)
}

func (s *Server) handleListTransactions(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	view := q.Get("view")
	if view == "" {
		view = "household"
	}
	if !validView(view) {
		writeErr(w, http.StatusBadRequest, "invalid view")
		return
	}
	month := q.Get("month")
	if month != "" && !monthRe.MatchString(month) {
		writeErr(w, http.StatusBadRequest, "month must be YYYY-MM")
		return
	}
	f := store.TxnFilter{
		View:          view,
		Month:         month,
		AccountID:     q.Get("account"),
		Query:         q.Get("q"),
		Uncategorized: q.Get("uncategorized") == "true",
	}
	if c := q.Get("category"); c != "" {
		id, err := strconv.Atoi(c)
		if err != nil {
			writeErr(w, http.StatusBadRequest, "category must be an integer")
			return
		}
		f.CategoryID = &id
	}
	rows, err := s.store.ListTransactions(r.Context(), f)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if rows == nil {
		rows = []store.TxnRow{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"transactions": rows})
}
```

The PATCH handler is implemented in Task 5; add a temporary stub method to keep the compile green (delete it in Task 5):

```go
func (s *Server) handlePatchTransaction(w http.ResponseWriter, r *http.Request) {
	writeErr(w, http.StatusNotImplemented, "not implemented")
}
```

Delete the `func (s *Server) registerTransactions() {}` stub from `server.go`.

- [ ] **Step 5: Run test to verify it passes**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/api/ -run TestGetTransactions -count=1`
Expected: PASS (both).

- [ ] **Step 6: Commit**

```bash
git add internal/api/transactions.go internal/api/transactions_test.go internal/api/testdb_test.go internal/api/date_test.go internal/api/server.go
git commit -m "feat(api): GET /api/transactions with view/month/filters"
```

---

## Task 4: `store.UpdateTransaction` (category + owner override)

**Files:**
- Modify: `internal/store/query.go`
- Modify: `internal/store/query_test.go`

- [ ] **Step 1: Write the failing test**

Append to `internal/store/query_test.go`:

```go
func TestUpdateTransaction(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedTxn(t, s, "joint1", "joint", "j1", "2026-07-10", "-40.00", "Dinner", nil)

	var diningID int
	if err := s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Dining'`).Scan(&diningID); err != nil {
		t.Fatal(err)
	}
	owner := "scott"
	if err := s.UpdateTransaction(ctx, id, TxnPatch{CategoryID: &diningID, OwnerOverride: &owner}); err != nil {
		t.Fatal(err)
	}
	rows, _ := s.ListTransactions(ctx, TxnFilter{View: "household", Month: "2026-07"})
	if len(rows) != 1 || rows[0].CategoryName == nil || *rows[0].CategoryName != "Dining" {
		t.Fatalf("category not updated: %+v", rows)
	}
	if rows[0].EffectiveOwner != "scott" {
		t.Fatalf("owner override not applied: %+v", rows[0])
	}
}

func TestUpdateTransactionClearOwnerOverride(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	id := seedTxn(t, s, "joint1", "joint", "j1", "2026-07-10", "-40.00", "Dinner", nil)
	scott := "scott"
	if err := s.UpdateTransaction(ctx, id, TxnPatch{OwnerOverride: &scott}); err != nil {
		t.Fatal(err)
	}
	// empty-string sentinel clears the override back to NULL
	empty := ""
	if err := s.UpdateTransaction(ctx, id, TxnPatch{OwnerOverride: &empty}); err != nil {
		t.Fatal(err)
	}
	rows, _ := s.ListTransactions(ctx, TxnFilter{View: "joint", Month: "2026-07"})
	if len(rows) != 1 {
		t.Fatalf("cleared override should return to joint view: %+v", rows)
	}
}

func TestUpdateTransactionNotFound(t *testing.T) {
	s := testDB(t)
	diningID := 1
	err := s.UpdateTransaction(context.Background(), 999999, TxnPatch{CategoryID: &diningID})
	if err != ErrNotFound {
		t.Fatalf("err = %v, want ErrNotFound", err)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestUpdateTransaction -count=1`
Expected: FAIL — `undefined: TxnPatch` / `UpdateTransaction` / `ErrNotFound`.

- [ ] **Step 3: Write the implementation**

Append to `internal/store/query.go` (add `"errors"` to the import block):

```go
// ErrNotFound is returned when an update targets a row that does not exist.
var ErrNotFound = errors.New("not found")

// TxnPatch is a partial update to a transaction. A nil field is left unchanged.
// For OwnerOverride, a non-nil pointer to "" clears the override to NULL;
// any other value sets it (validated by the DB CHECK constraint).
type TxnPatch struct {
	CategoryID    *int
	OwnerOverride *string
}

// UpdateTransaction applies a partial update. Returns ErrNotFound if no row
// with the given id exists. category_id and owner_override are the only
// user-editable fields (see spec API surface).
func (s *Store) UpdateTransaction(ctx context.Context, id int64, p TxnPatch) error {
	sets := []string{"updated_at=now()"}
	args := []any{}
	if p.CategoryID != nil {
		args = append(args, *p.CategoryID)
		sets = append(sets, fmt.Sprintf("category_id=$%d", len(args)))
	}
	if p.OwnerOverride != nil {
		if *p.OwnerOverride == "" {
			sets = append(sets, "owner_override=NULL")
		} else {
			args = append(args, *p.OwnerOverride)
			sets = append(sets, fmt.Sprintf("owner_override=$%d", len(args)))
		}
	}
	args = append(args, id)
	q := fmt.Sprintf("UPDATE transactions SET %s WHERE id=$%d",
		strings.Join(sets, ", "), len(args))
	tag, err := s.Pool.Exec(ctx, q, args...)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestUpdateTransaction -count=1`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add internal/store/query.go internal/store/query_test.go
git commit -m "feat(store): UpdateTransaction with owner-override clear sentinel"
```

---

## Task 5: `PATCH /api/transactions/{id}` handler

**Files:**
- Modify: `internal/api/transactions.go` (replace the PATCH stub)
- Modify: `internal/api/transactions_test.go`

- [ ] **Step 1: Write the failing test**

Append to `internal/api/transactions_test.go` (add `"strings"` and `"errors"` are not needed; uses `strings.NewReader` → add `"strings"` to imports):

```go
func TestPatchTransactionSetsCategory(t *testing.T) {
	s := testStore(t)
	id := seedTxn(t, s, "joint1", "joint", "j1", "2026-07-10", "-40.00", "Dinner")
	var diningID int
	_ = s.Pool.QueryRow(nil0(), `SELECT id FROM categories WHERE name='Dining'`).Scan(&diningID)
	srv := New(s)

	body := strings.NewReader(`{"category_id": ` + itoa(diningID) + `}`)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPatch, "/api/transactions/"+itoa64(id), body)
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rec.Code, rec.Body.String())
	}
}

func TestPatchTransactionNotFound(t *testing.T) {
	srv := New(testStore(t))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPatch, "/api/transactions/999999", strings.NewReader(`{"category_id":1}`))
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("status=%d, want 404", rec.Code)
	}
}

func TestPatchTransactionBadID(t *testing.T) {
	srv := New(testStore(t))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPatch, "/api/transactions/abc", strings.NewReader(`{}`))
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status=%d, want 400", rec.Code)
	}
}
```

Add these small helpers to the bottom of `internal/api/transactions_test.go` (avoids importing `strconv`/`context` noise inline):

```go
func itoa(i int) string    { return strconv.Itoa(i) }
func itoa64(i int64) string { return strconv.FormatInt(i, 10) }
func nil0() context.Context { return context.Background() }
```

Add `"context"` and `"strconv"` to that file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/api/ -run TestPatchTransaction -count=1`
Expected: FAIL — stub returns 501.

- [ ] **Step 3: Replace the stub with the real handler**

In `internal/api/transactions.go`, delete the `handlePatchTransaction` stub and add (also add `"encoding/json"` and `"errors"` to imports):

```go
type txnPatchBody struct {
	CategoryID    *int    `json:"category_id"`
	OwnerOverride *string `json:"owner_override"`
}

func (s *Server) handlePatchTransaction(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		writeErr(w, http.StatusBadRequest, "id must be an integer")
		return
	}
	var body txnPatchBody
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	err = s.store.UpdateTransaction(r.Context(), id, store.TxnPatch{
		CategoryID:    body.CategoryID,
		OwnerOverride: body.OwnerOverride,
	})
	if errors.Is(err, store.ErrNotFound) {
		writeErr(w, http.StatusNotFound, "transaction not found")
		return
	}
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/api/ -run TestPatchTransaction -count=1`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add internal/api/transactions.go internal/api/transactions_test.go
git commit -m "feat(api): PATCH /api/transactions/{id} category + owner override"
```

---

## Task 6: Summary report — `report.Summary` + `report.SpendByCategory`

**Files:**
- Create: `internal/report/report.go`
- Create: `internal/report/testdb_test.go`
- Create: `internal/report/report_test.go`

- [ ] **Step 1: Create the report test DB helper**

Create `internal/report/testdb_test.go` — identical body to `internal/api/testdb_test.go` from Task 3 but `package report`, and its `seedTxn` also accepts an optional category name so spend lands in a category. Use this exact file:

```go
package report

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/vollminlab/vollmint/internal/migrate"
	"github.com/vollminlab/vollmint/internal/store"
	"github.com/vollminlab/vollmint/internal/testutil"
)

func testStore(t *testing.T) *store.Store {
	t.Helper()
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		t.Fatal("TEST_DATABASE_URL not set")
	}
	testutil.SerializeDB(t, url)
	if err := migrate.Up(url); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	s, err := store.New(context.Background(), url)
	if err != nil {
		t.Fatalf("store.New: %v", err)
	}
	t.Cleanup(s.Close)
	for _, q := range []string{
		`TRUNCATE transactions, sync_runs, budgets RESTART IDENTITY CASCADE`,
		`DELETE FROM accounts WHERE id <> 'venmo'`,
		`DELETE FROM category_rules WHERE priority <> 1000`,
	} {
		if _, err := s.Pool.Exec(context.Background(), q); err != nil {
			t.Fatalf("reset (%s): %v", q, err)
		}
	}
	return s
}

// seedSpend inserts an account + a txn categorized as catName (or uncategorized
// if catName==""). amount is a signed decimal string.
func seedSpend(t *testing.T, s *store.Store, acct, owner, extID, posted, amount, catName string) {
	t.Helper()
	ctx := context.Background()
	if err := s.UpsertAccounts(ctx, []store.Account{{ID: acct, Name: acct, Org: "t", Owner: owner}}); err != nil {
		t.Fatal(err)
	}
	p, err := time.Parse("2006-01-02", posted)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.UpsertTransactions(ctx, []store.Txn{{
		Source: "simplefin", ExternalID: extID, AccountID: acct,
		Posted: p, Amount: amount, Description: extID, Payee: extID,
	}}); err != nil {
		t.Fatal(err)
	}
	if catName != "" {
		if _, err := s.Pool.Exec(ctx, `
			UPDATE transactions SET category_id=(SELECT id FROM categories WHERE name=$1)
			WHERE source='simplefin' AND external_id=$2`, catName, extID); err != nil {
			t.Fatal(err)
		}
	}
}

func setBudget(t *testing.T, s *store.Store, catName, month, amount string) {
	t.Helper()
	if _, err := s.Pool.Exec(context.Background(), `
		INSERT INTO budgets (category_id, month, amount)
		VALUES ((SELECT id FROM categories WHERE name=$1), $2::date, $3)`,
		catName, month+"-01", amount); err != nil {
		t.Fatal(err)
	}
}
```

- [ ] **Step 2: Write the failing test**

Create `internal/report/report_test.go`:

```go
package report

import (
	"context"
	"testing"
)

func TestSummaryTotals(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	// July spend + income + a vice + a transfer that must be excluded
	seedSpend(t, s, "ally-s", "scott", "sp1", "2026-07-05", "-100.00", "Groceries")
	seedSpend(t, s, "ally-s", "scott", "sp2", "2026-07-06", "-40.00", "Dining") // Dining is a vice
	seedSpend(t, s, "ally-s", "scott", "in1", "2026-07-01", "3000.00", "Paycheck")
	seedSpend(t, s, "ally-s", "scott", "tr1", "2026-07-07", "-500.00", "Transfer") // excluded
	setBudget(t, s, "Groceries", "2026-07", "120.00")

	sum, err := Summary(ctx, s, "household", "2026-07")
	if err != nil {
		t.Fatal(err)
	}
	if sum.In != "3000.00" {
		t.Errorf("In = %q, want 3000.00", sum.In)
	}
	if sum.Out != "140.00" { // 100 + 40, transfer excluded
		t.Errorf("Out = %q, want 140.00", sum.Out)
	}
	if sum.Vices != "40.00" { // Dining is_vice
		t.Errorf("Vices = %q, want 40.00", sum.Vices)
	}
	if sum.BudgetTotal != "120.00" {
		t.Errorf("BudgetTotal = %q, want 120.00", sum.BudgetTotal)
	}
}

func TestSpendByCategory(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	seedSpend(t, s, "ally-s", "scott", "g1", "2026-07-05", "-60.00", "Groceries")
	seedSpend(t, s, "ally-s", "scott", "g2", "2026-07-08", "-40.00", "Groceries")
	seedSpend(t, s, "ally-s", "scott", "d1", "2026-07-09", "-25.00", "Dining")
	setBudget(t, s, "Groceries", "2026-07", "120.00")

	rows, err := SpendByCategory(ctx, s, "household", "2026-07")
	if err != nil {
		t.Fatal(err)
	}
	// Groceries 100.00 (budget 120.00) should sort before Dining 25.00
	if len(rows) != 2 {
		t.Fatalf("got %d category rows, want 2", len(rows))
	}
	if rows[0].Category != "Groceries" || rows[0].Spent != "100.00" || rows[0].Budget != "120.00" {
		t.Errorf("row0 = %+v", rows[0])
	}
	if rows[1].Category != "Dining" || rows[1].Budget != "" {
		t.Errorf("row1 = %+v (Dining should have empty budget)", rows[1])
	}
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/report/ -count=1`
Expected: FAIL — `undefined: Summary` / `SpendByCategory`.

- [ ] **Step 4: Write the implementation**

Create `internal/report/report.go`:

```go
// Package report holds read-only aggregation queries for the vollmint API.
// All money is summed in Postgres numeric and returned as decimal strings;
// no float64 ever touches a monetary value.
package report

import (
	"context"
	"fmt"

	"github.com/vollminlab/vollmint/internal/store"
)

// ownerFilter returns the SQL fragment + arg for a view, or ("", nil) for
// household. The alias for transactions is "t" and accounts is "a".
func ownerFilter(view string) (string, []any) {
	switch view {
	case "scott", "nikki", "joint":
		return " AND COALESCE(t.owner_override, a.owner) = $2", []any{view}
	default:
		return "", nil
	}
}

// notTransfer excludes transfer rows from spend/income math.
const notTransfer = ` AND t.transfer_peer_id IS NULL
	AND (c.kind IS NULL OR c.kind <> 'transfer')`

// SummaryResult is the dashboard rollup. All amounts are decimal strings.
type SummaryResult struct {
	In          string `json:"in"`
	Out         string `json:"out"`
	Vices       string `json:"vices"`
	BudgetTotal string `json:"budget_total"`
	Month       string `json:"month"`
	View        string `json:"view"`
}

// Summary computes In/Out/Vices for the month+view and the month's total budget.
func Summary(ctx context.Context, s *store.Store, view, month string) (SummaryResult, error) {
	res := SummaryResult{In: "0.00", Out: "0.00", Vices: "0.00", BudgetTotal: "0.00", Month: month, View: view}
	own, args := ownerFilter(view)
	q := `
		SELECT
		  COALESCE(SUM(t.amount) FILTER (WHERE t.amount > 0), 0)::text,
		  COALESCE(-SUM(t.amount) FILTER (WHERE t.amount < 0), 0)::text,
		  COALESCE(-SUM(t.amount) FILTER (WHERE t.amount < 0 AND c.is_vice), 0)::text
		FROM transactions t
		JOIN accounts a ON a.id = t.account_id
		LEFT JOIN categories c ON c.id = t.category_id
		WHERE t.posted >= $1::date AND t.posted < ($1::date + interval '1 month')` +
		notTransfer + own
	full := append([]any{month + "-01"}, args...)
	if err := s.Pool.QueryRow(ctx, q, full...).Scan(&res.In, &res.Out, &res.Vices); err != nil {
		return res, fmt.Errorf("summary totals: %w", err)
	}
	// Total budget for the month (view-independent — budgets are household).
	if err := s.Pool.QueryRow(ctx, `
		SELECT COALESCE(SUM(amount), 0)::text FROM budgets WHERE month = $1::date`,
		month+"-01").Scan(&res.BudgetTotal); err != nil {
		return res, fmt.Errorf("summary budget: %w", err)
	}
	return res, nil
}

// CategorySpend is one row of the spend-by-category report.
type CategorySpend struct {
	CategoryID int    `json:"category_id"`
	Category   string `json:"category"`
	Spent      string `json:"spent"`
	Budget     string `json:"budget"` // "" when no budget set
	IsVice     bool   `json:"is_vice"`
}

// SpendByCategory returns spend per category for the month+view, descending by
// spent, with each category's budget (if any) for that month.
func SpendByCategory(ctx context.Context, s *store.Store, view, month string) ([]CategorySpend, error) {
	own, args := ownerFilter(view)
	q := `
		SELECT c.id, c.name, (-SUM(t.amount))::text, c.is_vice,
		       COALESCE(b.amount::text, '')
		FROM transactions t
		JOIN accounts a ON a.id = t.account_id
		JOIN categories c ON c.id = t.category_id
		LEFT JOIN budgets b ON b.category_id = c.id AND b.month = $1::date
		WHERE t.amount < 0
		  AND t.posted >= $1::date AND t.posted < ($1::date + interval '1 month')
		  AND t.transfer_peer_id IS NULL AND c.kind <> 'transfer'` + own + `
		GROUP BY c.id, c.name, c.is_vice, b.amount
		ORDER BY (-SUM(t.amount)) DESC, c.name`
	full := append([]any{month + "-01"}, args...)
	rows, err := s.Pool.Query(ctx, q, full...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []CategorySpend
	for rows.Next() {
		var cs CategorySpend
		if err := rows.Scan(&cs.CategoryID, &cs.Category, &cs.Spent, &cs.IsVice, &cs.Budget); err != nil {
			return nil, err
		}
		out = append(out, cs)
	}
	return out, rows.Err()
}
```

Note the arg indexing convention: `$1` is always the month; `$2` (if present) is the view owner. `ownerFilter` hardcodes `$2`, so callers must pass month first, view second — which `Summary`/`SpendByCategory` do via `append([]any{month...}, args...)`.

- [ ] **Step 5: Run test to verify it passes**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/report/ -count=1`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add internal/report/report.go internal/report/report_test.go internal/report/testdb_test.go
git commit -m "feat(report): Summary + SpendByCategory aggregations (decimal strings)"
```

---

## Task 7: `GET /api/summary` handler

**Files:**
- Create: `internal/api/summary.go`
- Create: `internal/api/summary_test.go`
- Modify: `internal/api/server.go` (delete `registerSummary` stub)

- [ ] **Step 1: Write the failing test**

Create `internal/api/summary_test.go`:

```go
package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestGetSummary(t *testing.T) {
	s := testStore(t)
	seedTxn(t, s, "ally-s", "scott", "sp1", "2026-07-05", "-100.00", "Groceries")
	// categorize it so spend-by-category has a row
	_, _ = s.Pool.Exec(nil0(),
		`UPDATE transactions SET category_id=(SELECT id FROM categories WHERE name='Groceries') WHERE external_id='sp1'`)
	srv := New(s)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/summary?view=household&month=2026-07", nil)
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rec.Code, rec.Body.String())
	}
	var body struct {
		Summary struct {
			Out string `json:"out"`
		} `json:"summary"`
		Categories []struct {
			Category string `json:"category"`
			Spent    string `json:"spent"`
		} `json:"categories"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.Summary.Out != "100.00" {
		t.Errorf("summary.out = %q, want 100.00", body.Summary.Out)
	}
	if len(body.Categories) != 1 || body.Categories[0].Category != "Groceries" {
		t.Errorf("categories = %+v", body.Categories)
	}
}

func TestGetSummaryRequiresMonth(t *testing.T) {
	srv := New(testStore(t))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/summary?view=household", nil)
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status=%d, want 400 (month required)", rec.Code)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/api/ -run TestGetSummary -count=1`
Expected: FAIL — no route (404).

- [ ] **Step 3: Write the handler**

Create `internal/api/summary.go`:

```go
package api

import (
	"net/http"

	"github.com/vollminlab/vollmint/internal/report"
)

func (s *Server) registerSummary() {
	s.mux.HandleFunc("GET /api/summary", s.handleSummary)
}

// requireViewMonth parses and validates the shared view+month query params.
// It writes a 400 and returns ok=false on any problem.
func requireViewMonth(w http.ResponseWriter, r *http.Request) (view, month string, ok bool) {
	view = r.URL.Query().Get("view")
	if view == "" {
		view = "household"
	}
	if !validView(view) {
		writeErr(w, http.StatusBadRequest, "invalid view")
		return "", "", false
	}
	month = r.URL.Query().Get("month")
	if !monthRe.MatchString(month) {
		writeErr(w, http.StatusBadRequest, "month is required and must be YYYY-MM")
		return "", "", false
	}
	return view, month, true
}

func (s *Server) handleSummary(w http.ResponseWriter, r *http.Request) {
	view, month, ok := requireViewMonth(w, r)
	if !ok {
		return
	}
	sum, err := report.Summary(r.Context(), s.store, view, month)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	cats, err := report.SpendByCategory(r.Context(), s.store, view, month)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if cats == nil {
		cats = []report.CategorySpend{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"summary": sum, "categories": cats})
}
```

Delete the `registerSummary` stub from `server.go`.

- [ ] **Step 4: Run test to verify it passes**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/api/ -run TestGetSummary -count=1`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add internal/api/summary.go internal/api/summary_test.go internal/api/server.go
git commit -m "feat(api): GET /api/summary (rollups + spend-by-category)"
```

---

## Task 8: Categories — store CRUD + `GET/POST/PATCH /api/categories`

**Files:**
- Modify: `internal/store/query.go`, `internal/store/query_test.go`
- Create: `internal/api/categories.go`, `internal/api/categories_test.go`
- Modify: `internal/api/server.go` (delete `registerCategories` stub)

- [ ] **Step 1: Write the failing store test**

Append to `internal/store/query_test.go`:

```go
func TestCategoryCRUD(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	// list includes seeds
	cats, err := s.ListCategories(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(cats) == 0 {
		t.Fatal("expected seed categories")
	}
	// create
	id, err := s.CreateCategory(ctx, "Pets", "spend", false)
	if err != nil {
		t.Fatal(err)
	}
	// update
	if err := s.UpdateCategory(ctx, id, CategoryPatch{IsVice: boolp(true)}); err != nil {
		t.Fatal(err)
	}
	cats, _ = s.ListCategories(ctx)
	var found bool
	for _, c := range cats {
		if c.ID == id && c.Name == "Pets" && c.IsVice {
			found = true
		}
	}
	if !found {
		t.Fatal("updated Pets category not found or is_vice not set")
	}
}

func boolp(b bool) *bool { return &b }
```

- [ ] **Step 2: Run to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestCategoryCRUD -count=1`
Expected: FAIL — undefined `ListCategories`/`CreateCategory`/`UpdateCategory`/`CategoryPatch`.

- [ ] **Step 3: Implement in `internal/store/query.go`**

Append:

```go
// Category is a spending category.
type Category struct {
	ID       int    `json:"id"`
	Name     string `json:"name"`
	ParentID *int   `json:"parent_id"`
	Kind     string `json:"kind"`
	IsVice   bool   `json:"is_vice"`
}

func (s *Store) ListCategories(ctx context.Context) ([]Category, error) {
	rows, err := s.Pool.Query(ctx,
		`SELECT id, name, parent_id, kind, is_vice FROM categories ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Category
	for rows.Next() {
		var c Category
		if err := rows.Scan(&c.ID, &c.Name, &c.ParentID, &c.Kind, &c.IsVice); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// CreateCategory inserts a category and returns its id. kind must be one of
// spend|income|transfer|savings (enforced by the DB CHECK).
func (s *Store) CreateCategory(ctx context.Context, name, kind string, isVice bool) (int, error) {
	var id int
	err := s.Pool.QueryRow(ctx,
		`INSERT INTO categories (name, kind, is_vice) VALUES ($1,$2,$3) RETURNING id`,
		name, kind, isVice).Scan(&id)
	return id, err
}

// CategoryPatch is a partial category update; nil fields are unchanged.
type CategoryPatch struct {
	Name   *string
	Kind   *string
	IsVice *bool
}

func (s *Store) UpdateCategory(ctx context.Context, id int, p CategoryPatch) error {
	sets := []string{}
	args := []any{}
	if p.Name != nil {
		args = append(args, *p.Name)
		sets = append(sets, fmt.Sprintf("name=$%d", len(args)))
	}
	if p.Kind != nil {
		args = append(args, *p.Kind)
		sets = append(sets, fmt.Sprintf("kind=$%d", len(args)))
	}
	if p.IsVice != nil {
		args = append(args, *p.IsVice)
		sets = append(sets, fmt.Sprintf("is_vice=$%d", len(args)))
	}
	if len(sets) == 0 {
		return nil
	}
	args = append(args, id)
	tag, err := s.Pool.Exec(ctx,
		fmt.Sprintf("UPDATE categories SET %s WHERE id=$%d", strings.Join(sets, ", "), len(args)), args...)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}
```

- [ ] **Step 4: Run store test to verify it passes**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestCategoryCRUD -count=1`
Expected: PASS.

- [ ] **Step 5: Write the API test**

Create `internal/api/categories_test.go`:

```go
package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestListCategories(t *testing.T) {
	srv := New(testStore(t))
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/categories", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d", rec.Code)
	}
	var body struct {
		Categories []struct{ Name string `json:"name"` } `json:"categories"`
	}
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if len(body.Categories) == 0 {
		t.Fatal("want seed categories")
	}
}

func TestCreateAndPatchCategory(t *testing.T) {
	srv := New(testStore(t))
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/categories",
		strings.NewReader(`{"name":"Pets","kind":"spend","is_vice":false}`)))
	if rec.Code != http.StatusCreated {
		t.Fatalf("create status=%d body=%s", rec.Code, rec.Body.String())
	}
	var created struct{ ID int `json:"id"` }
	_ = json.Unmarshal(rec.Body.Bytes(), &created)
	if created.ID == 0 {
		t.Fatal("no id returned")
	}
	rec = httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodPatch, "/api/categories/"+itoa(created.ID),
		strings.NewReader(`{"is_vice":true}`)))
	if rec.Code != http.StatusOK {
		t.Fatalf("patch status=%d", rec.Code)
	}
}

func TestCreateCategoryBadKind(t *testing.T) {
	srv := New(testStore(t))
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/categories",
		strings.NewReader(`{"name":"X","kind":"bogus"}`)))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status=%d, want 400", rec.Code)
	}
}
```

- [ ] **Step 6: Write the API handler**

Create `internal/api/categories.go`:

```go
package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"

	"github.com/vollminlab/vollmint/internal/store"
)

func (s *Server) registerCategories() {
	s.mux.HandleFunc("GET /api/categories", s.handleListCategories)
	s.mux.HandleFunc("POST /api/categories", s.handleCreateCategory)
	s.mux.HandleFunc("PATCH /api/categories/{id}", s.handlePatchCategory)
}

func validKind(k string) bool {
	switch k {
	case "spend", "income", "transfer", "savings":
		return true
	}
	return false
}

func (s *Server) handleListCategories(w http.ResponseWriter, r *http.Request) {
	cats, err := s.store.ListCategories(r.Context())
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if cats == nil {
		cats = []store.Category{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"categories": cats})
}

func (s *Server) handleCreateCategory(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name   string `json:"name"`
		Kind   string `json:"kind"`
		IsVice bool   `json:"is_vice"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if body.Name == "" {
		writeErr(w, http.StatusBadRequest, "name is required")
		return
	}
	if body.Kind == "" {
		body.Kind = "spend"
	}
	if !validKind(body.Kind) {
		writeErr(w, http.StatusBadRequest, "kind must be spend|income|transfer|savings")
		return
	}
	id, err := s.store.CreateCategory(r.Context(), body.Name, body.Kind, body.IsVice)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"id": id})
}

func (s *Server) handlePatchCategory(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.Atoi(r.PathValue("id"))
	if err != nil {
		writeErr(w, http.StatusBadRequest, "id must be an integer")
		return
	}
	var body struct {
		Name   *string `json:"name"`
		Kind   *string `json:"kind"`
		IsVice *bool   `json:"is_vice"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if body.Kind != nil && !validKind(*body.Kind) {
		writeErr(w, http.StatusBadRequest, "kind must be spend|income|transfer|savings")
		return
	}
	err = s.store.UpdateCategory(r.Context(), id, store.CategoryPatch{
		Name: body.Name, Kind: body.Kind, IsVice: body.IsVice,
	})
	if errors.Is(err, store.ErrNotFound) {
		writeErr(w, http.StatusNotFound, "category not found")
		return
	}
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
```

Delete the `registerCategories` stub from `server.go`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ ./internal/api/ -count=1`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add internal/store/query.go internal/store/query_test.go internal/api/categories.go internal/api/categories_test.go internal/api/server.go
git commit -m "feat(api): categories CRUD (list/create/patch)"
```

---

## Task 9: Rules — store CRUD + `GET/POST/DELETE /api/rules` (re-run over history)

**Files:**
- Modify: `internal/store/query.go`, `internal/store/query_test.go`
- Create: `internal/api/rules.go`, `internal/api/rules_test.go`
- Modify: `internal/api/server.go` (delete `registerRules` stub)

- [ ] **Step 1: Write the failing store test**

Append to `internal/store/query_test.go`:

```go
func TestRuleCRUD(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	var diningID int
	_ = s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Dining'`).Scan(&diningID)

	id, err := s.CreateRule(ctx, 10, "substring", "CHIPOTLE", diningID)
	if err != nil {
		t.Fatal(err)
	}
	rules, err := s.ListRules(ctx)
	if err != nil {
		t.Fatal(err)
	}
	var found bool
	for _, r := range rules {
		if r.ID == id && r.Pattern == "CHIPOTLE" && r.CategoryID == diningID {
			found = true
		}
	}
	if !found {
		t.Fatal("created rule not listed")
	}
	if err := s.DeleteRule(ctx, id); err != nil {
		t.Fatal(err)
	}
	if err := s.DeleteRule(ctx, id); err != ErrNotFound {
		t.Fatalf("second delete = %v, want ErrNotFound", err)
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestRuleCRUD -count=1`
Expected: FAIL — undefined symbols.

- [ ] **Step 3: Implement in `internal/store/query.go`**

Append:

```go
// Rule is a payee→category matcher.
type Rule struct {
	ID         int    `json:"id"`
	Priority   int    `json:"priority"`
	MatchType  string `json:"match_type"`
	Pattern    string `json:"pattern"`
	CategoryID int    `json:"category_id"`
}

func (s *Store) ListRules(ctx context.Context) ([]Rule, error) {
	rows, err := s.Pool.Query(ctx,
		`SELECT id, priority, match_type, pattern, category_id FROM category_rules ORDER BY priority, id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Rule
	for rows.Next() {
		var r Rule
		if err := rows.Scan(&r.ID, &r.Priority, &r.MatchType, &r.Pattern, &r.CategoryID); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (s *Store) CreateRule(ctx context.Context, priority int, matchType, pattern string, categoryID int) (int, error) {
	var id int
	err := s.Pool.QueryRow(ctx,
		`INSERT INTO category_rules (priority, match_type, pattern, category_id)
		 VALUES ($1,$2,$3,$4) RETURNING id`,
		priority, matchType, pattern, categoryID).Scan(&id)
	return id, err
}

func (s *Store) DeleteRule(ctx context.Context, id int) error {
	tag, err := s.Pool.Exec(ctx, `DELETE FROM category_rules WHERE id=$1`, id)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}
```

- [ ] **Step 4: Run store test**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestRuleCRUD -count=1`
Expected: PASS.

- [ ] **Step 5: Write the API test**

Create `internal/api/rules_test.go`:

```go
package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCreateRuleAppliesToHistory(t *testing.T) {
	s := testStore(t)
	// an uncategorized CHIPOTLE charge already in history
	seedTxn(t, s, "ally-s", "scott", "c1", "2026-07-05", "-12.00", "CHIPOTLE 4021")
	srv := New(s)

	var diningID int
	_ = s.Pool.QueryRow(context.Background(), `SELECT id FROM categories WHERE name='Dining'`).Scan(&diningID)

	rec := httptest.NewRecorder()
	body := `{"priority":10,"match_type":"substring","pattern":"CHIPOTLE","category_id":` + itoa(diningID) + `}`
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/rules", strings.NewReader(body)))
	if rec.Code != http.StatusCreated {
		t.Fatalf("status=%d body=%s", rec.Code, rec.Body.String())
	}
	var created struct {
		ID         int `json:"id"`
		Recategorized int `json:"recategorized"`
	}
	_ = json.Unmarshal(rec.Body.Bytes(), &created)
	if created.Recategorized != 1 {
		t.Fatalf("recategorized=%d, want 1 (rule should re-run over history)", created.Recategorized)
	}
	// the historical charge is now Dining
	var name string
	_ = s.Pool.QueryRow(context.Background(),
		`SELECT c.name FROM transactions t JOIN categories c ON c.id=t.category_id WHERE t.external_id='c1'`).Scan(&name)
	if name != "Dining" {
		t.Fatalf("charge category=%q, want Dining", name)
	}
}

func TestDeleteRule(t *testing.T) {
	s := testStore(t)
	srv := New(s)
	var diningID int
	_ = s.Pool.QueryRow(context.Background(), `SELECT id FROM categories WHERE name='Dining'`).Scan(&diningID)
	id, _ := s.CreateRule(context.Background(), 10, "substring", "X", diningID)

	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodDelete, "/api/rules/"+itoa(id), nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("delete status=%d", rec.Code)
	}
}
```

- [ ] **Step 6: Write the API handler**

Create `internal/api/rules.go` (note: on create it calls `ingest.ApplyRules` to re-run over history, per spec "created from the UI … re-runnable over history"):

```go
package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"

	"github.com/vollminlab/vollmint/internal/ingest"
	"github.com/vollminlab/vollmint/internal/store"
)

func (s *Server) registerRules() {
	s.mux.HandleFunc("GET /api/rules", s.handleListRules)
	s.mux.HandleFunc("POST /api/rules", s.handleCreateRule)
	s.mux.HandleFunc("DELETE /api/rules/{id}", s.handleDeleteRule)
}

func (s *Server) handleListRules(w http.ResponseWriter, r *http.Request) {
	rules, err := s.store.ListRules(r.Context())
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if rules == nil {
		rules = []store.Rule{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"rules": rules})
}

func (s *Server) handleCreateRule(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Priority   int    `json:"priority"`
		MatchType  string `json:"match_type"`
		Pattern    string `json:"pattern"`
		CategoryID int    `json:"category_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if body.Pattern == "" || body.CategoryID == 0 {
		writeErr(w, http.StatusBadRequest, "pattern and category_id are required")
		return
	}
	if body.MatchType == "" {
		body.MatchType = "substring"
	}
	if body.MatchType != "substring" && body.MatchType != "regex" {
		writeErr(w, http.StatusBadRequest, "match_type must be substring|regex")
		return
	}
	id, err := s.store.CreateRule(r.Context(), body.Priority, body.MatchType, body.Pattern, body.CategoryID)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	// Re-run rules over uncategorized history so the new rule takes effect now.
	n, err := ingest.ApplyRules(r.Context(), s.store)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "rule created but re-apply failed: "+err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"id": id, "recategorized": n})
}

func (s *Server) handleDeleteRule(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.Atoi(r.PathValue("id"))
	if err != nil {
		writeErr(w, http.StatusBadRequest, "id must be an integer")
		return
	}
	err = s.store.DeleteRule(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) {
		writeErr(w, http.StatusNotFound, "rule not found")
		return
	}
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
```

Delete the `registerRules` stub from `server.go`.

- [ ] **Step 7: Run tests**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ ./internal/api/ -count=1`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add internal/store/query.go internal/store/query_test.go internal/api/rules.go internal/api/rules_test.go internal/api/server.go
git commit -m "feat(api): rules CRUD; create re-applies over history"
```

---

## Task 10: Budgets — store get/put + `GET/PUT /api/budgets?month=`

**Files:**
- Modify: `internal/store/query.go`, `internal/store/query_test.go`
- Create: `internal/api/budgets.go`, `internal/api/budgets_test.go`
- Modify: `internal/api/server.go` (delete `registerBudgets` stub)

- [ ] **Step 1: Write the failing store test**

Append to `internal/store/query_test.go`:

```go
func TestBudgetGetPut(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	var groceriesID int
	_ = s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Groceries'`).Scan(&groceriesID)

	// PUT replaces the whole month's budget set
	if err := s.PutBudgets(ctx, "2026-07", []BudgetItem{{CategoryID: groceriesID, Amount: "120.00"}}); err != nil {
		t.Fatal(err)
	}
	got, err := s.GetBudgets(ctx, "2026-07")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Amount != "120.00" {
		t.Fatalf("budgets = %+v", got)
	}
	// PUT again with empty list clears the month
	if err := s.PutBudgets(ctx, "2026-07", nil); err != nil {
		t.Fatal(err)
	}
	got, _ = s.GetBudgets(ctx, "2026-07")
	if len(got) != 0 {
		t.Fatalf("budgets not cleared: %+v", got)
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestBudgetGetPut -count=1`
Expected: FAIL — undefined `BudgetItem`/`PutBudgets`/`GetBudgets`.

- [ ] **Step 3: Implement in `internal/store/query.go`**

Append:

```go
// BudgetItem is a single category's budget for a month. Amount is a decimal string.
type BudgetItem struct {
	CategoryID   int    `json:"category_id"`
	CategoryName string `json:"category_name"`
	Amount       string `json:"amount"`
}

func (s *Store) GetBudgets(ctx context.Context, month string) ([]BudgetItem, error) {
	rows, err := s.Pool.Query(ctx, `
		SELECT b.category_id, c.name, b.amount::text
		FROM budgets b JOIN categories c ON c.id = b.category_id
		WHERE b.month = $1::date ORDER BY c.name`, month+"-01")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []BudgetItem
	for rows.Next() {
		var b BudgetItem
		if err := rows.Scan(&b.CategoryID, &b.CategoryName, &b.Amount); err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, rows.Err()
}

// PutBudgets replaces the entire budget set for a month (delete-then-insert in
// one transaction). Amounts are validated as decimal strings.
func (s *Store) PutBudgets(ctx context.Context, month string, items []BudgetItem) error {
	for _, it := range items {
		if !amountRe.MatchString(it.Amount) {
			return fmt.Errorf("budget for category %d: bad amount %q", it.CategoryID, it.Amount)
		}
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, `DELETE FROM budgets WHERE month = $1::date`, month+"-01"); err != nil {
		return err
	}
	for _, it := range items {
		if _, err := tx.Exec(ctx,
			`INSERT INTO budgets (category_id, month, amount) VALUES ($1, $2::date, $3)`,
			it.CategoryID, month+"-01", it.Amount); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}
```

- [ ] **Step 4: Run store test**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestBudgetGetPut -count=1`
Expected: PASS.

- [ ] **Step 5: Write the API test**

Create `internal/api/budgets_test.go`:

```go
package api

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestPutAndGetBudgets(t *testing.T) {
	s := testStore(t)
	srv := New(s)
	var groceriesID int
	_ = s.Pool.QueryRow(context.Background(), `SELECT id FROM categories WHERE name='Groceries'`).Scan(&groceriesID)

	body := `{"budgets":[{"category_id":` + itoa(groceriesID) + `,"amount":"120.00"}]}`
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodPut, "/api/budgets?month=2026-07", strings.NewReader(body)))
	if rec.Code != http.StatusOK {
		t.Fatalf("put status=%d body=%s", rec.Code, rec.Body.String())
	}
	rec = httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/budgets?month=2026-07", nil))
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "120.00") {
		t.Fatalf("get status=%d body=%s", rec.Code, rec.Body.String())
	}
}

func TestPutBudgetsBadAmount(t *testing.T) {
	srv := New(testStore(t))
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodPut, "/api/budgets?month=2026-07",
		strings.NewReader(`{"budgets":[{"category_id":1,"amount":"12.3.4"}]}`)))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status=%d, want 400", rec.Code)
	}
}
```

- [ ] **Step 6: Write the API handler**

Create `internal/api/budgets.go`:

```go
package api

import (
	"encoding/json"
	"net/http"

	"github.com/vollminlab/vollmint/internal/store"
)

func (s *Server) registerBudgets() {
	s.mux.HandleFunc("GET /api/budgets", s.handleGetBudgets)
	s.mux.HandleFunc("PUT /api/budgets", s.handlePutBudgets)
}

func (s *Server) handleGetBudgets(w http.ResponseWriter, r *http.Request) {
	month := r.URL.Query().Get("month")
	if !monthRe.MatchString(month) {
		writeErr(w, http.StatusBadRequest, "month is required and must be YYYY-MM")
		return
	}
	items, err := s.store.GetBudgets(r.Context(), month)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if items == nil {
		items = []store.BudgetItem{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"budgets": items})
}

func (s *Server) handlePutBudgets(w http.ResponseWriter, r *http.Request) {
	month := r.URL.Query().Get("month")
	if !monthRe.MatchString(month) {
		writeErr(w, http.StatusBadRequest, "month is required and must be YYYY-MM")
		return
	}
	var body struct {
		Budgets []store.BudgetItem `json:"budgets"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if err := s.store.PutBudgets(r.Context(), month, body.Budgets); err != nil {
		// bad-amount validation error → 400; anything else → 500
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
```

Note: `PutBudgets` returns a validation error for a bad amount, which we surface as 400. A genuine DB failure would also map to 400 here — acceptable for v1 since the only expected client is our own SPA; revisit if a distinct 500 is ever needed.

Delete the `registerBudgets` stub from `server.go`.

- [ ] **Step 7: Run tests**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ ./internal/api/ -count=1`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add internal/store/query.go internal/store/query_test.go internal/api/budgets.go internal/api/budgets_test.go internal/api/server.go
git commit -m "feat(api): budgets GET/PUT per month (whole-month replace)"
```

---

## Task 11: Recurring report + `GET /api/recurring`

**Files:**
- Modify: `internal/report/report.go`, `internal/report/report_test.go`
- Create: `internal/api/recurring.go`, `internal/api/recurring_test.go`
- Modify: `internal/api/server.go` (delete `registerRecurring` stub)

- [ ] **Step 1: Write the failing report test**

Append to `internal/report/report_test.go`:

```go
func TestRecurring(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	// Netflix charged 3 distinct months → recurring
	seedSpend(t, s, "ally-s", "scott", "nf1", "2026-05-10", "-15.99", "Subscriptions")
	seedSpend(t, s, "ally-s", "scott", "nf2", "2026-06-10", "-15.99", "Subscriptions")
	seedSpend(t, s, "ally-s", "scott", "nf3", "2026-07-10", "-15.99", "Subscriptions")
	// set the payee explicitly so grouping keys on it
	_, _ = s.Pool.Exec(ctx, `UPDATE transactions SET payee='NETFLIX' WHERE external_id IN ('nf1','nf2','nf3')`)
	// A one-off purchase must NOT be flagged recurring
	seedSpend(t, s, "ally-s", "scott", "one", "2026-07-11", "-80.00", "Shopping")
	_, _ = s.Pool.Exec(ctx, `UPDATE transactions SET payee='ONE OFF STORE' WHERE external_id='one'`)

	rows, err := Recurring(ctx, s, "household", "2026-07")
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("got %d recurring, want 1: %+v", len(rows), rows)
	}
	if rows[0].Payee != "NETFLIX" || rows[0].AvgAmount != "15.99" {
		t.Errorf("recurring row = %+v", rows[0])
	}
	if rows[0].Months < 3 {
		t.Errorf("months = %d, want >=3", rows[0].Months)
	}
}

func TestRecurringFlagsNew(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	// A subscription whose first charge is in the current month → is_new
	seedSpend(t, s, "ally-s", "scott", "hbo1", "2026-07-01", "-10.00", "Subscriptions")
	seedSpend(t, s, "ally-s", "scott", "hbo2", "2026-07-15", "-10.00", "Subscriptions")
	seedSpend(t, s, "ally-s", "scott", "hbo3", "2026-07-28", "-10.00", "Subscriptions")
	_, _ = s.Pool.Exec(ctx, `UPDATE transactions SET payee='HBO MAX' WHERE external_id IN ('hbo1','hbo2','hbo3')`)

	rows, _ := Recurring(ctx, s, "household", "2026-07")
	if len(rows) != 1 || !rows[0].IsNew {
		t.Fatalf("want 1 recurring flagged new, got %+v", rows)
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/report/ -run TestRecurring -count=1`
Expected: FAIL — `undefined: Recurring`.

- [ ] **Step 3: Implement in `internal/report/report.go`**

Append. The heuristic: a payee is recurring if it has spend transactions in **≥3 distinct months** anywhere in history. `is_new` = the payee's first-ever charge falls within the selected month. `avg_amount` is the mean magnitude, returned as a 2-decimal string.

```go
// RecurringItem is a detected recurring charge.
type RecurringItem struct {
	Payee     string `json:"payee"`
	Count     int    `json:"count"`
	Months    int    `json:"months"`
	AvgAmount string `json:"avg_amount"`
	LastSeen  string `json:"last_seen"`
	FirstSeen string `json:"first_seen"`
	IsNew     bool   `json:"is_new"`
}

// Recurring detects recurring charges: payees with spend in >=3 distinct
// months. is_new flags payees whose first charge is within the given month.
// view filters by effective owner; the month only affects the is_new flag and
// is NOT a spend filter (recurrence is judged across all history).
func Recurring(ctx context.Context, s *store.Store, view, month string) ([]RecurringItem, error) {
	own, args := ownerFilter(view)
	q := `
		WITH spend AS (
		  SELECT t.payee, -t.amount AS mag, t.posted,
		         date_trunc('month', t.posted) AS m
		  FROM transactions t
		  JOIN accounts a ON a.id = t.account_id
		  LEFT JOIN categories c ON c.id = t.category_id
		  WHERE t.amount < 0 AND t.payee <> ''
		    AND t.transfer_peer_id IS NULL
		    AND (c.kind IS NULL OR c.kind <> 'transfer')` + own + `
		)
		SELECT payee, count(*)::int, count(DISTINCT m)::int,
		       round(avg(mag), 2)::text,
		       to_char(max(posted),'YYYY-MM-DD'),
		       to_char(min(posted),'YYYY-MM-DD'),
		       (min(posted) >= $1::date AND min(posted) < ($1::date + interval '1 month')) AS is_new
		FROM spend
		GROUP BY payee
		HAVING count(DISTINCT m) >= 3
		ORDER BY avg(mag) DESC, payee`
	full := append([]any{month + "-01"}, args...)
	rows, err := s.Pool.Query(ctx, q, full...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []RecurringItem
	for rows.Next() {
		var it RecurringItem
		if err := rows.Scan(&it.Payee, &it.Count, &it.Months, &it.AvgAmount,
			&it.LastSeen, &it.FirstSeen, &it.IsNew); err != nil {
			return nil, err
		}
		out = append(out, it)
	}
	return out, rows.Err()
}
```

Note: `ownerFilter` uses `$2`, and here `$1` is the month — consistent with Task 6's convention (month first, view second). The `own` fragment references `t`/`a` aliases which exist in the `spend` CTE.

- [ ] **Step 4: Run report test**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/report/ -count=1`
Expected: PASS.

- [ ] **Step 5: Write the API test**

Create `internal/api/recurring_test.go`:

```go
package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGetRecurring(t *testing.T) {
	s := testStore(t)
	for i, ext := range []string{"nf1", "nf2", "nf3"} {
		_ = i
		seedTxn(t, s, "ally-s", "scott", ext, []string{"2026-05-10", "2026-06-10", "2026-07-10"}[strings.Index("012", itoa(i))], "-15.99", "NETFLIX")
	}
	srv := New(s)
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/recurring?view=household&month=2026-07", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), "NETFLIX") {
		t.Fatalf("recurring body missing NETFLIX: %s", rec.Body.String())
	}
}
```

Note: the index gymnastics above are fragile — replace the loop with three explicit `seedTxn` calls for clarity:

```go
	seedTxn(t, s, "ally-s", "scott", "nf1", "2026-05-10", "-15.99", "NETFLIX")
	seedTxn(t, s, "ally-s", "scott", "nf2", "2026-06-10", "-15.99", "NETFLIX")
	seedTxn(t, s, "ally-s", "scott", "nf3", "2026-07-10", "-15.99", "NETFLIX")
```

Use those three lines and delete the `for` loop.

- [ ] **Step 6: Write the API handler**

Create `internal/api/recurring.go`:

```go
package api

import (
	"net/http"

	"github.com/vollminlab/vollmint/internal/report"
)

func (s *Server) registerRecurring() {
	s.mux.HandleFunc("GET /api/recurring", s.handleRecurring)
}

func (s *Server) handleRecurring(w http.ResponseWriter, r *http.Request) {
	view, month, ok := requireViewMonth(w, r)
	if !ok {
		return
	}
	items, err := report.Recurring(r.Context(), s.store, view, month)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if items == nil {
		items = []report.RecurringItem{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"recurring": items})
}
```

Delete the `registerRecurring` stub from `server.go`.

- [ ] **Step 7: Run tests**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/report/ ./internal/api/ -count=1`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add internal/report/report.go internal/report/report_test.go internal/api/recurring.go internal/api/recurring_test.go internal/api/server.go
git commit -m "feat(api): recurring-charge detection + GET /api/recurring"
```

---

## Task 12: Venmo CSV upload + sync status endpoints

**Files:**
- Modify: `internal/store/query.go`, `internal/store/query_test.go` (add `SyncStatus`)
- Create: `internal/api/imports.go`, `internal/api/imports_test.go`
- Create: `internal/api/sync.go`, `internal/api/sync_test.go`
- Modify: `internal/api/server.go` (delete `registerImports` + `registerSync` stubs)

- [ ] **Step 1: Write the failing store test for SyncStatus**

Append to `internal/store/query_test.go`:

```go
func TestSyncStatus(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	_, err := s.Pool.Exec(ctx, `
		INSERT INTO sync_runs (kind, status, rows_upserted, detail, finished)
		VALUES ('simplefin','ok',5,'',now()), ('venmo_csv','failed',0,'bad header',now())`)
	if err != nil {
		t.Fatal(err)
	}
	runs, err := s.SyncStatus(ctx, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(runs) != 2 {
		t.Fatalf("got %d runs, want 2", len(runs))
	}
	// newest first; both just inserted, so just check fields are populated
	if runs[0].Kind == "" || runs[0].Status == "" {
		t.Fatalf("run not populated: %+v", runs[0])
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestSyncStatus -count=1`
Expected: FAIL — undefined `SyncStatus`.

- [ ] **Step 3: Implement `SyncStatus` in `internal/store/query.go`**

Append:

```go
// SyncRun is one ingestion run (sync or CSV import) for the status endpoint.
type SyncRun struct {
	ID           int64   `json:"id"`
	Kind         string  `json:"kind"`
	Started      string  `json:"started"`
	Finished     *string `json:"finished"`
	Status       string  `json:"status"`
	RowsUpserted int     `json:"rows_upserted"`
	Detail       string  `json:"detail"`
}

// SyncStatus returns the most recent ingestion runs, newest first.
func (s *Store) SyncStatus(ctx context.Context, limit int) ([]SyncRun, error) {
	rows, err := s.Pool.Query(ctx, `
		SELECT id, kind, to_char(started,'YYYY-MM-DD"T"HH24:MI:SSOF'),
		       to_char(finished,'YYYY-MM-DD"T"HH24:MI:SSOF'),
		       status, rows_upserted, detail
		FROM sync_runs ORDER BY started DESC, id DESC LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []SyncRun
	for rows.Next() {
		var r SyncRun
		if err := rows.Scan(&r.ID, &r.Kind, &r.Started, &r.Finished,
			&r.Status, &r.RowsUpserted, &r.Detail); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}
```

- [ ] **Step 4: Run store test**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ -run TestSyncStatus -count=1`
Expected: PASS.

- [ ] **Step 5: Write the API tests**

Create `internal/api/sync_test.go`:

```go
package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGetSyncStatus(t *testing.T) {
	s := testStore(t)
	_, _ = s.Pool.Exec(nil0(), `INSERT INTO sync_runs (kind,status,rows_upserted) VALUES ('simplefin','ok',3)`)
	srv := New(s)
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/sync/status", nil))
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "simplefin") {
		t.Fatalf("status=%d body=%s", rec.Code, rec.Body.String())
	}
}
```

Create `internal/api/imports_test.go` (multipart upload of a tiny Venmo CSV; reuse the real parser). The CSV must match the format the parser expects — reuse the committed golden fixture at `internal/venmo/testdata/venmo-2026.csv`:

```go
package api

import (
	"bytes"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

func TestVenmoUpload(t *testing.T) {
	s := testStore(t)
	srv := New(s)

	csv, err := os.ReadFile("../venmo/testdata/venmo-2026.csv")
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, _ := mw.CreateFormFile("file", "venmo.csv")
	_, _ = fw.Write(csv)
	_ = mw.Close()

	req := httptest.NewRequest(http.MethodPost, "/api/imports/venmo", &buf)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("upload status=%d body=%s", rec.Code, rec.Body.String())
	}
}

func TestVenmoUploadMissingFile(t *testing.T) {
	srv := New(testStore(t))
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	_ = mw.Close()
	req := httptest.NewRequest(http.MethodPost, "/api/imports/venmo", &buf)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status=%d, want 400", rec.Code)
	}
}
```

- [ ] **Step 6: Write the handlers**

Create `internal/api/imports.go` (the uploaded file is streamed straight into `ingest.ImportVenmo` and never written to disk — per spec "parsed and discarded"):

```go
package api

import (
	"net/http"

	"github.com/vollminlab/vollmint/internal/ingest"
)

// maxUploadBytes caps a Venmo CSV upload. A 90-day export is a few hundred KB;
// 10 MiB is generous headroom and bounds memory.
const maxUploadBytes = 10 << 20

func (s *Server) registerImports() {
	s.mux.HandleFunc("POST /api/imports/venmo", s.handleVenmoUpload)
}

func (s *Server) handleVenmoUpload(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)
	if err := r.ParseMultipartForm(maxUploadBytes); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid multipart form or file too large")
		return
	}
	file, _, err := r.FormFile("file")
	if err != nil {
		writeErr(w, http.StatusBadRequest, "missing form field 'file'")
		return
	}
	defer file.Close()

	// The CSV is streamed through the parser and never persisted to disk.
	res, err := ingest.ImportVenmo(r.Context(), s.store, file)
	if err != nil {
		writeErr(w, http.StatusBadRequest, "import failed: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"upserted":    res.Upserted,
		"categorized": res.Categorized,
		"paired":      res.Paired,
	})
}
```

Create `internal/api/sync.go`:

```go
package api

import (
	"net/http"

	"github.com/vollminlab/vollmint/internal/store"
)

func (s *Server) registerSync() {
	s.mux.HandleFunc("GET /api/sync/status", s.handleSyncStatus)
}

func (s *Server) handleSyncStatus(w http.ResponseWriter, r *http.Request) {
	runs, err := s.store.SyncStatus(r.Context(), 20)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if runs == nil {
		runs = []store.SyncRun{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"runs": runs})
}
```

Delete the `registerImports` and `registerSync` stubs from `server.go`.

- [ ] **Step 7: Run tests**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/store/ ./internal/api/ -count=1`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add internal/store/query.go internal/store/query_test.go internal/api/imports.go internal/api/imports_test.go internal/api/sync.go internal/api/sync_test.go internal/api/server.go
git commit -m "feat(api): Venmo CSV upload + sync status endpoints"
```

---

## Task 13: Prometheus `/metrics` + embedded SPA serving with fallback

**Files:**
- Create: `internal/api/static.go`
- Create: `internal/api/static_test.go`
- Create: `web/dist/index.html` (placeholder so `go:embed` compiles before the frontend exists)
- Modify: `internal/api/server.go` (delete `registerStatic` stub)
- Modify: `go.mod`/`go.sum` (adds `prometheus/client_golang`)

- [ ] **Step 1: Create the embed placeholder**

The Go build must succeed before the Vite build exists, so commit a placeholder `web/dist/index.html`:

```bash
mkdir -p web/dist
```

Create `web/dist/index.html`:

```html
<!doctype html>
<title>vollmint</title>
<p>vollmint SPA placeholder — replaced by the Vite build.</p>
```

- [ ] **Step 2: Write the failing test**

Create `internal/api/static_test.go`:

```go
package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestMetricsEndpoint(t *testing.T) {
	srv := New(nil)
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("metrics status=%d", rec.Code)
	}
	// default Go collector always exposes this
	if !strings.Contains(rec.Body.String(), "go_goroutines") {
		t.Fatalf("metrics body missing go_goroutines")
	}
}

func TestSPAIndexServed(t *testing.T) {
	srv := New(nil)
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "vollmint") {
		t.Fatalf("index status=%d body=%s", rec.Code, rec.Body.String())
	}
}

func TestSPAClientRouteFallsBackToIndex(t *testing.T) {
	srv := New(nil)
	rec := httptest.NewRecorder()
	// a client-side route that is not a real file must return index.html, not 404
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/transactions", nil))
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "vollmint") {
		t.Fatalf("client route status=%d body=%s", rec.Code, rec.Body.String())
	}
}

func TestUnknownAPIStill404(t *testing.T) {
	srv := New(nil)
	rec := httptest.NewRecorder()
	srv.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/does-not-exist", nil))
	if rec.Code != http.StatusNotFound {
		t.Fatalf("unknown api status=%d, want 404 (never SPA fallback)", rec.Code)
	}
}
```

- [ ] **Step 3: Add the dependency**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && go get github.com/prometheus/client_golang@v1.20.5`
Expected: `go.mod`/`go.sum` updated. (Verify the version resolves; if `v1.20.5` is unavailable, use the latest `v1.20.x` and note it in the commit.)

- [ ] **Step 4: Write the implementation**

Create `internal/api/static.go`:

```go
package api

import (
	"embed"
	"io/fs"
	"net/http"
	"strings"

	"github.com/prometheus/client_golang/prometheus/promhttp"
)

//go:embed all:../../web/dist
var spaFiles embed.FS

// registerStatic wires /metrics and the SPA. The SPA is served from the
// embedded web/dist; any non-/api, non-file path falls back to index.html so
// client-side routing works. /api/* is never served the SPA — it 404s cleanly.
func (s *Server) registerStatic() {
	s.mux.Handle("GET /metrics", promhttp.Handler())

	dist, err := fs.Sub(spaFiles, "web/dist")
	if err != nil {
		panic("embed web/dist: " + err.Error()) // build-time guarantee
	}
	fileServer := http.FileServer(http.FS(dist))

	s.mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		// /api/* that reached here matched no API route → 404 (never SPA).
		if strings.HasPrefix(r.URL.Path, "/api/") {
			writeErr(w, http.StatusNotFound, "not found")
			return
		}
		// If the requested path exists as an embedded file, serve it.
		p := strings.TrimPrefix(r.URL.Path, "/")
		if p == "" {
			serveIndex(w, r, dist)
			return
		}
		if _, err := fs.Stat(dist, p); err == nil {
			fileServer.ServeHTTP(w, r)
			return
		}
		// Otherwise it's a client-side route → serve index.html.
		serveIndex(w, r, dist)
	})
}

func serveIndex(w http.ResponseWriter, r *http.Request, dist fs.FS) {
	data, err := fs.ReadFile(dist, "index.html")
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "index.html missing from build")
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(data)
}
```

Delete the `registerStatic` stub from `server.go`.

- [ ] **Step 5: Run tests**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go test ./internal/api/ -count=1`
Expected: PASS (all static + earlier api tests). The `//go:embed all:../../web/dist` path is relative to `static.go`; `web/dist/index.html` exists, so it compiles.

- [ ] **Step 6: Full backend suite + build**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go vet ./... && go test ./... -count=1 && go build ./...`
Expected: all packages PASS, build succeeds.

- [ ] **Step 7: Commit**

```bash
git add internal/api/static.go internal/api/static_test.go internal/api/server.go web/dist/index.html go.mod go.sum
git commit -m "feat(api): /metrics + embedded SPA with client-route fallback"
```

The backend API is now complete. Tasks 14–19 build the React SPA.

---

## Task 14: Vite + React + TypeScript scaffold

**Files (all new, under `web/`):**
- Create: `web/package.json`, `web/tsconfig.json`, `web/tsconfig.node.json`, `web/vite.config.ts`, `web/index.html`, `web/.gitignore`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/vite-env.d.ts`, `web/src/setupTests.ts`

The scaffold is authored by hand (not `npm create`) so the plan is deterministic. Node 20+ and npm must be on PATH; verify with `node --version` (expect v20+).

- [ ] **Step 1: Write `web/package.json`**

```json
{
  "name": "vollmint-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.2",
    "recharts": "^2.12.7"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.6",
    "@testing-library/react": "^16.0.0",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^24.1.0",
    "typescript": "^5.5.3",
    "vite": "^5.3.4",
    "vitest": "^2.0.4"
  }
}
```

- [ ] **Step 2: Write the TypeScript + Vite config**

Create `web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

Create `web/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "noEmit": true
  },
  "include": ["vite.config.ts"]
}
```

Create `web/vite.config.ts` (dev server proxies `/api`, `/healthz`, `/metrics` to the Go server on :8080; build emits to `dist`; Vitest uses jsdom):

```ts
/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
      '/healthz': 'http://localhost:8080',
      '/metrics': 'http://localhost:8080',
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
  },
})
```

- [ ] **Step 3: Write the entry points**

Create `web/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>vollmint</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `web/src/vite-env.d.ts`:

```ts
/// <reference types="vite/client" />
```

Create `web/src/setupTests.ts`:

```ts
import '@testing-library/jest-dom'
```

Create `web/src/main.tsx`:

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
```

Create `web/src/index.css`:

```css
:root {
  --bg: #0f1115;
  --panel: #181b22;
  --text: #e6e8ec;
  --muted: #9aa0ab;
  --accent: #4f9cf9;
  --danger: #f9714f;
  --good: #4fd18b;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); }
a { color: var(--accent); text-decoration: none; }
.container { max-width: 1100px; margin: 0 auto; padding: 1rem; }
.card { background: var(--panel); border-radius: 10px; padding: 1rem; }
.grid { display: grid; gap: 1rem; }
button { cursor: pointer; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #262a33; }
```

Create a temporary `web/src/App.tsx` (replaced in Task 17):

```tsx
export default function App() {
  return <div className="container">vollmint</div>
}
```

- [ ] **Step 4: Write `web/.gitignore`**

```
node_modules/
dist/
```

Note: `web/dist/` is git-ignored, but `web/dist/index.html` was committed in Task 13 as the embed placeholder. That's intentional — force-add it so the Go build always has something to embed even on a fresh checkout before `npm run build`. It will be overwritten by real build output locally but the committed placeholder guarantees `go build ./...` never fails. (Task 19 documents the real build flow.)

- [ ] **Step 5: Install and verify the toolchain**

Run: `cd web && npm install`
Expected: dependencies install, `web/package-lock.json` created.

Run: `cd web && npm run build`
Expected: `tsc -b` passes, Vite writes `web/dist/index.html` + assets. (This overwrites the placeholder locally.)

- [ ] **Step 6: Restore the committed placeholder for the embed**

Because the real `dist` is git-ignored but `dist/index.html` must stay tracked, re-assert the tracked copy so we don't accidentally commit a hashed build:

```bash
cd .. && git checkout web/dist/index.html
```

(This leaves the tracked placeholder in git while your local `web/dist` holds the real build for manual testing.)

- [ ] **Step 7: Commit the scaffold**

```bash
git add web/package.json web/package-lock.json web/tsconfig.json web/tsconfig.node.json web/vite.config.ts web/index.html web/.gitignore web/src/main.tsx web/src/App.tsx web/src/index.css web/src/vite-env.d.ts web/src/setupTests.ts
git commit -m "feat(web): Vite + React + TypeScript scaffold"
```

---

## Task 15: Typed API client

**Files:**
- Create: `web/src/api.ts`
- Create: `web/src/api.test.ts`

- [ ] **Step 1: Write the failing test**

Create `web/src/api.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { getSummary, getTransactions, patchTransaction, buildQuery } from './api'

describe('buildQuery', () => {
  it('omits empty params and encodes present ones', () => {
    expect(buildQuery({ view: 'household', month: '2026-07', q: '' })).toBe(
      '?view=household&month=2026-07',
    )
    expect(buildQuery({ view: 'scott', category: 3 })).toBe('?view=scott&category=3')
  })
})

describe('api client', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('getSummary hits the right URL and returns parsed JSON', async () => {
    const payload = { summary: { in: '10.00', out: '5.00', vices: '0.00', budget_total: '0.00' }, categories: [] }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => payload,
    })
    vi.stubGlobal('fetch', fetchMock)
    const res = await getSummary('household', '2026-07')
    expect(fetchMock).toHaveBeenCalledWith('/api/summary?view=household&month=2026-07')
    expect(res.summary.out).toBe('5.00')
  })

  it('getTransactions passes filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ transactions: [] }) })
    vi.stubGlobal('fetch', fetchMock)
    await getTransactions({ view: 'scott', month: '2026-07', category: 4, uncategorized: true })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/transactions?view=scott&month=2026-07&category=4&uncategorized=true',
    )
  })

  it('patchTransaction sends PATCH with JSON body', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok' }) })
    vi.stubGlobal('fetch', fetchMock)
    await patchTransaction(7, { category_id: 3 })
    expect(fetchMock).toHaveBeenCalledWith('/api/transactions/7', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category_id: 3 }),
    })
  })

  it('throws on non-ok response', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ error: 'boom' }) })
    vi.stubGlobal('fetch', fetchMock)
    await expect(getSummary('household', '2026-07')).rejects.toThrow('boom')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/api.test.ts`
Expected: FAIL — cannot resolve `./api`.

- [ ] **Step 3: Write the implementation**

Create `web/src/api.ts`. All monetary fields are typed as `string` (decimal), matching the backend. `buildQuery` drops empty/undefined values.

```ts
// Typed client for the vollmint API. Money fields are decimal strings — never
// coerce them to number except for chart geometry.

export type View = 'scott' | 'nikki' | 'joint' | 'household'

export interface Summary {
  in: string
  out: string
  vices: string
  budget_total: string
  month: string
  view: string
}

export interface CategorySpend {
  category_id: number
  category: string
  spent: string
  budget: string
  is_vice: boolean
}

export interface SummaryResponse {
  summary: Summary
  categories: CategorySpend[]
}

export interface Txn {
  id: number
  source: string
  account_id: string
  account_name: string
  posted: string
  amount: string
  description: string
  payee: string
  pending: boolean
  category_id: number | null
  category_name: string | null
  owner_override: string | null
  effective_owner: string
  transfer_peer_id: number | null
}

export interface Category {
  id: number
  name: string
  parent_id: number | null
  kind: string
  is_vice: boolean
}

export interface Rule {
  id: number
  priority: number
  match_type: string
  pattern: string
  category_id: number
}

export interface Budget {
  category_id: number
  category_name: string
  amount: string
}

export interface Recurring {
  payee: string
  count: number
  months: number
  avg_amount: string
  last_seen: string
  first_seen: string
  is_new: boolean
}

export interface SyncRun {
  id: number
  kind: string
  started: string
  finished: string | null
  status: string
  rows_upserted: number
  detail: string
}

export type QueryParams = Record<string, string | number | boolean | undefined>

// buildQuery renders a query string, dropping undefined/empty values.
export function buildQuery(params: QueryParams): string {
  const parts: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === '' || v === false) continue
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  }
  return parts.length ? `?${parts.join('&')}` : ''
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let msg = `request failed: ${res.status}`
    try {
      const body = await res.json()
      if (body && body.error) msg = body.error
    } catch {
      // non-JSON error body; keep the status message
    }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export function getSummary(view: View, month: string): Promise<SummaryResponse> {
  return req<SummaryResponse>(`/api/summary${buildQuery({ view, month })}`)
}

export interface TxnFilter {
  view: View
  month?: string
  category?: number
  account?: string
  q?: string
  uncategorized?: boolean
}

export function getTransactions(f: TxnFilter): Promise<{ transactions: Txn[] }> {
  return req(`/api/transactions${buildQuery({ ...f })}`)
}

export function patchTransaction(
  id: number,
  patch: { category_id?: number; owner_override?: string },
): Promise<{ status: string }> {
  return req(`/api/transactions/${id}`, jsonInit('PATCH', patch))
}

export function getCategories(): Promise<{ categories: Category[] }> {
  return req('/api/categories')
}

export function getRules(): Promise<{ rules: Rule[] }> {
  return req('/api/rules')
}

export function createRule(rule: {
  priority: number
  match_type: string
  pattern: string
  category_id: number
}): Promise<{ id: number; recategorized: number }> {
  return req('/api/rules', jsonInit('POST', rule))
}

export function deleteRule(id: number): Promise<{ status: string }> {
  return req(`/api/rules/${id}`, { method: 'DELETE' })
}

export function getBudgets(month: string): Promise<{ budgets: Budget[] }> {
  return req(`/api/budgets${buildQuery({ month })}`)
}

export function putBudgets(
  month: string,
  budgets: { category_id: number; amount: string }[],
): Promise<{ status: string }> {
  return req(`/api/budgets${buildQuery({ month })}`, jsonInit('PUT', { budgets }))
}

export function getRecurring(view: View, month: string): Promise<{ recurring: Recurring[] }> {
  return req(`/api/recurring${buildQuery({ view, month })}`)
}

export function uploadVenmo(file: File): Promise<{ upserted: number; categorized: number; paired: number }> {
  const form = new FormData()
  form.append('file', file)
  return req('/api/imports/venmo', { method: 'POST', body: form })
}

export function getSyncStatus(): Promise<{ runs: SyncRun[] }> {
  return req('/api/sync/status')
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/api.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/api.ts web/src/api.test.ts
git commit -m "feat(web): typed API client with buildQuery"
```

---

## Task 16: Shared UI primitives — money formatting, ViewSwitcher, MonthPager

**Files:**
- Create: `web/src/format.ts`, `web/src/format.test.ts`
- Create: `web/src/components/ViewSwitcher.tsx`
- Create: `web/src/components/MonthPager.tsx`, `web/src/components/MonthPager.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `web/src/format.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { money, shiftMonth, monthLabel } from './format'

describe('money', () => {
  it('formats a decimal string as USD', () => {
    expect(money('1234.50')).toBe('$1,234.50')
    expect(money('0.00')).toBe('$0.00')
    expect(money('-42.00')).toBe('-$42.00')
  })
  it('handles empty/undefined as $0.00', () => {
    expect(money('')).toBe('$0.00')
  })
})

describe('shiftMonth', () => {
  it('moves forward and backward across year boundaries', () => {
    expect(shiftMonth('2026-07', 1)).toBe('2026-08')
    expect(shiftMonth('2026-12', 1)).toBe('2027-01')
    expect(shiftMonth('2026-01', -1)).toBe('2025-12')
  })
})

describe('monthLabel', () => {
  it('renders a human label', () => {
    expect(monthLabel('2026-07')).toBe('July 2026')
  })
})
```

Create `web/src/components/MonthPager.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MonthPager } from './MonthPager'

describe('MonthPager', () => {
  it('renders the label and calls onChange with shifted month', async () => {
    const onChange = vi.fn()
    render(<MonthPager month="2026-07" onChange={onChange} />)
    expect(screen.getByText('July 2026')).toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('previous month'))
    expect(onChange).toHaveBeenCalledWith('2026-06')
    await userEvent.click(screen.getByLabelText('next month'))
    expect(onChange).toHaveBeenCalledWith('2026-08')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run src/format.test.ts src/components/MonthPager.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write `web/src/format.ts`**

```ts
// Display helpers. money() parses the decimal string ONLY to drive
// Intl.NumberFormat's grouping — the value is never stored or re-serialized as
// a number, so float imprecision cannot leak back into data.
const usd = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

export function money(v: string): string {
  if (!v) return '$0.00'
  const n = Number(v)
  if (Number.isNaN(n)) return v
  return usd.format(n)
}

// shiftMonth adds delta months to a YYYY-MM string.
export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split('-').map(Number)
  const idx = (y * 12 + (m - 1)) + delta
  const ny = Math.floor(idx / 12)
  const nm = (idx % 12) + 1
  return `${ny.toString().padStart(4, '0')}-${nm.toString().padStart(2, '0')}`
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export function monthLabel(month: string): string {
  const [y, m] = month.split('-').map(Number)
  return `${MONTHS[m - 1]} ${y}`
}

// currentMonth returns today's month as YYYY-MM.
export function currentMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${(d.getMonth() + 1).toString().padStart(2, '0')}`
}
```

- [ ] **Step 4: Write the components**

Create `web/src/components/MonthPager.tsx`:

```tsx
import { monthLabel, shiftMonth } from '../format'

export function MonthPager({
  month,
  onChange,
}: {
  month: string
  onChange: (m: string) => void
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
      <button aria-label="previous month" onClick={() => onChange(shiftMonth(month, -1))}>
        ‹
      </button>
      <strong>{monthLabel(month)}</strong>
      <button aria-label="next month" onClick={() => onChange(shiftMonth(month, 1))}>
        ›
      </button>
    </div>
  )
}
```

Create `web/src/components/ViewSwitcher.tsx`:

```tsx
import type { View } from '../api'

const VIEWS: View[] = ['scott', 'nikki', 'joint', 'household']
const LABELS: Record<View, string> = {
  scott: 'Scott',
  nikki: 'Nikki',
  joint: 'Joint',
  household: 'Household',
}

export function ViewSwitcher({
  view,
  onChange,
}: {
  view: View
  onChange: (v: View) => void
}) {
  return (
    <div style={{ display: 'flex', gap: '0.4rem' }}>
      {VIEWS.map((v) => (
        <button
          key={v}
          onClick={() => onChange(v)}
          aria-pressed={v === view}
          style={{
            padding: '0.3rem 0.7rem',
            borderRadius: '999px',
            border: '1px solid #2c313b',
            background: v === view ? 'var(--accent)' : 'transparent',
            color: v === view ? '#04121f' : 'var(--text)',
          }}
        >
          {LABELS[v]}
        </button>
      ))}
    </div>
  )
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd web && npx vitest run src/format.test.ts src/components/MonthPager.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/src/format.ts web/src/format.test.ts web/src/components/ViewSwitcher.tsx web/src/components/MonthPager.tsx web/src/components/MonthPager.test.tsx
git commit -m "feat(web): money/month helpers, ViewSwitcher, MonthPager"
```

---

## Task 17: App shell — routing + shared view/month state

**Files:**
- Modify: `web/src/App.tsx` (replace the placeholder)
- Create: `web/src/components/Nav.tsx`
- Create: `web/src/App.test.tsx`

The view and month live in the URL query string so deep-links ("drill into this category for this view+month") work and survive reload. `App` reads them from `useSearchParams` and passes setters down.

- [ ] **Step 1: Write the failing test**

Create `web/src/App.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from './App'

// stub the API so the dashboard's initial load doesn't hit the network
beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        summary: { in: '0.00', out: '0.00', vices: '0.00', budget_total: '0.00', month: '2026-07', view: 'household' },
        categories: [],
        transactions: [],
        recurring: [],
      }),
    }),
  )
})

describe('App', () => {
  it('renders the nav with all three pages', () => {
    render(
      <MemoryRouter initialEntries={['/?view=household&month=2026-07']}>
        <App />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Transactions' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Budgets' })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/App.test.tsx`
Expected: FAIL — placeholder App has no nav.

- [ ] **Step 3: Write `web/src/components/Nav.tsx`**

```tsx
import { NavLink } from 'react-router-dom'

// Nav preserves the current query string (view+month) across page links so the
// shared state survives navigation.
export function Nav({ search }: { search: string }) {
  const link = (to: string, label: string) => (
    <NavLink
      to={{ pathname: to, search }}
      style={({ isActive }) => ({
        padding: '0.4rem 0.8rem',
        fontWeight: isActive ? 700 : 400,
        color: isActive ? 'var(--accent)' : 'var(--text)',
      })}
      end
    >
      {label}
    </NavLink>
  )
  return (
    <nav style={{ display: 'flex', gap: '0.5rem', borderBottom: '1px solid #262a33', marginBottom: '1rem' }}>
      {link('/', 'Dashboard')}
      {link('/transactions', 'Transactions')}
      {link('/budgets', 'Budgets')}
    </nav>
  )
}
```

- [ ] **Step 4: Write `web/src/App.tsx`**

```tsx
import { Routes, Route, useSearchParams } from 'react-router-dom'
import type { View } from './api'
import { currentMonth } from './format'
import { Nav } from './components/Nav'
import { ViewSwitcher } from './components/ViewSwitcher'
import { MonthPager } from './components/MonthPager'
import { Dashboard } from './components/Dashboard'
import { Transactions } from './components/Transactions'
import { Budgets } from './components/Budgets'

const isView = (v: string): v is View =>
  v === 'scott' || v === 'nikki' || v === 'joint' || v === 'household'

export default function App() {
  const [params, setParams] = useSearchParams()
  const rawView = params.get('view') ?? 'household'
  const view: View = isView(rawView) ? rawView : 'household'
  const month = params.get('month') ?? currentMonth()

  const update = (next: { view?: View; month?: string }) => {
    const p = new URLSearchParams(params)
    if (next.view) p.set('view', next.view)
    if (next.month) p.set('month', next.month)
    setParams(p, { replace: true })
  }

  return (
    <div className="container">
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '1rem',
          flexWrap: 'wrap',
          gap: '0.75rem',
        }}
      >
        <h1 style={{ margin: 0, fontSize: '1.3rem' }}>vollmint</h1>
        <MonthPager month={month} onChange={(m) => update({ month: m })} />
        <ViewSwitcher view={view} onChange={(v) => update({ view: v })} />
      </header>
      <Nav search={`?${params.toString()}`} />
      <Routes>
        <Route path="/" element={<Dashboard view={view} month={month} />} />
        <Route path="/transactions" element={<Transactions view={view} month={month} />} />
        <Route path="/budgets" element={<Budgets month={month} />} />
      </Routes>
    </div>
  )
}
```

`App` imports `Dashboard`, `Transactions`, and `Budgets`, which don't exist yet — Tasks 18–19 create them. To keep the test in this task compiling and passing, create **minimal stub files now** and flesh each out in its own task:

Create `web/src/components/Dashboard.tsx`:

```tsx
import type { View } from '../api'

export function Dashboard(_: { view: View; month: string }) {
  return <div>Dashboard</div>
}
```

Create `web/src/components/Transactions.tsx`:

```tsx
import type { View } from '../api'

export function Transactions(_: { view: View; month: string }) {
  return <div>Transactions</div>
}
```

Create `web/src/components/Budgets.tsx`:

```tsx
export function Budgets(_: { month: string }) {
  return <div>Budgets</div>
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd web && npx vitest run src/App.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/src/App.tsx web/src/App.test.tsx web/src/components/Nav.tsx web/src/components/Dashboard.tsx web/src/components/Transactions.tsx web/src/components/Budgets.tsx
git commit -m "feat(web): app shell, routing, URL-backed view/month state"
```

---

## Task 18: Dashboard — summary cards + category bars with drill-down links

**Files:**
- Modify: `web/src/components/Dashboard.tsx` (replace the stub)
- Create: `web/src/components/SummaryCards.tsx`
- Create: `web/src/components/CategoryBars.tsx`
- Create: `web/src/components/Dashboard.test.tsx`

The dashboard is the core deliverable: In/Out/vs-Budget/Vices cards, and a spend-by-category bar list where **every bar links to the Transactions page pre-filtered** to that category + current view + month ("no number is a dead end").

- [ ] **Step 1: Write the failing test**

Create `web/src/components/Dashboard.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Dashboard } from './Dashboard'

const summaryPayload = {
  summary: { in: '3000.00', out: '140.00', vices: '40.00', budget_total: '120.00', month: '2026-07', view: 'household' },
  categories: [
    { category_id: 2, category: 'Groceries', spent: '100.00', budget: '120.00', is_vice: false },
    { category_id: 3, category: 'Dining', spent: '40.00', budget: '', is_vice: true },
  ],
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => summaryPayload }))
})

describe('Dashboard', () => {
  it('shows summary cards and category bars', async () => {
    render(
      <MemoryRouter initialEntries={['/?view=household&month=2026-07']}>
        <Dashboard view="household" month="2026-07" />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('$3,000.00')).toBeInTheDocument()) // In
    expect(screen.getByText('$140.00')).toBeInTheDocument() // Out
    expect(screen.getByText('Groceries')).toBeInTheDocument()
  })

  it('each category bar deep-links to Transactions pre-filtered by category', async () => {
    render(
      <MemoryRouter initialEntries={['/?view=scott&month=2026-07']}>
        <Dashboard view="scott" month="2026-07" />
      </MemoryRouter>,
    )
    const link = await screen.findByRole('link', { name: /Groceries/ })
    const href = link.getAttribute('href')!
    expect(href).toContain('/transactions')
    expect(href).toContain('category=2')
    expect(href).toContain('view=scott')
    expect(href).toContain('month=2026-07')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/Dashboard.test.tsx`
Expected: FAIL — stub Dashboard renders only "Dashboard".

- [ ] **Step 3: Write `web/src/components/SummaryCards.tsx`**

```tsx
import type { Summary } from '../api'
import { money } from '../format'

// vsBudget is out − budget_total as a signed decimal, computed in JS for
// DISPLAY only (both operands are exact 2-dp strings, so string math is safe
// here via cents integers — no float rounding).
function centsDiff(a: string, b: string): string {
  const toCents = (s: string) => Math.round(Number(s) * 100)
  const d = toCents(a) - toCents(b)
  const sign = d < 0 ? '-' : ''
  const abs = Math.abs(d)
  return `${sign}${Math.floor(abs / 100)}.${(abs % 100).toString().padStart(2, '0')}`
}

export function SummaryCards({ s }: { s: Summary }) {
  const vs = centsDiff(s.out, s.budget_total)
  const overBudget = Number(vs) > 0
  const cards: { label: string; value: string; tone?: string }[] = [
    { label: 'Money In', value: money(s.in), tone: 'var(--good)' },
    { label: 'Money Out', value: money(s.out) },
    { label: 'vs Budget', value: money(vs), tone: overBudget ? 'var(--danger)' : 'var(--good)' },
    { label: 'Vices', value: money(s.vices), tone: 'var(--danger)' },
  ]
  return (
    <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }}>
      {cards.map((c) => (
        <div key={c.label} className="card">
          <div style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>{c.label}</div>
          <div style={{ fontSize: '1.6rem', fontWeight: 700, color: c.tone ?? 'var(--text)' }}>{c.value}</div>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 4: Write `web/src/components/CategoryBars.tsx`**

```tsx
import { Link } from 'react-router-dom'
import type { CategorySpend, View } from '../api'
import { money } from '../format'

// Bar width is the ONLY place a monetary value becomes a float — pixel geometry
// only, never displayed or stored.
function pct(spent: string, max: number): number {
  if (max <= 0) return 0
  return Math.min(100, (Number(spent) / max) * 100)
}

export function CategoryBars({
  categories,
  view,
  month,
}: {
  categories: CategorySpend[]
  view: View
  month: string
}) {
  const max = categories.reduce((m, c) => Math.max(m, Number(c.spent)), 0)
  if (categories.length === 0) {
    return <p style={{ color: 'var(--muted)' }}>No spending this month.</p>
  }
  return (
    <div className="grid" style={{ gap: '0.5rem' }}>
      {categories.map((c) => {
        const over = c.budget !== '' && Number(c.spent) > Number(c.budget)
        const to = `/transactions?view=${view}&month=${month}&category=${c.category_id}`
        return (
          <Link key={c.category_id} to={to} className="card" style={{ display: 'block' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>
                {c.category}
                {c.is_vice ? ' 🔥' : ''}
              </span>
              <span style={{ color: over ? 'var(--danger)' : 'var(--text)' }}>
                {money(c.spent)}
                {c.budget !== '' ? ` / ${money(c.budget)}` : ''}
              </span>
            </div>
            <div style={{ height: 6, background: '#262a33', borderRadius: 3, marginTop: 6 }}>
              <div
                style={{
                  width: `${pct(c.spent, max)}%`,
                  height: '100%',
                  borderRadius: 3,
                  background: over ? 'var(--danger)' : 'var(--accent)',
                }}
              />
            </div>
          </Link>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 5: Write `web/src/components/Dashboard.tsx`**

```tsx
import { useEffect, useState } from 'react'
import type { View, SummaryResponse } from '../api'
import { getSummary } from '../api'
import { SummaryCards } from './SummaryCards'
import { CategoryBars } from './CategoryBars'

export function Dashboard({ view, month }: { view: View; month: string }) {
  const [data, setData] = useState<SummaryResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setData(null)
    setErr(null)
    getSummary(view, month)
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(e.message))
    return () => {
      live = false
    }
  }, [view, month])

  if (err) return <p style={{ color: 'var(--danger)' }}>Error: {err}</p>
  if (!data) return <p style={{ color: 'var(--muted)' }}>Loading…</p>

  return (
    <div className="grid" style={{ gap: '1.5rem' }}>
      <SummaryCards s={data.summary} />
      <section>
        <h2 style={{ fontSize: '1rem' }}>Spending by category</h2>
        <CategoryBars categories={data.categories} view={view} month={month} />
      </section>
    </div>
  )
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/Dashboard.test.tsx`
Expected: PASS (both, including the drill-down href assertions).

- [ ] **Step 7: Commit**

```bash
git add web/src/components/Dashboard.tsx web/src/components/SummaryCards.tsx web/src/components/CategoryBars.tsx web/src/components/Dashboard.test.tsx
git commit -m "feat(web): dashboard summary cards + category drill-down bars"
```

---

## Task 19: Transactions page — filter plumbing + inline recategorize; Budgets page

**Files:**
- Modify: `web/src/components/Transactions.tsx` (replace the stub)
- Create: `web/src/components/Transactions.test.tsx`
- Modify: `web/src/components/Budgets.tsx` (replace the stub)
- Create: `web/src/components/Budgets.test.tsx`

The Transactions page reads its category filter from the URL (so a dashboard drill-down lands pre-filtered) and lets the user recategorize a row inline (PATCH). The drill-down plumbing is the spec's explicitly-required component test.

- [ ] **Step 1: Write the failing Transactions test**

Create `web/src/components/Transactions.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Transactions } from './Transactions'

const txns = {
  transactions: [
    {
      id: 5, source: 'simplefin', account_id: 'ally-s', account_name: 'Ally',
      posted: '2026-07-05', amount: '-100.00', description: 'WHOLE FOODS', payee: 'WHOLE FOODS',
      pending: false, category_id: 2, category_name: 'Groceries',
      owner_override: null, effective_owner: 'scott', transfer_peer_id: null,
    },
  ],
}
const cats = { categories: [{ id: 2, name: 'Groceries', parent_id: null, kind: 'spend', is_vice: false }] }

function stubFetch() {
  return vi.fn((url: string) => {
    const body = url.startsWith('/api/categories') ? cats : txns
    return Promise.resolve({ ok: true, json: async () => body })
  })
}

beforeEach(() => {
  vi.stubGlobal('fetch', stubFetch())
})

describe('Transactions drill-down plumbing', () => {
  it('reads the category filter from the URL and passes it to the API', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter initialEntries={['/transactions?view=scott&month=2026-07&category=2']}>
        <Transactions view="scott" month="2026-07" />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('WHOLE FOODS')).toBeInTheDocument())
    // the transactions request must have carried category=2 and view=scott
    const calledUrls = fetchMock.mock.calls.map((c) => c[0] as string)
    const txnCall = calledUrls.find((u) => u.startsWith('/api/transactions'))!
    expect(txnCall).toContain('category=2')
    expect(txnCall).toContain('view=scott')
    expect(txnCall).toContain('month=2026-07')
  })

  it('shows a heading noting the active category filter', async () => {
    render(
      <MemoryRouter initialEntries={['/transactions?view=scott&month=2026-07&category=2']}>
        <Transactions view="scott" month="2026-07" />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText(/Filtered by category/i)).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/Transactions.test.tsx`
Expected: FAIL — stub renders only "Transactions".

- [ ] **Step 3: Write `web/src/components/Transactions.tsx`**

```tsx
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { View, Txn, Category } from '../api'
import { getTransactions, getCategories, patchTransaction } from '../api'
import { money } from '../format'

export function Transactions({ view, month }: { view: View; month: string }) {
  const [params] = useSearchParams()
  const categoryParam = params.get('category')
  const categoryId = categoryParam ? Number(categoryParam) : undefined

  const [rows, setRows] = useState<Txn[]>([])
  const [cats, setCats] = useState<Category[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    getTransactions({ view, month, category: categoryId })
      .then((d) => setRows(d.transactions))
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, month, categoryParam])

  useEffect(() => {
    getCategories().then((d) => setCats(d.categories)).catch(() => {})
  }, [])

  const recategorize = async (id: number, catId: number) => {
    await patchTransaction(id, { category_id: catId })
    load()
  }

  const activeCat = cats.find((c) => c.id === categoryId)

  if (err) return <p style={{ color: 'var(--danger)' }}>Error: {err}</p>

  return (
    <div>
      {categoryId !== undefined && (
        <p style={{ color: 'var(--muted)' }}>
          Filtered by category: <strong>{activeCat ? activeCat.name : categoryId}</strong>
        </p>
      )}
      {loading ? (
        <p style={{ color: 'var(--muted)' }}>Loading…</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Payee</th>
              <th>Account</th>
              <th style={{ textAlign: 'right' }}>Amount</th>
              <th>Category</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id}>
                <td>{t.posted}</td>
                <td>{t.payee || t.description}</td>
                <td>{t.account_name}</td>
                <td style={{ textAlign: 'right', color: t.amount.startsWith('-') ? 'var(--text)' : 'var(--good)' }}>
                  {money(t.amount)}
                </td>
                <td>
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
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} style={{ color: 'var(--muted)' }}>
                  No transactions.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run Transactions test**

Run: `cd web && npx vitest run src/components/Transactions.test.tsx`
Expected: PASS (both).

- [ ] **Step 5: Write the failing Budgets test**

Create `web/src/components/Budgets.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { Budgets } from './Budgets'

const cats = { categories: [
  { id: 2, name: 'Groceries', parent_id: null, kind: 'spend', is_vice: false },
  { id: 3, name: 'Dining', parent_id: null, kind: 'spend', is_vice: true },
] }
const budgets = { budgets: [{ category_id: 2, category_name: 'Groceries', amount: '120.00' }] }

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const body = (url as string).startsWith('/api/categories') ? cats : budgets
    return Promise.resolve({ ok: true, json: async () => body })
  }))
})

describe('Budgets', () => {
  it('lists categories with their current budget value', async () => {
    render(<Budgets month="2026-07" />)
    await waitFor(() => expect(screen.getByText('Groceries')).toBeInTheDocument())
    const grocInput = screen.getByLabelText('budget for Groceries') as HTMLInputElement
    expect(grocInput.value).toBe('120.00')
    const diningInput = screen.getByLabelText('budget for Dining') as HTMLInputElement
    expect(diningInput.value).toBe('')
  })
})
```

- [ ] **Step 6: Write `web/src/components/Budgets.tsx`**

```tsx
import { useEffect, useState } from 'react'
import type { Category } from '../api'
import { getCategories, getBudgets, putBudgets } from '../api'

export function Budgets({ month }: { month: string }) {
  const [cats, setCats] = useState<Category[]>([])
  const [amounts, setAmounts] = useState<Record<number, string>>({})
  const [status, setStatus] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([getCategories(), getBudgets(month)])
      .then(([c, b]) => {
        setCats(c.categories)
        const m: Record<number, string> = {}
        for (const item of b.budgets) m[item.category_id] = item.amount
        setAmounts(m)
      })
      .catch((e) => setStatus(e.message))
  }, [month])

  const save = async () => {
    const items = Object.entries(amounts)
      .filter(([, v]) => v.trim() !== '')
      .map(([id, amount]) => ({ category_id: Number(id), amount: amount.trim() }))
    try {
      await putBudgets(month, items)
      setStatus('Saved.')
    } catch (e) {
      setStatus((e as Error).message)
    }
  }

  // Only spend/savings categories get budgets (income/transfer don't).
  const budgetable = cats.filter((c) => c.kind === 'spend' || c.kind === 'savings')

  return (
    <div className="grid" style={{ gap: '1rem', maxWidth: 480 }}>
      <h2 style={{ fontSize: '1rem', margin: 0 }}>Monthly budgets</h2>
      {budgetable.map((c) => (
        <div key={c.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <label htmlFor={`budget-${c.id}`}>{c.name}</label>
          <input
            id={`budget-${c.id}`}
            aria-label={`budget for ${c.name}`}
            inputMode="decimal"
            placeholder="0.00"
            value={amounts[c.id] ?? ''}
            onChange={(e) => setAmounts((a) => ({ ...a, [c.id]: e.target.value }))}
            style={{ width: 120, padding: '0.3rem', textAlign: 'right' }}
          />
        </div>
      ))}
      <button onClick={save} style={{ padding: '0.5rem', background: 'var(--accent)', border: 'none', borderRadius: 6 }}>
        Save budgets
      </button>
      {status && <p style={{ color: 'var(--muted)' }}>{status}</p>}
    </div>
  )
}
```

- [ ] **Step 7: Run tests**

Run: `cd web && npx vitest run src/components/Transactions.test.tsx src/components/Budgets.test.tsx`
Expected: PASS.

- [ ] **Step 8: Run the full frontend suite + typecheck + build**

Run: `cd web && npx vitest run && npm run build`
Expected: all tests PASS; `tsc -b` clean; Vite build succeeds.

Then restore the tracked embed placeholder so the hashed build isn't committed:

```bash
cd .. && git checkout web/dist/index.html
```

- [ ] **Step 9: Commit**

```bash
git add web/src/components/Transactions.tsx web/src/components/Transactions.test.tsx web/src/components/Budgets.tsx web/src/components/Budgets.test.tsx
git commit -m "feat(web): transactions drill-down + inline recategorize; budgets editor"
```

---

## Task 20: Full-stack build wiring + smoke check

**Files:**
- Create: `scripts/build.sh`
- Create: `docs/development.md` (if a `docs/` dir doesn't exist yet, create it)

This task ties the frontend build to the Go binary: build the SPA, embed it, build Go, and smoke-test that `serve` boots and answers.

- [ ] **Step 1: Write the build script**

Create `scripts/build.sh`:

```bash
#!/usr/bin/env bash
# Builds the vollmint SPA and the Go binary with the SPA embedded.
set -euo pipefail
cd "$(dirname "$0")/.."

export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"

echo "==> building web frontend"
(cd web && npm ci && npm run build)

echo "==> building go binary (embeds web/dist)"
go build -o bin/vollmint ./cmd/vollmint

echo "==> done: ./bin/vollmint"
```

Make it executable:

```bash
chmod +x scripts/build.sh
```

- [ ] **Step 2: Run the build script**

Run: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && ./scripts/build.sh`
Expected: web build + `go build` succeed; `bin/vollmint` exists.

- [ ] **Step 3: Smoke-test the server**

Start it against the dev DB on an alternate port, hit the endpoints, then stop it:

```bash
export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"
export DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable'
export LISTEN_ADDR=':8099'
./bin/vollmint serve &
SERVER_PID=$!
sleep 2
echo "--- healthz ---"; curl -fsS http://localhost:8099/healthz; echo
echo "--- summary ---"; curl -fsS 'http://localhost:8099/api/summary?view=household&month=2026-07'; echo
echo "--- metrics (first line) ---"; curl -fsS http://localhost:8099/metrics | head -1
echo "--- SPA root ---"; curl -fsS http://localhost:8099/ | head -1
kill $SERVER_PID
```

Expected: `healthz` prints `ok`; summary returns a JSON object with a `summary` key; metrics prints a `# HELP`/`# TYPE` line; SPA root prints an HTML doctype/`<html`. (The migrations run automatically on boot.)

- [ ] **Step 4: Write `docs/development.md`**

```markdown
# vollmint — local development

## Prerequisites

- Go on PATH: `export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"`
- Node 20+ and npm
- A Postgres 16 dev instance at `localhost:5433` (user `postgres`, password `dev`)

## Backend

```bash
export DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable'
go run ./cmd/vollmint serve            # migrates, then serves on :8080
```

Environment:
- `DATABASE_URL` (required) — Postgres DSN
- `LISTEN_ADDR` (optional, default `:8080`)

The `serve` process never touches SimpleFIN credentials — ingestion runs
separately via the `sync` / `import-venmo` subcommands (see the deploy plan for
the CronJob wiring).

## Frontend

```bash
cd web
npm install
npm run dev       # Vite dev server on :5173, proxies /api → :8080
```

Run the Go server (`serve`) in one terminal and the Vite dev server in another;
the proxy forwards API calls.

## Tests

```bash
# Go (needs the dev DB)
export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable'
go test ./... -count=1

# Frontend
cd web && npm test
```

## Production build

```bash
./scripts/build.sh     # builds the SPA, embeds it, produces ./bin/vollmint
```
```

- [ ] **Step 5: Full backend + frontend verification**

Run:

```bash
export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH" && export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' && go vet ./... && go test ./... -count=1 && (cd web && npx vitest run)
```

Expected: `go vet` clean, all Go tests PASS, all frontend tests PASS.

- [ ] **Step 6: Restore the embed placeholder and commit**

```bash
git checkout web/dist/index.html
git add scripts/build.sh docs/development.md
git commit -m "chore: full-stack build script + development docs"
```

---

## Done

After Task 20, the branch `feat/api-frontend` contains the complete HTTP API and React SPA. The final whole-branch review (per subagent-driven-development) runs next, then push + open PR. **Do not merge** — that requires explicit sign-off.

### Spec coverage cross-check

| Spec endpoint / feature | Task |
|---|---|
| `GET /api/summary` | 6, 7 |
| `GET /api/transactions` | 2, 3 |
| `PATCH /api/transactions/{id}` | 4, 5 |
| `GET/POST/DELETE /api/rules` (+ re-run) | 9 |
| `GET/POST/PATCH /api/categories` | 8 |
| `GET/PUT /api/budgets` | 10 |
| `GET /api/recurring` | 11 |
| `POST /api/imports/venmo` | 12 |
| `GET /api/sync/status` | 12 |
| `GET /healthz` | 1 |
| `GET /metrics` | 13 |
| Dashboard (layout A, 4 cards) | 18 |
| Transactions page + drill-down | 18 (links), 19 (page) |
| Budgets page | 19 |
| Month pager + view switcher (shared state) | 16, 17 |
| "No number is a dead end" deep-links | 18 |
| Component test for drill-down plumbing | 19 |
| go:embed SPA into binary | 13, 20 |
