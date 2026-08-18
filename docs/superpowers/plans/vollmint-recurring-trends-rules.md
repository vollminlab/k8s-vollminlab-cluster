# vollmint Plan 4 — Recurring Page, Spending Trends, Rules Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three UI surfaces to vollmint: a Recurring charges page (backend already shipped), a Spending-over-time Trends page (new backend aggregation + first Recharts chart), and a Rules management page (backend already shipped).

**Architecture:** One new read-only aggregation (`report.MonthlyFlow`) + one new endpoint (`GET /api/trends`) on the Go side; three new React pages + a `q` search filter on the existing Transactions page, wired into the existing App/Nav shell. Everything else reuses shipped, tested backends (`GET /api/recurring`, `GET/POST/DELETE /api/rules`).

**Tech Stack:** Go 1.26 stdlib ServeMux + pgx v5 (hand-written SQL), React 18 + TypeScript strict + Vite, react-router-dom v6, Recharts 2.12 (already in package.json, first actual use), Vitest + React Testing Library (jsdom).

**Date:** 2026-07-28 (date lives here, never in the filename)

---

## Context — read before Task 1

**Repo:** `/home/vollmin/repos/vollminlab/vollmint` (NOT the k8s repo this plan file lives in).

**Worktree:** Use the `using-git-worktrees` skill to create a worktree on branch `feat/recurring-trends-rules` before any edit. Never work on `main`; never push to `main`; the finished branch becomes a PR that **Scott merges — never the agent**.

**Environment setup (every shell):**

```bash
export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"   # go is not on PATH by default
```

**Go tests need a live Postgres** (dev instance on port 5433; see `docs/development.md` in the vollmint repo if it is not running):

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' go test ./...
```

**Frontend tests:**

```bash
cd web && npm test        # vitest run
```

**House rules that apply to every task:**

- Money is a decimal **string** end-to-end. SQL casts with `::text`. `Number()` is allowed ONLY for chart pixel geometry; labels/tooltips go through `money()` from `web/src/format.ts`.
- API responses are single-key envelopes (`{"trends":[...]}`); errors are `{"error":"msg"}`; handlers never emit `null` arrays.
- No auth code anywhere — Authentik forward-auth upstream covers every new endpoint automatically.
- TypeScript strict mode has `noUnusedLocals`/`noUnusedParameters` on — an unused import fails the build.
- Interactive controls need `aria-label`s; tests query by accessible name.
- Commit messages end with the two standard trailer lines (Co-Authored-By + Claude-Session) used throughout this session.

**Spec deviations (deliberate — from `docs/superpowers/specs/vollmint-design.md`):**

1. The spec placed recurring charges as a *dashboard panel*; plan-4 ships it as a standalone page (Scott's explicit request). The "no number is a dead end" spec rule is honored by deep-linking every recurring row into Transactions.
2. The spec's rules UX was an inline "always categorize X as Y" affordance; a dedicated Rules page is an addition (also Scott's explicit request).
3. Time-series graphs are not in the spec at all — new scope requested for plan-4.

**Recurring deep-link detail:** recurring rows carry no `category_id`, so the link uses the free-text filter: `/transactions?view=<view>&month=<month>&q=<payee>`. `store.ListTransactions` already supports `q` (payee/description ILIKE) and the ts client `TxnFilter` already has `q?: string` — only the Transactions *page* must learn to read `q` from the URL (Task 3).

**Rules engine facts (surface these in the Rules page UI copy):** first match wins, ordered by `priority ASC, id ASC`; case-insensitive substring against payee+description. Priority scheme in production data: 100 = money movement, 400 = disambiguators, 500 = merchants (default for new rules), 1000 = seed VENMO fallback. Lower number wins. There is **no PATCH/PUT** for rules — editing = delete + recreate.

---

## File Structure

```
vollmint/
  internal/report/report.go            # MODIFY — add MonthFlow type + MonthlyFlow()
  internal/report/report_test.go       # MODIFY — add TestMonthlyFlow, TestMonthlyFlowViewFilter
  internal/api/trends.go               # CREATE — GET /api/trends handler
  internal/api/trends_test.go          # CREATE — handler tests
  internal/api/server.go               # MODIFY — register registerTrends() in routes()
  web/src/api.ts                       # MODIFY — TrendPoint interface + getTrends()
  web/src/components/Transactions.tsx  # MODIFY — read q param, pass to API, show filter line
  web/src/components/Transactions.test.tsx  # MODIFY — add q-filter test
  web/src/components/Recurring.tsx     # CREATE — recurring charges page
  web/src/components/Recurring.test.tsx     # CREATE
  web/src/components/Trends.tsx        # CREATE — Recharts bar chart page
  web/src/components/Trends.test.tsx        # CREATE
  web/src/components/Rules.tsx         # CREATE — rules list + create form + delete
  web/src/components/Rules.test.tsx         # CREATE
  web/src/App.tsx                      # MODIFY — three new routes
  web/src/components/Nav.tsx           # MODIFY — three new links
  web/src/App.test.tsx                 # MODIFY — nav assertion (currently asserts exactly 3 pages)
