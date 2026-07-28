# vollmint Backend Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the vollmint Go backend core — Postgres schema, SimpleFIN client, Venmo CSV parser, category rules engine, transfer matcher, and the `claim` / `sync` / `import-venmo` CLI — as a new repo `vollminlab/vollmint`.

**Architecture:** Single Go module, stdlib CLI dispatch (`serve` comes in plan 2). All money amounts are `numeric(12,2)` in Postgres and validated **strings** in Go — never floats. Ingestion is idempotent via `UNIQUE (source, external_id)` upsert and never deletes. Spec: `docs/superpowers/specs/vollmint-design.md` (k8s-vollminlab-cluster repo).

**Tech Stack:** Go 1.23+, `jackc/pgx/v5` (DB), `pressly/goose/v3` (embedded migrations), Postgres 16 (dev via Docker), stdlib `net/http` + `encoding/csv` + `httptest`.

**Plan 1 of 3.** Plan 2 = HTTP API + React SPA. Plan 3 = Dockerfile/chart/CI/cluster deploy. Not in this plan: any HTTP server, any Kubernetes manifest.

---

## Prerequisites (one-time, before Task 1)

```bash
go version          # need >= 1.23
docker run -d --name vollmint-pg -e POSTGRES_PASSWORD=dev -p 5433:5432 postgres:16
export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable'
```

All store/sync tests use `TEST_DATABASE_URL` and **fail** (not skip) when unset — silent skips hide broken tests.

## File structure (end state of this plan)

```
~/repos/vollminlab/vollmint/
  go.mod                          module github.com/vollminlab/vollmint
  cmd/vollmint/main.go            CLI dispatch: claim | sync | import-venmo
  internal/migrate/migrate.go     goose runner (embedded FS)
  internal/migrate/migrations/0001_schema.sql
  internal/migrate/migrations/0002_seed.sql
  internal/store/store.go         pgx pool, upserts, queries
  internal/store/store_test.go
  internal/store/testdb_test.go   shared test-DB helper
  internal/simplefin/client.go    Claim + Accounts
  internal/simplefin/client_test.go
  internal/venmo/parser.go        CSV → []store.Txn
  internal/venmo/parser_test.go
  internal/venmo/testdata/*.csv   golden files
  internal/ingest/rules.go        category rules engine
  internal/ingest/rules_test.go
  internal/ingest/matcher.go      transfer matcher (Venmo + card payments)
  internal/ingest/matcher_test.go
  internal/ingest/sync.go         sync orchestration + pending sweep
  internal/ingest/sync_test.go
```

---

### Task 1: Repo scaffold

**Files:**
- Create: `~/repos/vollminlab/vollmint/` (new GitHub repo), `go.mod`, `.gitignore`, `README.md`, `cmd/vollmint/main.go`

- [ ] **Step 1: Create the repo and module**

```bash
gh repo create vollminlab/vollmint --private --description "Household budget tracker (SimpleFIN + Venmo CSV ingestion)" --clone -- ~/repos/vollminlab/vollmint 2>/dev/null \
  || (gh repo create vollminlab/vollmint --private --description "Household budget tracker (SimpleFIN + Venmo CSV ingestion)" && git clone https://github.com/vollminlab/vollmint ~/repos/vollminlab/vollmint)
cd ~/repos/vollminlab/vollmint
go mod init github.com/vollminlab/vollmint
```

- [ ] **Step 2: Write `.gitignore` and `README.md`**

`.gitignore`:
```
vollmint
*.csv
!internal/venmo/testdata/*.csv
.env
```
(Real Venmo exports must never be committed — only sanitized testdata fixtures.)

`README.md`:
```markdown
# vollmint

Household budget tracker. Go backend, React SPA (plan 2), deployed on the
vollminlab cluster (plan 3). Design spec lives in
k8s-vollminlab-cluster/docs/superpowers/specs/vollmint-design.md.

## Commands
- `vollmint claim <setup-token>` — one-time SimpleFIN claim; prints Access URL (save to 1Password, never to disk)
- `vollmint sync` — pull SimpleFIN accounts/transactions (env: DATABASE_URL, SIMPLEFIN_ACCESS_URL)
- `vollmint import-venmo <file.csv>` — import a Venmo CSV export (env: DATABASE_URL)

## Dev
docker run -d --name vollmint-pg -e POSTGRES_PASSWORD=dev -p 5433:5432 postgres:16
export TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable'
go test ./...
```

- [ ] **Step 3: Write the CLI skeleton** — `cmd/vollmint/main.go`:

```go
package main

import (
	"fmt"
	"os"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	var err error
	switch os.Args[1] {
	case "claim":
		err = runClaim(os.Args[2:])
	case "sync":
		err = runSync(os.Args[2:])
	case "import-venmo":
		err = runImportVenmo(os.Args[2:])
	default:
		usage()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: vollmint <claim|sync|import-venmo> [args]")
	os.Exit(2)
}

// Implemented in later tasks; stubs keep the build green.
func runClaim(args []string) error       { return fmt.Errorf("not implemented") }
func runSync(args []string) error        { return fmt.Errorf("not implemented") }
func runImportVenmo(args []string) error { return fmt.Errorf("not implemented") }
```

- [ ] **Step 4: Verify it builds**

Run: `go build ./... && go vet ./...`
Expected: no output, exit 0.

- [ ] **Step 5: Commit to main (initial scaffold), then branch**

```bash
git add .gitignore README.md go.mod cmd/vollmint/main.go
git commit -m "chore: scaffold vollmint CLI skeleton"
git push -u origin main
git checkout -b feat/backend-core
```
All remaining tasks commit to `feat/backend-core`.

---

### Task 2: Schema migrations

**Files:**
- Create: `internal/migrate/migrate.go`, `internal/migrate/migrations/0001_schema.sql`
- Test: `internal/migrate/migrate_test.go`

- [ ] **Step 1: Add dependencies**

```bash
go get github.com/jackc/pgx/v5@latest github.com/jackc/pgx/v5/pgxpool@latest github.com/pressly/goose/v3@latest
```

- [ ] **Step 2: Write the failing test** — `internal/migrate/migrate_test.go`:

```go
package migrate

import (
	"context"
	"os"
	"testing"

	"github.com/jackc/pgx/v5"
)

func TestUpCreatesTables(t *testing.T) {
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		t.Fatal("TEST_DATABASE_URL not set (see README dev section)")
	}
	if err := Up(url); err != nil {
		t.Fatalf("Up: %v", err)
	}
	conn, err := pgx.Connect(context.Background(), url)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(context.Background())
	for _, tbl := range []string{"accounts", "categories", "transactions", "category_rules", "budgets", "sync_runs"} {
		var n int
		if err := conn.QueryRow(context.Background(),
			`SELECT count(*) FROM information_schema.tables WHERE table_name=$1`, tbl).Scan(&n); err != nil || n != 1 {
			t.Errorf("table %s missing (n=%d err=%v)", tbl, n, err)
		}
	}
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `go test ./internal/migrate/ -run TestUpCreatesTables -v`
Expected: FAIL — `Up` undefined.

- [ ] **Step 4: Write the migration runner** — `internal/migrate/migrate.go`:

```go
// Package migrate applies embedded SQL migrations with goose.
package migrate

import (
	"database/sql"
	"embed"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/pressly/goose/v3"
)

//go:embed migrations/*.sql
var migrations embed.FS

func Up(databaseURL string) error {
	db, err := sql.Open("pgx", databaseURL)
	if err != nil {
		return err
	}
	defer db.Close()
	goose.SetBaseFS(migrations)
	if err := goose.SetDialect("postgres"); err != nil {
		return err
	}
	return goose.Up(db, "migrations")
}
```

- [ ] **Step 5: Write the schema** — `internal/migrate/migrations/0001_schema.sql`:

```sql
-- +goose Up
CREATE TABLE accounts (
    id           text PRIMARY KEY,
    name         text NOT NULL,
    org          text NOT NULL DEFAULT '',
    currency     text NOT NULL DEFAULT 'USD',
    owner        text NOT NULL CHECK (owner IN ('scott','nikki','joint')),
    balance      numeric(14,2),
    balance_date date,
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE categories (
    id        serial PRIMARY KEY,
    name      text NOT NULL UNIQUE,
    parent_id int REFERENCES categories(id),
    kind      text NOT NULL DEFAULT 'spend' CHECK (kind IN ('spend','income','transfer','savings')),
    is_vice   boolean NOT NULL DEFAULT false
);

CREATE TABLE transactions (
    id               bigserial PRIMARY KEY,
    source           text NOT NULL CHECK (source IN ('simplefin','venmo_csv')),
    external_id      text NOT NULL,
    account_id       text NOT NULL REFERENCES accounts(id),
    posted           date NOT NULL,
    amount           numeric(12,2) NOT NULL,
    description      text NOT NULL DEFAULT '',
    payee            text NOT NULL DEFAULT '',
    pending          boolean NOT NULL DEFAULT false,
    category_id      int REFERENCES categories(id),
    owner_override   text CHECK (owner_override IN ('scott','nikki','joint')),
    transfer_peer_id bigint REFERENCES transactions(id),
    raw              jsonb NOT NULL DEFAULT '{}',
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, external_id)
);
CREATE INDEX idx_txn_posted   ON transactions (posted);
CREATE INDEX idx_txn_category ON transactions (category_id);
CREATE INDEX idx_txn_account  ON transactions (account_id);