```

Responsibilities: `report.go` owns SQL aggregation; `trends.go` owns HTTP validation/envelope; each page component owns its own fetch + render (house pattern — no shared data layer beyond `api.ts`).

---

### Task 1: `report.MonthlyFlow` — monthly in/out aggregation

**Files:**
- Modify: `internal/report/report.go` (append at end of file)
- Test: `internal/report/report_test.go` (append at end of file)

The report package's test helpers already exist in `internal/report/testdb_test.go`: `testStore(t)` (serializes on an advisory lock, migrates, truncates, keeps seed categories + the priority-1000 VENMO rule) and `seedSpend(t, s, acct, owner, extID, posted, amount, catName)` (inserts account + txn; sets Payee=Description=extID; categorizes by name). Do not re-create them.

- [ ] **Step 1: Write the failing tests**

Append to `internal/report/report_test.go`:

```go
func TestMonthlyFlow(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	seedSpend(t, s, "ally-s", "scott", "mf1", "2026-05-10", "-100.00", "Groceries")
	seedSpend(t, s, "ally-s", "scott", "mf2", "2026-06-10", "-50.00", "Groceries")
	seedSpend(t, s, "ally-s", "scott", "mf3", "2026-06-15", "2000.00", "Paycheck")
	seedSpend(t, s, "ally-s", "scott", "mf4", "2026-07-01", "-25.00", "Dining")
	seedSpend(t, s, "ally-s", "scott", "mf5", "2026-07-02", "-500.00", "Transfer") // must be excluded
	seedSpend(t, s, "ally-s", "scott", "mf6", "2026-08-01", "-99.00", "Groceries") // outside window

	rows, err := MonthlyFlow(ctx, s, "household", "2026-07", 4)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 4 {
		t.Fatalf("got %d rows, want 4: %+v", len(rows), rows)
	}
	// Empty months render as "0" (COALESCE(...,0)::text — same convention as Summary).
	want := []MonthFlow{
		{Month: "2026-04", In: "0", Out: "0"},
		{Month: "2026-05", In: "0", Out: "100.00"},
		{Month: "2026-06", In: "2000.00", Out: "50.00"},
		{Month: "2026-07", In: "0", Out: "25.00"},
	}
	for i, w := range want {
		if rows[i] != w {
			t.Errorf("row %d = %+v, want %+v", i, rows[i], w)
		}
	}
}