CREATE TABLE category_rules (
    id          serial PRIMARY KEY,
    priority    int NOT NULL,
    match_type  text NOT NULL DEFAULT 'substring' CHECK (match_type IN ('substring','regex')),
    pattern     text NOT NULL,
    category_id int NOT NULL REFERENCES categories(id)
);

CREATE TABLE budgets (
    category_id int NOT NULL REFERENCES categories(id),
    month       date NOT NULL,
    amount      numeric(12,2) NOT NULL,
    PRIMARY KEY (category_id, month)
);

CREATE TABLE sync_runs (
    id            bigserial PRIMARY KEY,
    kind          text NOT NULL CHECK (kind IN ('simplefin','venmo_csv')),
    started       timestamptz NOT NULL DEFAULT now(),
    finished      timestamptz,
    status        text NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','partial','failed')),
    window_start  date,
    window_end    date,
    rows_upserted int NOT NULL DEFAULT 0,
    detail        text NOT NULL DEFAULT ''
);

-- +goose Down
DROP TABLE sync_runs; DROP TABLE budgets; DROP TABLE category_rules;
DROP TABLE transactions; DROP TABLE categories; DROP TABLE accounts;
```

- [ ] **Step 6: Run test to verify it passes**

Run: `go mod tidy && go test ./internal/migrate/ -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add internal/migrate/ go.mod go.sum
git commit -m "feat: schema migrations (accounts, transactions, categories, rules, budgets, sync_runs)"
```

---

### Task 3: Seed migration (default categories + venmo account)

**Files:**
- Create: `internal/migrate/migrations/0002_seed.sql`
- Modify: `internal/migrate/migrate_test.go`

- [ ] **Step 1: Extend the test** — append to `TestUpCreatesTables` in `internal/migrate/migrate_test.go` (before the closing brace):

```go
	var cats int
	if err := conn.QueryRow(context.Background(), `SELECT count(*) FROM categories`).Scan(&cats); err != nil || cats < 10 {
		t.Errorf("expected seeded categories, got %d (err=%v)", cats, err)
	}
	var venmoOwner string
	if err := conn.QueryRow(context.Background(), `SELECT owner FROM accounts WHERE id='venmo'`).Scan(&venmoOwner); err != nil || venmoOwner != "scott" {
		t.Errorf("venmo account not seeded (owner=%q err=%v)", venmoOwner, err)
	}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/migrate/ -v` → FAIL on the two new assertions.

- [ ] **Step 3: Write the seed** — `internal/migrate/migrations/0002_seed.sql`:

```sql
-- +goose Up
INSERT INTO categories (name, kind, is_vice) VALUES
    ('Housing',            'spend',    false),
    ('Groceries',          'spend',    false),
    ('Dining',             'spend',    true),
    ('Transport',          'spend',    false),
    ('Utilities',          'spend',    false),
    ('Subscriptions',      'spend',    false),
    ('Entertainment',      'spend',    false),
    ('Shopping',           'spend',    false),
    ('Health',             'spend',    false),
    ('Travel',             'spend',    false),
    ('Vices',              'spend',    true),
    ('Paycheck',           'income',   false),
    ('Savings',            'savings',  false),
    ('Transfer',           'transfer', false),
    ('Needs Venmo detail', 'spend',    false);

-- Venmo has no SimpleFIN feed; CSV imports attach to this synthetic account.
INSERT INTO accounts (id, name, org, owner) VALUES ('venmo', 'Venmo', 'Venmo', 'scott');

-- Ally-side Venmo ACH debits: until a CSV pairs them, they are spend in the
-- "needs detail" bucket (spec: totals never understate).
INSERT INTO category_rules (priority, match_type, pattern, category_id)
    SELECT 1000, 'substring', 'VENMO', id FROM categories WHERE name = 'Needs Venmo detail';

-- +goose Down
DELETE FROM category_rules; DELETE FROM accounts WHERE id='venmo'; DELETE FROM categories;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/migrate/ -v` → PASS. (Goose tracks applied versions, so re-runs are no-ops — the test DB accumulates state; the store tests' truncate helper handles that next.)

- [ ] **Step 5: Commit**

```bash
git add internal/migrate/migrations/0002_seed.sql internal/migrate/migrate_test.go
git commit -m "feat: seed default categories, venmo account, VENMO fallback rule"
```

---

### Task 4: Store layer — pool, types, idempotent upserts

**Files:**
- Create: `internal/store/store.go`, `internal/store/testdb_test.go`
- Test: `internal/store/store_test.go`

- [ ] **Step 1: Write the shared test-DB helper** — `internal/store/testdb_test.go`:

```go
package store

import (
	"context"
	"os"
	"testing"

	"github.com/vollminlab/vollmint/internal/migrate"
)

// testDB returns a Store on TEST_DATABASE_URL with migrations applied and
// mutable tables truncated (seed rows in categories/accounts/rules survive
// via re-migration after TRUNCATE ... CASCADE would be complex; instead we
// truncate only transaction-ish tables and restore the venmo account).
func testDB(t *testing.T) *Store {
	t.Helper()
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		t.Fatal("TEST_DATABASE_URL not set (see README dev section)")
	}
	if err := migrate.Up(url); err != nil {
		t.Fatalf("migrate: %v", err)
	}
	s, err := New(context.Background(), url)
	if err != nil {
		t.Fatalf("store.New: %v", err)
	}
	t.Cleanup(s.Close)
	for _, q := range []string{
		`TRUNCATE transactions, sync_runs, budgets RESTART IDENTITY CASCADE`,
		`DELETE FROM accounts WHERE id <> 'venmo'`,
		`DELETE FROM category_rules WHERE priority <> 1000`, // keep only the seed VENMO rule
	} {
		if _, err := s.Pool.Exec(context.Background(), q); err != nil {
			t.Fatalf("reset (%s): %v", q, err)
		}
	}
	return s
}
```

- [ ] **Step 2: Write the failing test** — `internal/store/store_test.go`:

```go
package store

import (
	"context"
	"testing"
	"time"
)

func day(s string) time.Time {
	d, _ := time.Parse("2006-01-02", s)
	return d
}

func TestUpsertTransactionsIsIdempotent(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()

	err := s.UpsertAccounts(ctx, []Account{{ID: "act-1", Name: "Ally Checking", Org: "Ally Bank", Owner: "scott", Balance: "1000.00", BalanceDate: day("2026-07-20")}})
	if err != nil {
		t.Fatalf("UpsertAccounts: %v", err)
	}

	txns := []Txn{
		{Source: "simplefin", ExternalID: "t1", AccountID: "act-1", Posted: day("2026-07-18"), Amount: "-14.62", Description: "CHIPOTLE 2291", Payee: "CHIPOTLE 2291", Raw: []byte(`{"id":"t1"}`)},
		{Source: "simplefin", ExternalID: "t2", AccountID: "act-1", Posted: day("2026-07-19"), Amount: "-41.87", Description: "DOORDASH", Payee: "DOORDASH", Raw: []byte(`{"id":"t2"}`)},
	}
	n, err := s.UpsertTransactions(ctx, txns)
	if err != nil || n != 2 {
		t.Fatalf("first upsert: n=%d err=%v", n, err)
	}

	// Re-upsert with one changed description — must update, never duplicate.
	txns[1].Description = "DOORDASH *LUIGIS"
	if _, err := s.UpsertTransactions(ctx, txns); err != nil {
		t.Fatalf("second upsert: %v", err)
	}
	var count int
	if err := s.Pool.QueryRow(ctx, `SELECT count(*) FROM transactions`).Scan(&count); err != nil || count != 2 {
		t.Fatalf("want 2 rows, got %d (err=%v)", count, err)
	}
	var desc string
	if err := s.Pool.QueryRow(ctx, `SELECT description FROM transactions WHERE external_id='t2'`).Scan(&desc); err != nil || desc != "DOORDASH *LUIGIS" {
		t.Fatalf("update not applied: %q err=%v", desc, err)
	}
}