func TestMonthlyFlowViewFilter(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	seedSpend(t, s, "ally-s", "scott", "mv1", "2026-07-05", "-50.00", "Groceries")
	seedSpend(t, s, "ally-n", "nikki", "mv2", "2026-07-06", "-30.00", "Groceries")

	rows, err := MonthlyFlow(ctx, s, "scott", "2026-07", 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("got %d rows, want 1: %+v", len(rows), rows)
	}
	if rows[0] != (MonthFlow{Month: "2026-07", In: "0", Out: "50.00"}) {
		t.Errorf("row = %+v, want scott-only 50.00", rows[0])
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /path/to/worktree
export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/report/ -run TestMonthlyFlow -v
```

Expected: FAIL to compile — `undefined: MonthlyFlow` and `undefined: MonthFlow`.

- [ ] **Step 3: Write the implementation**

Append to `internal/report/report.go`:

```go
// MonthFlow is one month of the income/spend trend. In/Out are decimal
// strings; months with no activity carry "0".
type MonthFlow struct {
	Month string `json:"month"` // YYYY-MM
	In    string `json:"in"`
	Out   string `json:"out"`
}

// MonthlyFlow returns income and spend per month for the `months`-wide window
// ending at (and including) month. Every month in the window is present even
// with zero activity, so charts get a continuous axis. Transfers are excluded
// exactly as in Summary.
func MonthlyFlow(ctx context.Context, s *store.Store, view, month string, months int) ([]MonthFlow, error) {
	own, args := ownerFilter(view, 3)
	q := `
		WITH months AS (
		  SELECT generate_series(
		    $1::date - make_interval(months => $2 - 1),
		    $1::date, interval '1 month')::date AS m
		),
		flows AS (
		  SELECT date_trunc('month', t.posted)::date AS m,
		         SUM(t.amount) FILTER (WHERE t.amount > 0) AS inflow,
		         -SUM(t.amount) FILTER (WHERE t.amount < 0) AS outflow
		  FROM transactions t
		  JOIN accounts a ON a.id = t.account_id
		  LEFT JOIN categories c ON c.id = t.category_id
		  WHERE t.posted >= $1::date - make_interval(months => $2 - 1)
		    AND t.posted < ($1::date + interval '1 month')` +
		notTransfer + own + `
		  GROUP BY 1
		)
		SELECT to_char(months.m, 'YYYY-MM'),
		       COALESCE(flows.inflow, 0)::text,
		       COALESCE(flows.outflow, 0)::text
		FROM months
		LEFT JOIN flows ON flows.m = months.m
		ORDER BY months.m`
	full := append([]any{month + "-01", months}, args...)
	rows, err := s.Pool.Query(ctx, q, full...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]MonthFlow, 0)
	for rows.Next() {
		var mf MonthFlow
		if err := rows.Scan(&mf.Month, &mf.In, &mf.Out); err != nil {
			return nil, err
		}
		out = append(out, mf)
	}
	return out, rows.Err()
}
```

Note it needs no new imports — `context`, `fmt` (via ownerFilter), and `store` are already imported in `report.go`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/report/ -v
```

Expected: all report tests PASS (new + pre-existing).

- [ ] **Step 5: Commit**

```bash
git add internal/report/report.go internal/report/report_test.go
git commit -m "feat: add report.MonthlyFlow for spend/income trends"
```

---

### Task 2: `GET /api/trends` endpoint

**Files:**
- Create: `internal/api/trends.go`
- Create: `internal/api/trends_test.go`
- Modify: `internal/api/server.go` (routes() — one line)

API-package test helpers already exist in `internal/api/testdb_test.go`: `testStore(t)` and `seedTxn(t, s, acct, owner, extID, posted, amount, desc)` (sets Payee=desc; returns txn id). Do not re-create them.

- [ ] **Step 1: Write the failing tests**

Create `internal/api/trends_test.go`:

```go
package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestGetTrends(t *testing.T) {
	s := testStore(t)
	seedTxn(t, s, "ally-s", "scott", "tr1", "2026-06-10", "-50.00", "WHOLE FOODS")
	seedTxn(t, s, "ally-s", "scott", "tr2", "2026-07-05", "-25.00", "WHOLE FOODS")
	srv := New(s)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/trends?view=household&month=2026-07&months=3", nil)
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rec.Code, rec.Body.String())
	}
	var body struct {
		Trends []struct {
			Month string `json:"month"`
			In    string `json:"in"`
			Out   string `json:"out"`
		} `json:"trends"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Trends) != 3 {
		t.Fatalf("got %d trend rows, want 3: %+v", len(body.Trends), body.Trends)
	}
	if body.Trends[0].Month != "2026-05" || body.Trends[2].Month != "2026-07" {
		t.Errorf("window = %s..%s, want 2026-05..2026-07", body.Trends[0].Month, body.Trends[2].Month)
	}
	if body.Trends[1].Out != "50.00" || body.Trends[2].Out != "25.00" {
		t.Errorf("trends = %+v", body.Trends)
	}
}

func TestGetTrendsDefaultsTo12Months(t *testing.T) {
	srv := New(testStore(t))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/trends?view=household&month=2026-07", nil)
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rec.Code, rec.Body.String())
	}
	var body struct {
		Trends []struct {
			Month string `json:"month"`
		} `json:"trends"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Trends) != 12 {
		t.Errorf("got %d trend rows, want 12 (default window)", len(body.Trends))
	}
}

func TestGetTrendsBadMonths(t *testing.T) {
	srv := New(testStore(t))
	for _, months := range []string{"0", "61", "abc", "-3"} {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/api/trends?view=household&month=2026-07&months="+months, nil)
		srv.Handler().ServeHTTP(rec, req)
		if rec.Code != http.StatusBadRequest {
			t.Errorf("months=%s: status=%d, want 400", months, rec.Code)
		}
	}
}

func TestGetTrendsBadView(t *testing.T) {
	srv := New(testStore(t))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/trends?view=alien&month=2026-07", nil)
	srv.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status=%d, want 400 (invalid view)", rec.Code)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' \
  go test ./internal/api/ -run TestGetTrends -v
```

Expected: FAIL — 404s (route not registered), reported as `status=404, want ...`.

- [ ] **Step 3: Write the implementation**

Create `internal/api/trends.go`:

```go
package api

import (
	"log"
	"net/http"
	"strconv"

	"github.com/vollminlab/vollmint/internal/report"
)

func (s *Server) registerTrends() {
	s.mux.HandleFunc("GET /api/trends", s.handleTrends)
}

func (s *Server) handleTrends(w http.ResponseWriter, r *http.Request) {
	view, month, ok := requireViewMonth(w, r)
	if !ok {
		return
	}
	months := 12
	if raw := r.URL.Query().Get("months"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < 1 || n > 60 {
			writeErr(w, http.StatusBadRequest, "months must be an integer between 1 and 60")
			return
		}
		months = n
	}
	points, err := report.MonthlyFlow(r.Context(), s.store, view, month, months)
	if err != nil {
		log.Printf("trends: %v", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
		return
	}
	if points == nil {
		points = []report.MonthFlow{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"trends": points})
}
```

In `internal/api/server.go`, add one line to `routes()` after `s.registerRecurring()`:

```go
	s.registerRecurring()
	s.registerTrends()
	s.registerImports()
```

- [ ] **Step 4: Run the full Go suite**

```bash
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' go test ./...
```

Expected: PASS across all packages.

- [ ] **Step 5: Commit**

```bash
git add internal/api/trends.go internal/api/trends_test.go internal/api/server.go
git commit -m "feat: add GET /api/trends endpoint"
```

---

### Task 3: Transactions page reads the `q` search filter from the URL

This is the prerequisite for recurring deep-links. The API + ts client already support `q`; only the page ignores it today.

**Files:**
- Modify: `web/src/components/Transactions.tsx`
- Test: `web/src/components/Transactions.test.tsx` (append tests to the existing describe block's file)

- [ ] **Step 1: Write the failing tests**

Append to `web/src/components/Transactions.test.tsx` (inside the file, as a new top-level `describe`):

```tsx
describe('Transactions q search filter', () => {
  it('reads q from the URL and passes it to the API', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter initialEntries={['/transactions?view=scott&month=2026-07&q=NETFLIX']}>
        <Transactions view="scott" month="2026-07" />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('WHOLE FOODS')).toBeInTheDocument())
    const calledUrls = fetchMock.mock.calls.map((c) => c[0] as string)
    const txnCall = calledUrls.find((u) => u.startsWith('/api/transactions'))!
    expect(txnCall).toContain('q=NETFLIX')
  })

  it('shows a line noting the active search filter', async () => {
    render(
      <MemoryRouter initialEntries={['/transactions?view=scott&month=2026-07&q=NETFLIX']}>
        <Transactions view="scott" month="2026-07" />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText(/Filtered by search/i)).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd web && npx vitest run src/components/Transactions.test.tsx
```

Expected: FAIL — `q=NETFLIX` not in the transactions URL, and "Filtered by search" not found.

- [ ] **Step 3: Implement**

In `web/src/components/Transactions.tsx`, three edits.

Replace:

```tsx
  const categoryParam = params.get('category')
  const categoryId = categoryParam ? Number(categoryParam) : undefined
```

with:

```tsx
  const categoryParam = params.get('category')
  const categoryId = categoryParam ? Number(categoryParam) : undefined
  const q = params.get('q') ?? undefined
```

Replace the load body's API call:

```tsx
    getTransactions({ view, month, category: categoryId })
```

with:

```tsx
    getTransactions({ view, month, category: categoryId, q })
```

Replace the load effect's dependency comment block:

```tsx
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, month, categoryParam])
```

with:

```tsx
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, month, categoryParam, q])
```

And directly under the existing category-filter line block, add:

```tsx
      {q !== undefined && (
        <p style={{ color: 'var(--muted)' }}>
          Filtered by search: <strong>{q}</strong>
        </p>
      )}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd web && npx vitest run src/components/Transactions.test.tsx
```

Expected: PASS (all Transactions tests, old + new).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/Transactions.tsx web/src/components/Transactions.test.tsx
git commit -m "feat: support q search filter on Transactions page"
```

---

### Task 4: Recurring page

**Files:**
- Create: `web/src/components/Recurring.tsx`
- Create: `web/src/components/Recurring.test.tsx`

The backend (`GET /api/recurring?view=&month=`) and client (`getRecurring(view, month)` returning `{ recurring: Recurring[] }`) already exist. `Recurring` fields: `payee, count, months, avg_amount, last_seen, first_seen, is_new`.

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/Recurring.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Recurring } from './Recurring'

const recurring = {
  recurring: [
    {
      payee: 'NETFLIX', count: 3, months: 3, avg_amount: '15.99',
      last_seen: '2026-07-10', first_seen: '2026-05-10', is_new: false,
    },
    {
      payee: 'HBO MAX', count: 3, months: 3, avg_amount: '10.00',
      last_seen: '2026-09-01', first_seen: '2026-07-01', is_new: true,
    },
  ],
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => recurring }))
})

describe('Recurring', () => {
  it('renders detected recurring charges with formatted amounts', async () => {
    render(
      <MemoryRouter>
        <Recurring view="household" month="2026-07" />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('NETFLIX')).toBeInTheDocument())
    expect(screen.getByText('HBO MAX')).toBeInTheDocument()
    expect(screen.getByText('$15.99')).toBeInTheDocument()
  })

  it('flags only new charges with a NEW badge', async () => {
    render(
      <MemoryRouter>
        <Recurring view="household" month="2026-07" />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('HBO MAX')).toBeInTheDocument())
    expect(screen.getAllByText('NEW')).toHaveLength(1)
  })

  it('deep-links each payee into Transactions with a q filter', async () => {
    render(
      <MemoryRouter>
        <Recurring view="scott" month="2026-07" />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('NETFLIX')).toBeInTheDocument())
    const link = screen.getByRole('link', { name: 'NETFLIX' })
    expect(link.getAttribute('href')).toContain('/transactions')
    expect(link.getAttribute('href')).toContain('q=NETFLIX')
    expect(link.getAttribute('href')).toContain('view=scott')
    expect(link.getAttribute('href')).toContain('month=2026-07')
  })

  it('shows an empty state when nothing recurs', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ recurring: [] }) }))
    render(
      <MemoryRouter>
        <Recurring view="household" month="2026-07" />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText(/No recurring charges/i)).toBeInTheDocument())
  })

  it('surfaces API errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ error: 'boom' }) }),
    )
    render(
      <MemoryRouter>
        <Recurring view="household" month="2026-07" />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('Error: boom')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd web && npx vitest run src/components/Recurring.test.tsx
```

Expected: FAIL — cannot resolve `./Recurring`.

- [ ] **Step 3: Implement**

Create `web/src/components/Recurring.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { View, Recurring as RecurringItem } from '../api'
import { getRecurring } from '../api'
import { money } from '../format'

// Recurring charges detected across all history (>=3 distinct months per
// payee). Each payee deep-links into Transactions pre-filtered by q, so no
// number is a dead end.
export function Recurring({ view, month }: { view: View; month: string }) {
  const [rows, setRows] = useState<RecurringItem[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let live = true
    setLoading(true)
    getRecurring(view, month)
      .then((d) => { if (live) setRows(d.recurring) })
      .catch((e) => { if (live) setErr(e.message) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [view, month])

  if (err) return <p style={{ color: 'var(--danger)' }}>Error: {err}</p>
  if (loading) return <p style={{ color: 'var(--muted)' }}>Loading…</p>

  return (
    <table>
      <thead>
        <tr>
          <th>Payee</th>
          <th style={{ textAlign: 'right' }}>Avg amount</th>
          <th style={{ textAlign: 'right' }}>Charges</th>
          <th style={{ textAlign: 'right' }}>Months</th>
          <th>First seen</th>
          <th>Last seen</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.payee}>
            <td>
              <Link
                to={{
                  pathname: '/transactions',
                  search: `?view=${view}&month=${month}&q=${encodeURIComponent(r.payee)}`,
                }}
              >
                {r.payee}
              </Link>
              {r.is_new && (
                <span
                  style={{ marginLeft: '0.5rem', color: 'var(--accent)', fontSize: '0.8rem', fontWeight: 700 }}
                >
                  NEW
                </span>
              )}
            </td>
            <td style={{ textAlign: 'right' }}>{money(r.avg_amount)}</td>
            <td style={{ textAlign: 'right' }}>{r.count}</td>
            <td style={{ textAlign: 'right' }}>{r.months}</td>
            <td>{r.first_seen}</td>
            <td>{r.last_seen}</td>
          </tr>
        ))}
        {rows.length === 0 && (
          <tr>
            <td colSpan={6} style={{ color: 'var(--muted)' }}>
              No recurring charges detected yet.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd web && npx vitest run src/components/Recurring.test.tsx
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/Recurring.tsx web/src/components/Recurring.test.tsx
git commit -m "feat: add Recurring charges page"
```

---

### Task 5: Trends page — client function + first Recharts chart

**Files:**
- Modify: `web/src/api.ts` (append near the other GET helpers)
- Create: `web/src/components/Trends.tsx`
- Create: `web/src/components/Trends.test.tsx`

**jsdom caveat:** Recharts' `ResponsiveContainer` measures its parent, which is 0×0 under jsdom, so it renders nothing and tests silently pass on emptiness. Use a fixed-size `<BarChart width={1000} height={360}>` instead — this also fits the app's 1100px `.container`. Assert on the Legend (rendered as HTML, reliable under jsdom) and on the fetch URL, not on SVG internals.

**Money rule:** `Number(p.in)` / `Number(p.out)` feed chart geometry only. The tooltip formats through `money()`.

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/Trends.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { Trends } from './Trends'

const trends = {
  trends: [
    { month: '2026-05', in: '3000.00', out: '1200.00' },
    { month: '2026-06', in: '3000.00', out: '900.50' },
    { month: '2026-07', in: '0', out: '450.00' },
  ],
}

function stubFetch() {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => trends })
}