func TestUpsertRejectsBadAmount(t *testing.T) {
	s := testDB(t)
	_, err := s.UpsertTransactions(context.Background(), []Txn{{Source: "simplefin", ExternalID: "x", AccountID: "venmo", Posted: day("2026-07-01"), Amount: "12.3.4"}})
	if err == nil {
		t.Fatal("expected error for malformed amount")
	}
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `go test ./internal/store/ -v` → FAIL — `New`, `Store`, `Account`, `Txn` undefined.

- [ ] **Step 4: Write the store** — `internal/store/store.go`:

```go
// Package store is the single Postgres access layer for vollmint.
// Amounts are decimal strings end to end — never float64.
package store

import (
	"context"
	"fmt"
	"regexp"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var amountRe = regexp.MustCompile(`^-?\d+(\.\d{1,2})?$`)

type Store struct {
	Pool *pgxpool.Pool
}

func New(ctx context.Context, databaseURL string) (*Store, error) {
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, err
	}
	return &Store{Pool: pool}, nil
}

func (s *Store) Close() { s.Pool.Close() }

type Account struct {
	ID, Name, Org, Currency, Owner string
	Balance                        string // decimal string; "" = unknown
	BalanceDate                    time.Time
}

type Txn struct {
	ID              int64
	Source          string // simplefin | venmo_csv
	ExternalID      string
	AccountID       string
	Posted          time.Time
	Amount          string // decimal string, negative = outflow
	Description     string
	Payee           string
	Pending         bool
	Raw             []byte // json
}

// UpsertAccounts inserts or updates by id. owner is set only on insert —
// the user may reassign owners in the UI and syncs must not clobber that.
func (s *Store) UpsertAccounts(ctx context.Context, accts []Account) error {
	for _, a := range accts {
		if a.Currency == "" {
			a.Currency = "USD"
		}
		var bal any
		if a.Balance != "" {
			if !amountRe.MatchString(a.Balance) {
				return fmt.Errorf("account %s: bad balance %q", a.ID, a.Balance)
			}
			bal = a.Balance
		}
		_, err := s.Pool.Exec(ctx, `
			INSERT INTO accounts (id, name, org, currency, owner, balance, balance_date)
			VALUES ($1,$2,$3,$4,$5,$6,$7)
			ON CONFLICT (id) DO UPDATE SET
			  name=EXCLUDED.name, org=EXCLUDED.org, currency=EXCLUDED.currency,
			  balance=EXCLUDED.balance, balance_date=EXCLUDED.balance_date`,
			a.ID, a.Name, a.Org, a.Currency, a.Owner, bal, nullTime(a.BalanceDate))
		if err != nil {
			return fmt.Errorf("upsert account %s: %w", a.ID, err)
		}
	}
	return nil
}

// UpsertTransactions inserts or updates by (source, external_id) and returns
// the number of rows written. It never deletes and never touches category_id,
// owner_override, or transfer_peer_id on update (user/matcher-owned fields).
func (s *Store) UpsertTransactions(ctx context.Context, txns []Txn) (int, error) {
	batch := &pgx.Batch{}
	for _, t := range txns {
		if !amountRe.MatchString(t.Amount) {
			return 0, fmt.Errorf("txn %s/%s: bad amount %q", t.Source, t.ExternalID, t.Amount)
		}
		raw := t.Raw
		if len(raw) == 0 {
			raw = []byte(`{}`)
		}
		batch.Queue(`
			INSERT INTO transactions (source, external_id, account_id, posted, amount, description, payee, pending, raw)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
			ON CONFLICT (source, external_id) DO UPDATE SET
			  posted=EXCLUDED.posted, amount=EXCLUDED.amount, description=EXCLUDED.description,
			  payee=EXCLUDED.payee, pending=EXCLUDED.pending, raw=EXCLUDED.raw, updated_at=now()`,
			t.Source, t.ExternalID, t.AccountID, t.Posted, t.Amount, t.Description, t.Payee, t.Pending, raw)
	}
	br := s.Pool.SendBatch(ctx, batch)
	defer br.Close()
	n := 0
	for range txns {
		if _, err := br.Exec(); err != nil {
			return n, err
		}
		n++
	}
	return n, nil
}

func nullTime(t time.Time) any {
	if t.IsZero() {
		return nil
	}
	return t
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `go test ./internal/store/ -v` → PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add internal/store/
git commit -m "feat: store layer with idempotent account/transaction upserts"
```

---

### Task 5: SimpleFIN client (claim + accounts)

**Files:**
- Create: `internal/simplefin/client.go`
- Test: `internal/simplefin/client_test.go`

- [ ] **Step 1: Write the failing tests** — `internal/simplefin/client_test.go`:

```go
package simplefin

import (
	"context"
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestClaim(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("claim must POST, got %s", r.Method)
		}
		w.Write([]byte("https://user:pass@bridge.example.com/simplefin"))
	}))
	defer srv.Close()

	setupToken := base64.StdEncoding.EncodeToString([]byte(srv.URL))
	got, err := Claim(setupToken)
	if err != nil {
		t.Fatal(err)
	}
	if got != "https://user:pass@bridge.example.com/simplefin" {
		t.Fatalf("got %q", got)
	}
}

func TestAccounts(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		u, p, ok := r.BasicAuth()
		if !ok || u != "user" || p != "pass" {
			t.Errorf("basic auth not forwarded (ok=%v u=%q)", ok, u)
		}
		if r.URL.Path != "/accounts" {
			t.Errorf("path %q", r.URL.Path)
		}
		q := r.URL.Query()
		if q.Get("start-date") == "" || q.Get("pending") != "1" {
			t.Errorf("missing params: %v", q)
		}
		w.Write([]byte(`{
		  "errors": ["Connection to Fake Bank may need attention"],
		  "accounts": [{
		    "id": "ACT-123", "name": "Checking", "currency": "USD",
		    "balance": "1204.55", "balance-date": 1752969600,
		    "org": {"name": "Ally Bank", "domain": "ally.com"},
		    "transactions": [
		      {"id": "TXN-1", "posted": 1752883200, "amount": "-14.62", "description": "CHIPOTLE 2291", "pending": false}
		    ]
		  }]
		}`))
	}))
	defer srv.Close()

	c := New("https://user:pass@" + srv.Listener.Addr().String())
	c.scheme = "http" // test-only override; real bridge is always https
	set, err := c.Accounts(context.Background(), time.Unix(1750000000, 0), true)
	if err != nil {
		t.Fatal(err)
	}
	if len(set.Errors) != 1 || len(set.Accounts) != 1 {
		t.Fatalf("errors=%v accounts=%d", set.Errors, len(set.Accounts))
	}
	a := set.Accounts[0]
	if a.ID != "ACT-123" || a.Org.Name != "Ally Bank" || a.Balance != "1204.55" {
		t.Fatalf("account parsed wrong: %+v", a)
	}
	if len(a.Transactions) != 1 || a.Transactions[0].Amount != "-14.62" {
		t.Fatalf("txns parsed wrong: %+v", a.Transactions)
	}
	if a.Transactions[0].PostedTime().Format("2006-01-02") != "2026-07-19" {
		t.Fatalf("posted time wrong: %v", a.Transactions[0].PostedTime())
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `go test ./internal/simplefin/ -v` → FAIL — package doesn't exist.

- [ ] **Step 3: Write the client** — `internal/simplefin/client.go`:

```go
// Package simplefin is a minimal SimpleFIN Bridge client.
// Protocol: setup token (base64 claim URL) → POST → Access URL whose embedded
// basic-auth userinfo is the only credential. https://www.simplefin.org/protocol.html
package simplefin

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Org struct {
	Name   string `json:"name"`
	Domain string `json:"domain"`
}

type Transaction struct {
	ID          string `json:"id"`
	Posted      int64  `json:"posted"`
	Amount      string `json:"amount"`
	Description string `json:"description"`
	Pending     bool   `json:"pending"`
}

func (t Transaction) PostedTime() time.Time { return time.Unix(t.Posted, 0).UTC() }

type Account struct {
	ID           string        `json:"id"`
	Name         string        `json:"name"`
	Currency     string        `json:"currency"`
	Balance      string        `json:"balance"`
	BalanceDate  int64         `json:"balance-date"`
	Org          Org           `json:"org"`
	Transactions []Transaction `json:"transactions"`
}

func (a Account) BalanceTime() time.Time { return time.Unix(a.BalanceDate, 0).UTC() }

type AccountSet struct {
	Errors   []string  `json:"errors"`
	Accounts []Account `json:"accounts"`
}

// Claim exchanges a one-time setup token for the Access URL. Call once, save
// the result to 1Password, and never write it to disk.
func Claim(setupToken string) (string, error) {
	claimURL, err := base64.StdEncoding.DecodeString(strings.TrimSpace(setupToken))
	if err != nil {
		return "", fmt.Errorf("setup token is not base64: %w", err)
	}
	resp, err := http.Post(string(claimURL), "application/json", nil)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<16))
	if err != nil {
		return "", err
	}
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("claim failed: %s: %s", resp.Status, body)
	}
	return strings.TrimSpace(string(body)), nil
}

type Client struct {
	user, pass, host, base string
	scheme                 string
	hc                     *http.Client
}

// New parses an Access URL of the form https://user:pass@host/path.
func New(accessURL string) *Client {
	u, err := url.Parse(accessURL)
	if err != nil || u.User == nil {
		// Fail loudly on first request rather than panicking at startup.
		return &Client{scheme: "https", hc: http.DefaultClient}
	}
	pass, _ := u.User.Password()
	return &Client{
		user: u.User.Username(), pass: pass,
		host: u.Host, base: strings.TrimSuffix(u.Path, "/"),
		scheme: "https",
		hc:     &http.Client{Timeout: 60 * time.Second},
	}
}

// Accounts fetches all accounts with transactions posted on/after start.
// SimpleFIN allows at most a 90-day window per request; callers (sync) stay
// far inside that. pending=true includes pending transactions.
func (c *Client) Accounts(ctx context.Context, start time.Time, pending bool) (*AccountSet, error) {
	if c.host == "" {
		return nil, fmt.Errorf("invalid SimpleFIN access URL (missing credentials or host)")
	}
	q := url.Values{"start-date": {fmt.Sprint(start.Unix())}}
	if pending {
		q.Set("pending", "1")
	}
	reqURL := fmt.Sprintf("%s://%s%s/accounts?%s", c.scheme, c.host, c.base, q.Encode())
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, reqURL, nil)
	if err != nil {
		return nil, err
	}
	req.SetBasicAuth(c.user, c.pass)
	resp, err := c.hc.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("simplefin /accounts: %s: %s", resp.Status, body)
	}
	var set AccountSet
	if err := json.NewDecoder(resp.Body).Decode(&set); err != nil {
		return nil, fmt.Errorf("decode /accounts: %w", err)
	}
	return &set, nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `go test ./internal/simplefin/ -v` → PASS.

- [ ] **Step 5: Wire the `claim` subcommand** — in `cmd/vollmint/main.go`, replace the `runClaim` stub:

```go
func runClaim(args []string) error {
	if len(args) != 1 {
		return fmt.Errorf("usage: vollmint claim <setup-token>")
	}
	accessURL, err := simplefin.Claim(args[0])
	if err != nil {
		return err
	}
	fmt.Println(accessURL)
	fmt.Fprintln(os.Stderr, "\nSave this Access URL to 1Password now (item \"SimpleFIN Access URL\", field: token).")
	fmt.Fprintln(os.Stderr, "Do NOT write it to any file. The setup token above is now spent.")
	return nil
}
```
Add `"github.com/vollminlab/vollmint/internal/simplefin"` to the imports.

- [ ] **Step 6: Verify build and commit**

Run: `go build ./... && go test ./... ` → all PASS.

```bash
git add internal/simplefin/ cmd/vollmint/main.go
git commit -m "feat: SimpleFIN client (claim + accounts) and claim subcommand"
```

---

### Task 6: Venmo CSV parser

**Files:**
- Create: `internal/venmo/parser.go`, `internal/venmo/testdata/venmo-2026.csv`
- Test: `internal/venmo/parser_test.go`

- [ ] **Step 1: Create the golden fixture** — `internal/venmo/testdata/venmo-2026.csv` (sanitized, matches Venmo's real export shape: a title line, header row, data rows, trailing summary row):

```csv
Account Statement - (@scott-v) - July 1st to July 20th 2026,,,,,,,,,,,,,,,,,,,,,
,ID,Datetime,Type,Status,Note,From,To,Amount (total),Amount (tip),Amount (tax),Amount (fee),Tax Rate,Tax Exempt,Funding Source,Destination,Beginning Balance,Ending Balance,Statement Period Venmo Fees,Terminal Location,Year to Date Venmo Fees,Disclaimer
,4111111111111111111,2026-07-15T18:22:03,Payment,Complete,Pizza night,Scott Vollmin,Luigi Mario,- $32.00,,,,,,Ally Bank Personal Checking x1234,,,,,Venmo,,
,4222222222222222222,2026-07-12T09:10:44,Payment,Complete,,Scott Vollmin,Coffee Cart,- $7.45,,,,,,Venmo balance,,,,,Venmo,,
,4333333333333333333,2026-07-10T20:05:00,Payment,Complete,Fantasy league,Dave Webb,Scott Vollmin,+ $25.00,,,,,,,Venmo balance,,,,Venmo,,
,,,,,,,,,,,,,,,,$0.00,$18.45,$0.00,,$0.00,In case of errors or questions about your electronic transfers...
```

- [ ] **Step 2: Write the failing test** — `internal/venmo/parser_test.go`:

```go
package venmo

import (
	"os"
	"testing"
)

func TestParseGoldenFile(t *testing.T) {
	f, err := os.Open("testdata/venmo-2026.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()

	txns, err := Parse(f)
	if err != nil {
		t.Fatal(err)
	}
	if len(txns) != 3 {
		t.Fatalf("want 3 txns, got %d", len(txns))
	}

	out := txns[0]
	if out.ExternalID != "4111111111111111111" || out.Amount != "-32.00" ||
		out.Payee != "Luigi Mario" || out.Description != "Pizza night" ||
		out.Posted.Format("2006-01-02") != "2026-07-15" ||
		out.AccountID != "venmo" || out.Source != "venmo_csv" {
		t.Errorf("outgoing parsed wrong: %+v", out)
	}
	if fs := FundingSource(out.Raw); fs != "Ally Bank Personal Checking x1234" {
		t.Errorf("funding source: %q", fs)
	}

	in := txns[2]
	if in.Amount != "25.00" || in.Payee != "Dave Webb" {
		t.Errorf("incoming parsed wrong: %+v", in)
	}
}

func TestParseRejectsMissingColumns(t *testing.T) {
	f, _ := os.CreateTemp(t.TempDir(), "bad*.csv")
	f.WriteString("Nope,Nada\n1,2\n")
	f.Seek(0, 0)
	if _, err := Parse(f); err == nil {
		t.Fatal("expected error for unrecognizable CSV")
	}
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `go test ./internal/venmo/ -v` → FAIL — `Parse` undefined.

- [ ] **Step 4: Write the parser** — `internal/venmo/parser.go`:

```go
// Package venmo parses Venmo's desktop-web CSV statement export.
// The format drifts over time, so columns are located by header NAME, never
// by position. Rows without an ID (title/summary lines) are skipped.
package venmo

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"strings"
	"time"

	"github.com/vollminlab/vollmint/internal/store"
)

var amountClean = regexp.MustCompile(`[^0-9.\-]`)

func Parse(r io.Reader) ([]store.Txn, error) {
	cr := csv.NewReader(r)
	cr.FieldsPerRecord = -1 // Venmo pads rows inconsistently

	var header map[string]int
	var txns []store.Txn
	for {
		rec, err := cr.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("csv read: %w", err)
		}
		if header == nil {
			if idx := indexHeader(rec); idx != nil {
				header = idx
			}
			continue // still hunting for the header row (title lines precede it)
		}
		get := func(col string) string {
			i, ok := header[col]
			if !ok || i >= len(rec) {
				return ""
			}
			return strings.TrimSpace(rec[i])
		}
		id := get("ID")
		if id == "" {
			continue // summary/blank row
		}
		posted, err := time.Parse("2006-01-02T15:04:05", get("Datetime"))
		if err != nil {
			return nil, fmt.Errorf("row %s: bad datetime %q", id, get("Datetime"))
		}
		amount, err := parseAmount(get("Amount (total)"))
		if err != nil {
			return nil, fmt.Errorf("row %s: %w", id, err)
		}
		payee := get("To")
		if !strings.HasPrefix(amount, "-") {
			payee = get("From")
		}
		raw, _ := json.Marshal(map[string]string{
			"type": get("Type"), "status": get("Status"),
			"from": get("From"), "to": get("To"),
			"funding_source": get("Funding Source"), "destination": get("Destination"),
		})
		txns = append(txns, store.Txn{
			Source: "venmo_csv", ExternalID: id, AccountID: "venmo",
			Posted: posted.UTC(), Amount: amount,
			Description: get("Note"), Payee: payee, Raw: raw,
		})
	}
	if header == nil {
		return nil, fmt.Errorf("no Venmo header row found (need ID, Datetime, Amount (total) columns)")
	}
	return txns, nil
}