beforeEach(() => {
  vi.stubGlobal('fetch', stubFetch())
})

describe('Trends', () => {
  it('fetches 12 months by default for the current view+month', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<Trends view="scott" month="2026-07" />)
    await waitFor(() => expect(screen.getByText('Income')).toBeInTheDocument())
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/api/trends')
    expect(url).toContain('view=scott')
    expect(url).toContain('month=2026-07')
    expect(url).toContain('months=12')
  })

  it('renders income and spending series in the legend', async () => {
    render(<Trends view="household" month="2026-07" />)
    await waitFor(() => expect(screen.getByText('Income')).toBeInTheDocument())
    expect(screen.getByText('Spending')).toBeInTheDocument()
  })

  it('refetches when the window selector changes', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<Trends view="household" month="2026-07" />)
    await waitFor(() => expect(screen.getByText('Income')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('months window'), { target: { value: '24' } })
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => c[0] as string)
      expect(urls.some((u) => u.includes('months=24'))).toBe(true)
    })
  })

  it('surfaces API errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ error: 'boom' }) }),
    )
    render(<Trends view="household" month="2026-07" />)
    await waitFor(() => expect(screen.getByText('Error: boom')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd web && npx vitest run src/components/Trends.test.tsx
```

Expected: FAIL — cannot resolve `./Trends`.

- [ ] **Step 3: Implement the client function**

In `web/src/api.ts`, add the interface next to the other interfaces (after `Recurring`):

```ts
export interface TrendPoint {
  month: string
  in: string
  out: string
}
```

and the function next to `getRecurring`:

```ts
export function getTrends(view: View, month: string, months: number): Promise<{ trends: TrendPoint[] }> {
  return req(`/api/trends${buildQuery({ view, month, months })}`)
}
```

- [ ] **Step 4: Implement the page**

Create `web/src/components/Trends.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, Tooltip, XAxis, YAxis } from 'recharts'
import type { View, TrendPoint } from '../api'
import { getTrends } from '../api'
import { money } from '../format'

// Fixed-size chart: ResponsiveContainer renders nothing under jsdom (zero
// width), and 1000px fits the app's 1100px container.
export function Trends({ view, month }: { view: View; month: string }) {
  const [months, setMonths] = useState(12)
  const [rows, setRows] = useState<TrendPoint[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let live = true
    setLoading(true)
    getTrends(view, month, months)
      .then((d) => { if (live) setRows(d.trends) })
      .catch((e) => { if (live) setErr(e.message) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [view, month, months])

  if (err) return <p style={{ color: 'var(--danger)' }}>Error: {err}</p>

  // Number() here is chart geometry only — display always goes through money().
  const data = rows.map((p) => ({ month: p.month, income: Number(p.in), spending: Number(p.out) }))

  return (
    <div>
      <p>
        <label style={{ color: 'var(--muted)' }}>
          Window:{' '}
          <select
            aria-label="months window"
            value={months}
            onChange={(e) => setMonths(Number(e.target.value))}
          >
            <option value={6}>6 months</option>
            <option value={12}>12 months</option>
            <option value={24}>24 months</option>
          </select>
        </label>
      </p>
      {loading ? (
        <p style={{ color: 'var(--muted)' }}>Loading…</p>
      ) : (
        <BarChart width={1000} height={360} data={data}>
          <CartesianGrid stroke="#262a33" />
          <XAxis dataKey="month" stroke="var(--muted)" />
          <YAxis stroke="var(--muted)" />
          <Tooltip
            formatter={(v) => money(Number(v).toFixed(2))}
            contentStyle={{ background: 'var(--panel)', border: '1px solid #262a33' }}
          />
          <Legend />
          <Bar dataKey="spending" name="Spending" fill="#f9714f" />
          <Bar dataKey="income" name="Income" fill="#4fd18b" />
        </BarChart>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd web && npx vitest run src/components/Trends.test.tsx
```

Expected: PASS (4 tests). If the Legend assertions fail with nothing rendered, the chart lost its fixed width/height — do not "fix" it by adding ResponsiveContainer.

- [ ] **Step 6: Commit**

```bash
git add web/src/api.ts web/src/components/Trends.tsx web/src/components/Trends.test.tsx
git commit -m "feat: add Trends page with monthly income/spending chart"
```

---

### Task 6: Rules page

**Files:**
- Create: `web/src/components/Rules.tsx`
- Create: `web/src/components/Rules.test.tsx`

Backend + client already exist: `getRules()`, `createRule({priority, match_type, pattern, category_id})` → `{id, recategorized}` (POST re-runs rules over ALL history server-side, atomically), `deleteRule(id)`. `Rule` fields: `id, priority, match_type, pattern, category_id`. There is no edit endpoint — the UI offers delete + create only.

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/Rules.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { Rules } from './Rules'

const rules = {
  rules: [
    { id: 1, priority: 500, match_type: 'substring', pattern: 'netflix', category_id: 6 },
    { id: 2, priority: 1000, match_type: 'substring', pattern: 'VENMO', category_id: 15 },
  ],
}
const cats = {
  categories: [
    { id: 6, name: 'Subscriptions', parent_id: null, kind: 'spend', is_vice: false },
    { id: 15, name: 'Needs Venmo detail', parent_id: null, kind: 'spend', is_vice: false },
  ],
}

function stubFetch() {
  return vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      return Promise.resolve({ ok: true, json: async () => ({ id: 3, recategorized: 4 }) })
    }
    if (init?.method === 'DELETE') {
      return Promise.resolve({ ok: true, json: async () => ({ status: 'deleted' }) })
    }
    const body = url.startsWith('/api/categories') ? cats : rules
    return Promise.resolve({ ok: true, json: async () => body })
  })
}

beforeEach(() => {
  vi.stubGlobal('fetch', stubFetch())
})

describe('Rules', () => {
  it('lists rules with resolved category names', async () => {
    render(<Rules />)
    await waitFor(() => expect(screen.getByText('netflix')).toBeInTheDocument())
    expect(screen.getByText('VENMO')).toBeInTheDocument()
    expect(screen.getByText('Subscriptions')).toBeInTheDocument()
    expect(screen.getByText('Needs Venmo detail')).toBeInTheDocument()
  })

  it('creates a rule and reports how many transactions were recategorized', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<Rules />)
    await waitFor(() => expect(screen.getByText('netflix')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('new rule pattern'), { target: { value: 'spotify' } })
    fireEvent.change(screen.getByLabelText('new rule category'), { target: { value: '6' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))

    await waitFor(() =>
      expect(screen.getByText('Rule added — 4 transactions recategorized.')).toBeInTheDocument(),
    )
    const post = fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === 'POST')!
    expect(post[0]).toBe('/api/rules')
    const body = JSON.parse((post[1] as RequestInit).body as string)
    expect(body).toEqual({ priority: 500, match_type: 'substring', pattern: 'spotify', category_id: 6 })
  })

  it('requires pattern and category before submitting', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<Rules />)
    await waitFor(() => expect(screen.getByText('netflix')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    await waitFor(() => expect(screen.getByText(/Pattern and category are required/i)).toBeInTheDocument())
    expect(fetchMock.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === 'POST')).toBe(false)
  })

  it('deletes a rule', async () => {
    const fetchMock = stubFetch()
    vi.stubGlobal('fetch', fetchMock)
    render(<Rules />)
    await waitFor(() => expect(screen.getByText('netflix')).toBeInTheDocument())
    fireEvent.click(screen.getByLabelText('delete rule netflix'))
    await waitFor(() => {
      const del = fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === 'DELETE')
      expect(del?.[0]).toBe('/api/rules/1')
    })
  })

  it('surfaces API errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ error: 'boom' }) }),
    )
    render(<Rules />)
    await waitFor(() => expect(screen.getByText('Error: boom')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd web && npx vitest run src/components/Rules.test.tsx
```

Expected: FAIL — cannot resolve `./Rules`.

- [ ] **Step 3: Implement**

Create `web/src/components/Rules.tsx`:

```tsx
import { useEffect, useState } from 'react'
import type { Category, Rule } from '../api'
import { createRule, deleteRule, getCategories, getRules } from '../api'

// Rules are applied first-match-wins, ordered by priority ASC then id ASC,
// case-insensitive substring against payee+description. Creating a rule
// re-runs the engine over all history server-side. There is no edit endpoint:
// to change a rule, delete it and add a replacement.
export function Rules() {
  const [rules, setRules] = useState<Rule[]>([])
  const [cats, setCats] = useState<Category[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState('')

  const [pattern, setPattern] = useState('')
  const [matchType, setMatchType] = useState('substring')
  const [priority, setPriority] = useState('500')
  const [categoryId, setCategoryId] = useState('')

  const load = () => {
    setLoading(true)
    Promise.all([getRules(), getCategories()])
      .then(([r, c]) => {
        setRules(r.rules)
        setCats(c.categories)
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const catName = (id: number) => cats.find((c) => c.id === id)?.name ?? String(id)

  const add = async () => {
    if (!pattern || !categoryId) {
      setStatus('Pattern and category are required.')
      return
    }
    try {
      const res = await createRule({
        priority: Number(priority),
        match_type: matchType,
        pattern,
        category_id: Number(categoryId),
      })
      setStatus(`Rule added — ${res.recategorized} transactions recategorized.`)
      setPattern('')
      load()
    } catch (e) {
      setStatus((e as Error).message)
    }
  }

  const remove = async (id: number) => {
    try {
      await deleteRule(id)
      setStatus('Rule deleted.')
    } catch (e) {
      setStatus((e as Error).message)
    } finally {
      load()
    }
  }

  if (err) return <p style={{ color: 'var(--danger)' }}>Error: {err}</p>

  return (
    <div>
      <div className="card" style={{ marginBottom: '1rem' }}>
        <p style={{ color: 'var(--muted)', marginTop: 0 }}>
          First match wins (lowest priority number first). Convention: 100 = money movement,
          400 = disambiguators, 500 = merchants, 1000 = fallback.
        </p>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            aria-label="new rule pattern"
            placeholder="pattern (e.g. spotify)"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
          />
          <select aria-label="new rule match type" value={matchType} onChange={(e) => setMatchType(e.target.value)}>
            <option value="substring">substring</option>
            <option value="regex">regex</option>
          </select>
          <input
            aria-label="new rule priority"
            type="number"
            style={{ width: '5rem' }}
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          />
          <select aria-label="new rule category" value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
            <option value="" disabled>
              — category —
            </option>
            {cats.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <button onClick={add}>Add rule</button>
        </div>
        {status && <p style={{ color: 'var(--muted)', marginBottom: 0 }}>{status}</p>}
      </div>
      {loading ? (
        <p style={{ color: 'var(--muted)' }}>Loading…</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th style={{ textAlign: 'right' }}>Priority</th>
              <th>Pattern</th>
              <th>Match</th>
              <th>Category</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.id}>
                <td style={{ textAlign: 'right' }}>{r.priority}</td>
                <td>{r.pattern}</td>
                <td>{r.match_type}</td>
                <td>{catName(r.category_id)}</td>
                <td>
                  <button aria-label={`delete rule ${r.pattern}`} onClick={() => remove(r.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {rules.length === 0 && (
              <tr>
                <td colSpan={5} style={{ color: 'var(--muted)' }}>
                  No rules.
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

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd web && npx vitest run src/components/Rules.test.tsx
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/Rules.tsx web/src/components/Rules.test.tsx
git commit -m "feat: add Rules management page"
```

---

### Task 7: Wire routes + nav

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/Nav.tsx`
- Modify: `web/src/App.test.tsx` (it currently asserts *exactly* the three original nav pages — must be updated in the same commit)

- [ ] **Step 1: Update the failing test first**

In `web/src/App.test.tsx`, replace the test body:

```tsx
  it('renders the nav with all six pages', () => {
    render(
      <MemoryRouter initialEntries={['/?view=household&month=2026-07']}>
        <App />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Transactions' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Recurring' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Trends' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Budgets' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Rules' })).toBeInTheDocument()
  })
```

(The old test name was `'renders the nav with all three pages'` — replace name and body. The fetch stub at the top of the file needs no change: only Dashboard fetches on `/`.)

- [ ] **Step 2: Run to verify it fails**

```bash
cd web && npx vitest run src/App.test.tsx
```

Expected: FAIL — no link named 'Recurring'.

- [ ] **Step 3: Implement**

In `web/src/components/Nav.tsx`, replace the link list:

```tsx
      {link('/', 'Dashboard')}
      {link('/transactions', 'Transactions')}
      {link('/recurring', 'Recurring')}
      {link('/trends', 'Trends')}
      {link('/budgets', 'Budgets')}
      {link('/rules', 'Rules')}
```

In `web/src/App.tsx`, add imports after the `Budgets` import:

```tsx
import { Recurring } from './components/Recurring'
import { Trends } from './components/Trends'
import { Rules } from './components/Rules'
```

and add routes inside `<Routes>` after the `/transactions` route:

```tsx
        <Route path="/recurring" element={<Recurring view={view} month={month} />} />
        <Route path="/trends" element={<Trends view={view} month={month} />} />
```

and after the `/budgets` route:

```tsx
        <Route path="/rules" element={<Rules />} />
```

- [ ] **Step 4: Run the full frontend suite**

```bash
cd web && npm test
```

Expected: PASS — every component suite plus App.

- [ ] **Step 5: Commit**

```bash
git add web/src/App.tsx web/src/components/Nav.tsx web/src/App.test.tsx
git commit -m "feat: wire Recurring, Trends, and Rules pages into nav and routes"
```

---

### Task 8: Final verification

- [ ] **Step 1: Full Go suite + vet**

```bash
export PATH="$HOME/.local/go/bin:$HOME/go/bin:$PATH"
TEST_DATABASE_URL='postgres://postgres:dev@localhost:5433/postgres?sslmode=disable' go test ./...
go vet ./...
```

Expected: all PASS, vet clean.

- [ ] **Step 2: Full frontend suite + production build**

```bash
cd web && npm test && npm run build
```

Expected: tests PASS; `tsc -b && vite build` completes with no errors (this is where any unused import from strict mode would surface).

- [ ] **Step 3: Full binary build**

```bash
./scripts/build.sh
```

Expected: `bin/vollmint` produced.

- [ ] **Step 4: Finish the branch**

Use the `finishing-a-development-branch` skill: push `feat/recurring-trends-rules`, open a PR in the vollmint repo. PR body: what shipped + the three spec deviations noted in Context. **Do not merge — Scott merges.** Deployment (tag `v*` → CI image → bump in the k8s repo) is a separate follow-up after merge, not part of this plan.

---

## Out of scope (deliberate, YAGNI)

- Rule editing (no PATCH endpoint exists; delete+recreate covers it)
- Per-category trend series / stacked charts (start with income vs spending; extend later if wanted)
- Dashboard recurring panel (standalone page replaces it per Scott's request)
- Net-worth trend (explicitly out of scope in the design spec)