// indexHeader returns a name→index map if rec looks like the Venmo header row.
func indexHeader(rec []string) map[string]int {
	idx := map[string]int{}
	for i, name := range rec {
		if n := strings.TrimSpace(name); n != "" {
			idx[n] = i
		}
	}
	for _, required := range []string{"ID", "Datetime", "Amount (total)"} {
		if _, ok := idx[required]; !ok {
			return nil
		}
	}
	return idx
}

// parseAmount turns "- $1,234.50" / "+ $25.00" into "-1234.50" / "25.00".
func parseAmount(s string) (string, error) {
	if s == "" {
		return "", fmt.Errorf("empty amount")
	}
	neg := strings.Contains(s, "-")
	cleaned := amountClean.ReplaceAllString(s, "")
	cleaned = strings.TrimPrefix(cleaned, "-")
	if cleaned == "" || strings.Count(cleaned, ".") > 1 {
		return "", fmt.Errorf("unparseable amount %q", s)
	}
	if neg {
		cleaned = "-" + cleaned
	}
	return cleaned, nil
}

// FundingSource extracts the funding source from a parsed row's raw json.
// Diagnostic surface for the UI ("funded by Ally" vs "Venmo balance") —
// the matcher itself pairs purely on amount + date.
func FundingSource(raw []byte) string {
	var m map[string]string
	if json.Unmarshal(raw, &m) != nil {
		return ""
	}
	return m["funding_source"]
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `go test ./internal/venmo/ -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add internal/venmo/
git commit -m "feat: Venmo CSV parser with header-drift tolerance and golden fixture"
```

---

### Task 7: Category rules engine

**Files:**
- Create: `internal/ingest/rules.go`
- Test: `internal/ingest/rules_test.go`

- [ ] **Step 1: Write the failing test** — `internal/ingest/rules_test.go`:

```go
package ingest

import (
	"context"
	"testing"
	"time"

	"github.com/vollminlab/vollmint/internal/store"
)

func day(s string) time.Time {
	d, _ := time.Parse("2006-01-02", s)
	return d
}

func seedTxn(t *testing.T, s *store.Store, extID, desc, amount string) int64 {
	t.Helper()
	_, err := s.UpsertTransactions(context.Background(), []store.Txn{{
		Source: "simplefin", ExternalID: extID, AccountID: "venmo",
		Posted: day("2026-07-10"), Amount: amount, Description: desc, Payee: desc,
	}})
	if err != nil {
		t.Fatal(err)
	}
	var id int64
	if err := s.Pool.QueryRow(context.Background(),
		`SELECT id FROM transactions WHERE source='simplefin' AND external_id=$1`, extID).Scan(&id); err != nil {
		t.Fatal(err)
	}
	return id
}

func TestApplyRulesCategorizesUncategorizedOnly(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()

	var dining, groceries int
	s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Dining'`).Scan(&dining)
	s.Pool.QueryRow(ctx, `SELECT id FROM categories WHERE name='Groceries'`).Scan(&groceries)
	// Lower priority number wins; the seed VENMO rule sits at 1000.
	s.Pool.Exec(ctx, `INSERT INTO category_rules (priority, match_type, pattern, category_id) VALUES
		(10, 'substring', 'chipotle', $1), (20, 'regex', '(?i)^wegmans', $2)`, dining, groceries)

	id1 := seedTxn(t, s, "r1", "CHIPOTLE 2291", "-14.62")
	id2 := seedTxn(t, s, "r2", "WEGMANS #44", "-88.10")
	id3 := seedTxn(t, s, "r3", "VENMO PAYMENT 55", "-32.00")
	id4 := seedTxn(t, s, "r4", "MYSTERY VENDOR", "-5.00")

	// Pre-categorized rows must not be overwritten.
	s.Pool.Exec(ctx, `UPDATE transactions SET category_id=$1 WHERE id=$2`, groceries, id1)

	n, err := ApplyRules(ctx, s)
	if err != nil {
		t.Fatal(err)
	}
	if n != 2 { // r2 (regex) + r3 (seed VENMO rule); r1 already set, r4 no match
		t.Fatalf("want 2 categorized, got %d", n)
	}
	check := func(id int64, want string) {
		var got string
		s.Pool.QueryRow(ctx, `SELECT coalesce(c.name,'') FROM transactions t
			LEFT JOIN categories c ON c.id=t.category_id WHERE t.id=$1`, id).Scan(&got)
		if got != want {
			t.Errorf("txn %d: category %q, want %q", id, got, want)
		}
	}
	check(id1, "Groceries")          // untouched
	check(id2, "Groceries")          // regex rule
	check(id3, "Needs Venmo detail") // seed VENMO rule
	check(id4, "")                   // uncategorized queue
}
```

Also create `internal/ingest/testdb_test.go` with the identical `testDB` helper from Task 4 Step 1, but in `package ingest` and returning `*store.Store` (imports `store` and `migrate`; same body, with `s.Pool` reachable because `Pool` is exported).

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/ingest/ -run TestApplyRules -v` → FAIL — `ApplyRules` undefined.

- [ ] **Step 3: Write the rules engine** — `internal/ingest/rules.go`:

```go
// Package ingest holds post-ingestion enrichment: category rules, transfer
// matching, and the sync orchestration.
package ingest

import (
	"context"
	"fmt"
	"regexp"
	"strings"

	"github.com/vollminlab/vollmint/internal/store"
)

type rule struct {
	matchType, pattern string
	categoryID         int
	re                 *regexp.Regexp
}

// ApplyRules assigns categories to uncategorized transactions. First matching
// rule wins (priority ASC, id ASC). Substring matches are case-insensitive
// against payee + description. Returns rows categorized.
func ApplyRules(ctx context.Context, s *store.Store) (int, error) {
	rows, err := s.Pool.Query(ctx,
		`SELECT match_type, pattern, category_id FROM category_rules ORDER BY priority, id`)
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	var rules []rule
	for rows.Next() {
		var r rule
		if err := rows.Scan(&r.matchType, &r.pattern, &r.categoryID); err != nil {
			return 0, err
		}
		if r.matchType == "regex" {
			re, err := regexp.Compile(r.pattern)
			if err != nil {
				return 0, fmt.Errorf("rule %q: %w", r.pattern, err)
			}
			r.re = re
		}
		rules = append(rules, r)
	}
	if err := rows.Err(); err != nil {
		return 0, err
	}

	txRows, err := s.Pool.Query(ctx,
		`SELECT id, payee, description FROM transactions WHERE category_id IS NULL`)
	if err != nil {
		return 0, err
	}
	defer txRows.Close()
	type match struct {
		id  int64
		cat int
	}
	var matches []match
	for txRows.Next() {
		var id int64
		var payee, desc string
		if err := txRows.Scan(&id, &payee, &desc); err != nil {
			return 0, err
		}
		haystack := strings.ToLower(payee + " " + desc)
		for _, r := range rules {
			hit := false
			if r.re != nil {
				hit = r.re.MatchString(payee) || r.re.MatchString(desc)
			} else {
				hit = strings.Contains(haystack, strings.ToLower(r.pattern))
			}
			if hit {
				matches = append(matches, match{id, r.categoryID})
				break
			}
		}
	}
	if err := txRows.Err(); err != nil {
		return 0, err
	}
	for _, m := range matches {
		if _, err := s.Pool.Exec(ctx,
			`UPDATE transactions SET category_id=$1, updated_at=now() WHERE id=$2 AND category_id IS NULL`,
			m.cat, m.id); err != nil {
			return 0, err
		}
	}
	return len(matches), nil
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/ingest/ -run TestApplyRules -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/ingest/
git commit -m "feat: priority-ordered category rules engine (substring + regex)"
```

---

### Task 8: Transfer matcher

**Files:**
- Create: `internal/ingest/matcher.go`
- Test: `internal/ingest/matcher_test.go`

Spec rules implemented here: (1) bank "VENMO" debits pair with `venmo_csv` rows by equal amount within ±3 days → bank side becomes Transfer, Venmo side keeps its category; (2) checking↔card payment pairs (equal magnitude, opposite sign, ±5 days, both `simplefin`, different accounts, description matches a payment descriptor) → both sides Transfer. Already-paired rows are never re-paired; each row pairs at most once.

- [ ] **Step 1: Write the failing test** — `internal/ingest/matcher_test.go`:

```go
package ingest

import (
	"context"
	"testing"

	"github.com/vollminlab/vollmint/internal/store"
)

func seedAccount(t *testing.T, s *store.Store, id, owner string) {
	t.Helper()
	if err := s.UpsertAccounts(context.Background(),
		[]store.Account{{ID: id, Name: id, Org: "test", Owner: owner}}); err != nil {
		t.Fatal(err)
	}
}

func seedFull(t *testing.T, s *store.Store, source, extID, acct, posted, amount, desc string) int64 {
	t.Helper()
	_, err := s.UpsertTransactions(context.Background(), []store.Txn{{
		Source: source, ExternalID: extID, AccountID: acct,
		Posted: day(posted), Amount: amount, Description: desc, Payee: desc,
	}})
	if err != nil {
		t.Fatal(err)
	}
	var id int64
	s.Pool.QueryRow(context.Background(),
		`SELECT id FROM transactions WHERE source=$1 AND external_id=$2`, source, extID).Scan(&id)
	return id
}

func categoryOf(t *testing.T, s *store.Store, id int64) string {
	t.Helper()
	var name string
	s.Pool.QueryRow(context.Background(), `SELECT coalesce(c.name,'') FROM transactions t
		LEFT JOIN categories c ON c.id=t.category_id WHERE t.id=$1`, id).Scan(&name)
	return name
}

func peerOf(t *testing.T, s *store.Store, id int64) int64 {
	t.Helper()
	var peer *int64
	s.Pool.QueryRow(context.Background(),
		`SELECT transfer_peer_id FROM transactions WHERE id=$1`, id).Scan(&peer)
	if peer == nil {
		return 0
	}
	return *peer
}

func TestMatchVenmoPairs(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	seedAccount(t, s, "ally", "scott")

	bank := seedFull(t, s, "simplefin", "b1", "ally", "2026-07-16", "-32.00", "VENMO PAYMENT 4111")
	venmo := seedFull(t, s, "venmo_csv", "v1", "venmo", "2026-07-15", "-32.00", "Pizza night")
	lonely := seedFull(t, s, "simplefin", "b2", "ally", "2026-07-01", "-99.00", "VENMO PAYMENT 9999")

	n, err := MatchTransfers(ctx, s)
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("want 1 pair, got %d", n)
	}
	if peerOf(t, s, bank) != venmo || peerOf(t, s, venmo) != bank {
		t.Error("peer ids not linked both ways")
	}
	if categoryOf(t, s, bank) != "Transfer" {
		t.Errorf("bank side category = %q, want Transfer", categoryOf(t, s, bank))
	}
	if categoryOf(t, s, venmo) == "Transfer" {
		t.Error("venmo side must keep its own category (it carries the spend)")
	}
	if peerOf(t, s, lonely) != 0 {
		t.Error("unmatched VENMO debit must stay unpaired (counted as spend)")
	}

	// Idempotency: second run pairs nothing new.
	if n2, _ := MatchTransfers(ctx, s); n2 != 0 {
		t.Fatalf("second run paired %d, want 0", n2)
	}
}

func TestMatchCardPaymentPairs(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	seedAccount(t, s, "chase-checking", "joint")
	seedAccount(t, s, "discover-card", "scott")

	out := seedFull(t, s, "simplefin", "c1", "chase-checking", "2026-07-10", "-500.00", "DISCOVER E-PAYMENT 1234")
	in := seedFull(t, s, "simplefin", "c2", "discover-card", "2026-07-12", "500.00", "DIRECTPAY PAYMENT THANK YOU")
	spend := seedFull(t, s, "simplefin", "c3", "discover-card", "2026-07-11", "-500.00", "BEST BUY 500")

	n, err := MatchTransfers(ctx, s)
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("want 1 pair, got %d", n)
	}
	if peerOf(t, s, out) != in || peerOf(t, s, in) != out {
		t.Error("card payment pair not linked")
	}
	if categoryOf(t, s, out) != "Transfer" || categoryOf(t, s, in) != "Transfer" {
		t.Error("both card-payment sides must be Transfer")
	}
	if peerOf(t, s, spend) != 0 || categoryOf(t, s, spend) == "Transfer" {
		t.Error("ordinary card spend must not be swept into a transfer pair")
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `go test ./internal/ingest/ -run TestMatch -v` → FAIL — `MatchTransfers` undefined.

- [ ] **Step 3: Write the matcher** — `internal/ingest/matcher.go`:

```go
package ingest

import (
	"context"
	"regexp"

	"github.com/vollminlab/vollmint/internal/store"
)

// cardPaymentRe covers the payment descriptors of the household's issuers
// (Chase, Discover) plus generic autopay wording. Ordinary purchases never
// match these; extend the list if a new issuer joins.
var cardPaymentRe = regexp.MustCompile(`(?i)(E-PAYMENT|EPAYMENT|AUTOPAY|CARD ?PYMT|CRD PMT|PAYMENT THANK YOU|CHASE CREDIT CRD|DISCOVER +PAYMENT)`)

var venmoRe = regexp.MustCompile(`(?i)VENMO`)

// MatchTransfers pairs (a) bank-side VENMO debits with venmo_csv rows and
// (b) checking↔card payment legs. Runs inside one DB transaction; each row
// pairs at most once; returns the number of new pairs.
func MatchTransfers(ctx context.Context, s *store.Store) (int, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx)

	var transferCat int
	if err := tx.QueryRow(ctx,
		`SELECT id FROM categories WHERE name='Transfer'`).Scan(&transferCat); err != nil {
		return 0, err
	}

	pairs := 0

	// (a) Venmo: bank debit ←→ venmo_csv row, equal amount, ±3 days.
	// Only the bank side becomes Transfer; the venmo side carries the spend.
	rows, err := tx.Query(ctx, `
		SELECT b.id, v.id FROM transactions b
		JOIN LATERAL (
		  SELECT id FROM transactions v
		  WHERE v.source='venmo_csv' AND v.transfer_peer_id IS NULL
		    AND v.amount = b.amount
		    AND v.posted BETWEEN b.posted - 3 AND b.posted + 3
		  ORDER BY abs(v.posted - b.posted), v.id LIMIT 1
		) v ON true
		WHERE b.source='simplefin' AND b.transfer_peer_id IS NULL
		  AND b.amount < 0 AND b.description ~* 'VENMO'
		ORDER BY b.id`)
	if err != nil {
		return 0, err
	}
	type pair struct{ a, b int64 }
	var venmoPairs []pair
	taken := map[int64]bool{}
	for rows.Next() {
		var p pair
		if err := rows.Scan(&p.a, &p.b); err != nil {
			rows.Close()
			return 0, err
		}
		if !taken[p.b] { // a venmo row can satisfy only one bank debit
			taken[p.b] = true
			venmoPairs = append(venmoPairs, p)
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}
	for _, p := range venmoPairs {
		if _, err := tx.Exec(ctx, `UPDATE transactions
			SET transfer_peer_id=$1, category_id=$2, updated_at=now() WHERE id=$3`,
			p.b, transferCat, p.a); err != nil {
			return 0, err
		}
		if _, err := tx.Exec(ctx, `UPDATE transactions
			SET transfer_peer_id=$1, updated_at=now() WHERE id=$2`, p.a, p.b); err != nil {
			return 0, err
		}
		pairs++
	}

	// (b) Card payments: negative leg + positive leg, equal magnitude,
	// different simplefin accounts, ±5 days, payment-descriptor on either leg.
	rows, err = tx.Query(ctx, `
		SELECT o.id, i.id, o.description, i.description FROM transactions o
		JOIN LATERAL (
		  SELECT id, description FROM transactions i
		  WHERE i.source='simplefin' AND i.transfer_peer_id IS NULL
		    AND i.account_id <> o.account_id
		    AND i.amount = -o.amount AND i.amount > 0
		    AND i.posted BETWEEN o.posted - 5 AND o.posted + 5
		  ORDER BY abs(i.posted - o.posted), i.id LIMIT 1
		) i ON true
		WHERE o.source='simplefin' AND o.transfer_peer_id IS NULL AND o.amount < 0
		ORDER BY o.id`)
	if err != nil {
		return 0, err
	}
	var cardPairs []pair
	takenIn := map[int64]bool{}
	for rows.Next() {
		var p pair
		var descO, descI string
		if err := rows.Scan(&p.a, &p.b, &descO, &descI); err != nil {
			rows.Close()
			return 0, err
		}
		// Require a payment descriptor and exclude Venmo legs (handled above).
		if takenIn[p.b] || venmoRe.MatchString(descO) {
			continue
		}
		if cardPaymentRe.MatchString(descO) || cardPaymentRe.MatchString(descI) {
			takenIn[p.b] = true
			cardPairs = append(cardPairs, p)
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}
	for _, p := range cardPairs {
		for _, upd := range []struct{ id, peer int64 }{{p.a, p.b}, {p.b, p.a}} {
			if _, err := tx.Exec(ctx, `UPDATE transactions
				SET transfer_peer_id=$1, category_id=$2, updated_at=now() WHERE id=$3`,
				upd.peer, transferCat, upd.id); err != nil {
				return 0, err
			}
		}
		pairs++
	}

	return pairs, tx.Commit(ctx)
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `go test ./internal/ingest/ -run TestMatch -v` → PASS both.

Note: `v.posted BETWEEN b.posted - 3 AND b.posted + 3` works because `posted` is a `date` — integer day arithmetic is valid Postgres. If the test errors on the date arithmetic, the columns were created as timestamptz — fix the schema, not the query.

- [ ] **Step 5: Run the FULL ingest suite (rules + matcher interact)**

Run: `go test ./internal/ingest/ -v` → all PASS.

- [ ] **Step 6: Commit**

```bash
git add internal/ingest/matcher.go internal/ingest/matcher_test.go
git commit -m "feat: transfer matcher for Venmo funding debits and card payments"
```

---

### Task 9: Sync orchestration + pending sweep

**Files:**
- Create: `internal/ingest/sync.go`
- Test: `internal/ingest/sync_test.go`
- Modify: `cmd/vollmint/main.go` (replace `runSync` stub)

- [ ] **Step 1: Write the failing test** — `internal/ingest/sync_test.go`:

```go
package ingest

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/vollminlab/vollmint/internal/simplefin"
)

func fakeBridge(t *testing.T, body string) *simplefin.Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, body)
	}))
	t.Cleanup(srv.Close)
	c := simplefin.New("https://u:p@" + srv.Listener.Addr().String())
	simplefin.ForceHTTP(c) // test hook, added in Step 3
	return c
}

func TestSyncEndToEnd(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	posted := time.Now().AddDate(0, 0, -2).Unix()
	c := fakeBridge(t, fmt.Sprintf(`{"errors":[],"accounts":[{
		"id":"ally-1","name":"Ally Checking","currency":"USD",
		"balance":"900.00","balance-date":%d,
		"org":{"name":"Ally Bank","domain":"ally.com"},
		"transactions":[
		 {"id":"s1","posted":%d,"amount":"-14.62","description":"CHIPOTLE 2291","pending":false},
		 {"id":"s2","posted":%d,"amount":"-32.00","description":"VENMO PAYMENT 4111","pending":false}
		]}]}`, posted, posted, posted))

	res, err := Sync(ctx, s, c, "scott")
	if err != nil {
		t.Fatal(err)
	}
	if res.Upserted != 2 {
		t.Fatalf("upserted=%d want 2", res.Upserted)
	}

	// sync_runs row recorded as ok
	var status string
	var rowsUp int
	if err := s.Pool.QueryRow(ctx,
		`SELECT status, rows_upserted FROM sync_runs ORDER BY id DESC LIMIT 1`).Scan(&status, &rowsUp); err != nil {
		t.Fatal(err)
	}
	if status != "ok" || rowsUp != 2 {
		t.Fatalf("sync_runs: status=%q rows=%d", status, rowsUp)
	}

	// rules ran: VENMO txn landed in the needs-detail bucket
	var cat string
	s.Pool.QueryRow(ctx, `SELECT coalesce(c.name,'') FROM transactions t
		LEFT JOIN categories c ON c.id=t.category_id
		WHERE t.external_id='s2'`).Scan(&cat)
	if cat != "Needs Venmo detail" {
		t.Fatalf("VENMO txn category=%q", cat)
	}

	// new account defaulted to the fallback owner
	var owner string
	s.Pool.QueryRow(ctx, `SELECT owner FROM accounts WHERE id='ally-1'`).Scan(&owner)
	if owner != "scott" {
		t.Fatalf("owner=%q", owner)
	}
}

func TestSyncRecordsFailure(t *testing.T) {
	s := testDB(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "Payment Required", http.StatusPaymentRequired)
	}))
	defer srv.Close()
	c := simplefin.New("https://u:p@" + srv.Listener.Addr().String())
	simplefin.ForceHTTP(c)

	if _, err := Sync(context.Background(), s, c, "scott"); err == nil {
		t.Fatal("want error on 402")
	}
	var status, detail string
	s.Pool.QueryRow(context.Background(),
		`SELECT status, detail FROM sync_runs ORDER BY id DESC LIMIT 1`).Scan(&status, &detail)
	if status != "failed" || detail == "" {
		t.Fatalf("failure not recorded: status=%q detail=%q", status, detail)
	}
}

func TestSweepStalePending(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()
	seedAccount(t, s, "ally", "scott")
	s.Pool.Exec(ctx, `INSERT INTO transactions (source, external_id, account_id, posted, amount, pending, updated_at)
		VALUES ('simplefin','old-pend','ally', current_date - 30, '-5.00', true, now() - interval '20 days'),
		       ('simplefin','new-pend','ally', current_date - 1,  '-6.00', true, now())`)
	n, err := SweepStalePending(ctx, s, 14)
	if err != nil || n != 1 {
		t.Fatalf("swept=%d err=%v (want 1)", n, err)
	}
	var count int
	s.Pool.QueryRow(ctx, `SELECT count(*) FROM transactions WHERE pending`).Scan(&count)
	if count != 1 {
		t.Fatalf("remaining pending=%d want 1", count)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `go test ./internal/ingest/ -run 'TestSync|TestSweep' -v` → FAIL — `Sync`, `SweepStalePending`, `simplefin.ForceHTTP` undefined.

- [ ] **Step 3: Add the test hook to the client** — append to `internal/simplefin/client.go`:

```go
// ForceHTTP downgrades the client to plain http. Test servers only —
// production access URLs are always https.
func ForceHTTP(c *Client) { c.scheme = "http" }
```

- [ ] **Step 4: Write the orchestration** — `internal/ingest/sync.go`:

```go
package ingest

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/vollminlab/vollmint/internal/simplefin"
	"github.com/vollminlab/vollmint/internal/store"
)

type SyncResult struct {
	Upserted, Categorized, Paired, Swept int
}

// Sync runs one SimpleFIN pull: fetch → upsert accounts+txns → rules →
// transfer matching → pending sweep, recording a sync_runs row either way.
// Window = last successful simplefin run − 7 days (self-healing overlap);
// first run backfills 85 days (SimpleFIN caps a request at 90).
// defaultOwner is assigned to accounts on first sight only (spec: the UI owns
// owner assignment afterwards).
func Sync(ctx context.Context, s *store.Store, c *simplefin.Client, defaultOwner string) (*SyncResult, error) {
	var runID int64
	start := windowStart(ctx, s)
	if err := s.Pool.QueryRow(ctx, `INSERT INTO sync_runs (kind, window_start, window_end)
		VALUES ('simplefin', $1, current_date) RETURNING id`, start).Scan(&runID); err != nil {
		return nil, err
	}
	fail := func(err error) (*SyncResult, error) {
		s.Pool.Exec(ctx, `UPDATE sync_runs SET status='failed', finished=now(), detail=$1 WHERE id=$2`,
			err.Error(), runID)
		return nil, err
	}

	set, err := c.Accounts(ctx, start, true)
	if err != nil {
		return fail(fmt.Errorf("simplefin fetch: %w", err))
	}

	res := &SyncResult{}
	var accts []store.Account
	var txns []store.Txn
	for _, a := range set.Accounts {
		accts = append(accts, store.Account{
			ID: a.ID, Name: a.Name, Org: a.Org.Name, Currency: a.Currency,
			Owner: defaultOwner, Balance: a.Balance, BalanceDate: a.BalanceTime(),
		})
		for _, tr := range a.Transactions {
			raw, _ := json.Marshal(tr)
			txns = append(txns, store.Txn{
				Source: "simplefin", ExternalID: tr.ID, AccountID: a.ID,
				Posted: tr.PostedTime(), Amount: tr.Amount,
				Description: tr.Description, Payee: tr.Description,
				Pending: tr.Pending, Raw: raw,
			})
		}
	}
	if err := s.UpsertAccounts(ctx, accts); err != nil {
		return fail(err)
	}
	if res.Upserted, err = s.UpsertTransactions(ctx, txns); err != nil {
		return fail(err)
	}
	if res.Categorized, err = ApplyRules(ctx, s); err != nil {
		return fail(err)
	}
	if res.Paired, err = MatchTransfers(ctx, s); err != nil {
		return fail(err)
	}
	if res.Swept, err = SweepStalePending(ctx, s, 14); err != nil {
		return fail(err)
	}

	status := "ok"
	detail := ""
	if len(set.Errors) > 0 {
		// Institution-level warnings (e.g. one bank needs re-auth): the run
		// still succeeded, but surface them.
		status = "partial"
		detail = strings.Join(set.Errors, "; ")
	}
	_, err = s.Pool.Exec(ctx, `UPDATE sync_runs
		SET status=$1, finished=now(), rows_upserted=$2, detail=$3 WHERE id=$4`,
		status, res.Upserted, detail, runID)
	return res, err
}

// windowStart returns (last successful sync − 7d), or −85d on first run.
func windowStart(ctx context.Context, s *store.Store) time.Time {
	var last *time.Time
	s.Pool.QueryRow(ctx, `SELECT max(started) FROM sync_runs
		WHERE kind='simplefin' AND status IN ('ok','partial')`).Scan(&last)
	if last == nil {
		return time.Now().UTC().AddDate(0, 0, -85)
	}
	return last.UTC().AddDate(0, 0, -7)
}

// SweepStalePending deletes pending rows untouched for staleDays — their
// posted replacement arrived under a new id via the overlap window. This is
// the single deliberate exception to "ingestion never deletes": pending rows
// are provisional by definition.
func SweepStalePending(ctx context.Context, s *store.Store, staleDays int) (int, error) {
	tag, err := s.Pool.Exec(ctx, `DELETE FROM transactions
		WHERE pending AND transfer_peer_id IS NULL
		  AND updated_at < now() - make_interval(days => $1)`, staleDays)
	if err != nil {
		return 0, err
	}
	return int(tag.RowsAffected()), nil
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `go test ./internal/ingest/ -v` → all PASS.

- [ ] **Step 6: Wire the `sync` subcommand** — in `cmd/vollmint/main.go`, replace the `runSync` stub:

```go
func runSync(args []string) error {
	dbURL := os.Getenv("DATABASE_URL")
	accessURL := os.Getenv("SIMPLEFIN_ACCESS_URL")
	if dbURL == "" || accessURL == "" {
		return fmt.Errorf("DATABASE_URL and SIMPLEFIN_ACCESS_URL are required")
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
	res, err := ingest.Sync(ctx, s, simplefin.New(accessURL), "scott")
	if err != nil {
		return err
	}
	fmt.Printf("sync ok: upserted=%d categorized=%d paired=%d swept=%d\n",
		res.Upserted, res.Categorized, res.Paired, res.Swept)
	return nil
}
```
Add `"context"`, `"github.com/vollminlab/vollmint/internal/ingest"`, `"github.com/vollminlab/vollmint/internal/migrate"`, `"github.com/vollminlab/vollmint/internal/store"` to imports.

- [ ] **Step 7: Verify full build + suite, commit**

Run: `go build ./... && go test ./...` → all PASS.

```bash
git add internal/ingest/sync.go internal/ingest/sync_test.go internal/simplefin/client.go cmd/vollmint/main.go
git commit -m "feat: sync orchestration with sync_runs audit, overlap window, pending sweep"
```

---

### Task 10: `import-venmo` subcommand

**Files:**
- Create: `internal/ingest/importvenmo.go`
- Test: `internal/ingest/importvenmo_test.go`
- Modify: `cmd/vollmint/main.go` (replace `runImportVenmo` stub)

- [ ] **Step 1: Write the failing test** — `internal/ingest/importvenmo_test.go`:

```go
package ingest

import (
	"context"
	"os"
	"testing"
)

func TestImportVenmoFileTwiceIsIdempotent(t *testing.T) {
	s := testDB(t)
	ctx := context.Background()

	f, err := os.Open("../venmo/testdata/venmo-2026.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()

	res, err := ImportVenmo(ctx, s, f)
	if err != nil {
		t.Fatal(err)
	}
	if res.Upserted != 3 {
		t.Fatalf("first import upserted=%d want 3", res.Upserted)
	}

	f2, _ := os.Open("../venmo/testdata/venmo-2026.csv")
	defer f2.Close()
	res2, err := ImportVenmo(ctx, s, f2)
	if err != nil {
		t.Fatal(err)
	}
	var count int
	s.Pool.QueryRow(ctx, `SELECT count(*) FROM transactions WHERE source='venmo_csv'`).Scan(&count)
	if count != 3 {
		t.Fatalf("re-import duplicated rows: count=%d (second res=%+v)", count, res2)
	}

	// audit row written
	var kind, status string
	s.Pool.QueryRow(ctx, `SELECT kind, status FROM sync_runs ORDER BY id DESC LIMIT 1`).Scan(&kind, &status)
	if kind != "venmo_csv" || status != "ok" {
		t.Fatalf("sync_runs: kind=%q status=%q", kind, status)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/ingest/ -run TestImportVenmo -v` → FAIL — `ImportVenmo` undefined.

- [ ] **Step 3: Write the importer** — `internal/ingest/importvenmo.go`:

```go
package ingest

import (
	"context"
	"fmt"
	"io"

	"github.com/vollminlab/vollmint/internal/store"
	"github.com/vollminlab/vollmint/internal/venmo"
)

// ImportVenmo parses one Venmo CSV export and upserts its rows, then runs
// rules + transfer matching so freshly imported rows pair with any waiting
// bank-side VENMO debits. The CSV itself is never persisted (spec).
func ImportVenmo(ctx context.Context, s *store.Store, r io.Reader) (*SyncResult, error) {
	var runID int64
	if err := s.Pool.QueryRow(ctx,
		`INSERT INTO sync_runs (kind) VALUES ('venmo_csv') RETURNING id`).Scan(&runID); err != nil {
		return nil, err
	}
	fail := func(err error) (*SyncResult, error) {
		s.Pool.Exec(ctx, `UPDATE sync_runs SET status='failed', finished=now(), detail=$1 WHERE id=$2`,
			err.Error(), runID)
		return nil, err
	}

	txns, err := venmo.Parse(r)
	if err != nil {
		return fail(fmt.Errorf("parse venmo csv: %w", err))
	}
	res := &SyncResult{}
	if res.Upserted, err = s.UpsertTransactions(ctx, txns); err != nil {
		return fail(err)
	}
	if res.Categorized, err = ApplyRules(ctx, s); err != nil {
		return fail(err)
	}
	if res.Paired, err = MatchTransfers(ctx, s); err != nil {
		return fail(err)
	}
	_, err = s.Pool.Exec(ctx, `UPDATE sync_runs
		SET status='ok', finished=now(), rows_upserted=$1 WHERE id=$2`, res.Upserted, runID)
	return res, err
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/ingest/ -run TestImportVenmo -v` → PASS.

- [ ] **Step 5: Wire the subcommand** — in `cmd/vollmint/main.go`, replace the `runImportVenmo` stub:

```go
func runImportVenmo(args []string) error {
	if len(args) != 1 {
		return fmt.Errorf("usage: vollmint import-venmo <statement.csv>")
	}
	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		return fmt.Errorf("DATABASE_URL is required")
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
	f, err := os.Open(args[0])
	if err != nil {
		return err
	}
	defer f.Close()
	res, err := ingest.ImportVenmo(ctx, s, f)
	if err != nil {
		return err
	}
	fmt.Printf("import ok: upserted=%d categorized=%d paired=%d\n",
		res.Upserted, res.Categorized, res.Paired)
	fmt.Fprintln(os.Stderr, "Reminder: delete the CSV export when done — it is not retained by vollmint.")
	return nil
}
```

- [ ] **Step 6: Verify full suite, commit**

Run: `go build ./... && go test ./...` → all PASS.

```bash
git add internal/ingest/importvenmo.go internal/ingest/importvenmo_test.go cmd/vollmint/main.go
git commit -m "feat: import-venmo subcommand with idempotent re-import"
```

---

### Task 11: Finish the branch

- [ ] **Step 1: Full verification**

```bash
go vet ./... && go test ./... && go build ./...
```
Expected: everything green.

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin feat/backend-core
gh pr create --title "feat: backend core (schema, SimpleFIN, Venmo CSV, rules, matcher, sync)" \
  --body "Implements plan 1 of 3 (vollmint-backend). Spec: k8s-vollminlab-cluster/docs/superpowers/specs/vollmint-design.md"
```

- [ ] **Step 3: STOP — do not merge.** Merging requires Scott's explicit approval (house rule). Report the PR URL and the test summary.

---

## Verification checklist (spec ↔ plan)

- Idempotent ingestion via `UNIQUE (source, external_id)` — Tasks 2, 4, 10
- Amounts as decimal strings, never floats — Tasks 4, 5, 6
- Category rules (substring + regex, priority, first-match, uncategorized-only) — Task 7
- Ally↔Venmo pairing, bank side → Transfer, unmatched stays spend in "Needs Venmo detail" — Tasks 3, 8
- Card-payment pairing, both sides → Transfer — Task 8
- 7-day overlap window, 85-day first backfill (< 90-day cap) — Task 9
- Pending sweep after 14 days (the one sanctioned delete) — Task 9
- sync_runs audit on success/partial/failure incl. 402 detail — Tasks 9, 10
- CSV never persisted; testdata is sanitized fixtures only — Tasks 1, 6, 10
- Owner set on first sight only, UI owns it after — Tasks 4, 9
- NOT here (deliberate): HTTP API + recurring detection (plan 2); Dockerfile/chart/CI/cluster manifests (plan 3)
