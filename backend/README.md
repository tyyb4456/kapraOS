# KapraOS - Backend

Multi-tenant Fabric & Fashion Retail Management SaaS - KapraOS backend.

Built step by step per the phased plan in the architecture docs
(Foundation → Catalog → Inventory → Purchases → POS → Customer Khata →
Supplier Payables → Accounting → Intelligence). Steps 1-9 are implemented:
project foundation, catalog, inventory, suppliers & purchases, sales/POS,
customer receivables (Khata), supplier payables (Khata), the general
ledger (double-entry accounting), and read-only reporting & financial
statements.

## Step 8 - General Ledger / Double-Entry Accounting

The accounting layer sits **on top of** the operational transaction system.
It adds exactly two tables (`db_arch.md` sections 23-24):

```text
accounts         a shop's chart of accounts
ledger_entries   one debit or credit line of a posting group
```

No `account_balances`, `journals`, `journal_lines` or balance columns exist:
account balances are always **derived** from `ledger_entries`.

### Default chart of accounts (one per shop, `is_system = true`)

```text
1000  Cash                 ASSET
1010  Bank                 ASSET
1100  Accounts Receivable  ASSET
1200  Inventory            ASSET
2000  Accounts Payable     LIABILITY
3000  Owner Equity         EQUITY
4000  Sales Revenue        REVENUE
```

`ensure_system_accounts()` provisions them idempotently per shop.

### Posting rules

```text
Sale               Debit  Cash / Bank            (paid portion)
                   Debit  Accounts Receivable    (due portion)
                   Credit Sales Revenue          (total)

Purchase           Debit  Inventory
                   Credit Accounts Payable

Customer Payment   Debit  Cash / Bank
                   Credit Accounts Receivable

Supplier Payment   Debit  Accounts Payable
                   Credit Cash / Bank
```

Payment-method → asset mapping is deterministic: `cash` → Cash; `card`,
`bank`, `jazzcash`, `easypaisa` → Bank; `other` → Cash.

### Integrity guarantees

- **Double-entry:** `app.services.accounting` refuses to persist a posting
  whose `SUM(debits) != SUM(credits)` (`UnbalancedPostingError`).
- **Database checks:** each ledger line is non-negative, non-zero, and exactly
  one of debit/credit.
- **Duplicate protection:** a unique constraint on
  `(shop_id, reference_type, reference_id, account_id)` makes posting
  idempotent - re-posting a sale/purchase/payment writes nothing new.
- **Tenant isolation:** the composite foreign key
  `(account_id, shop_id) -> accounts(id, shop_id)` makes a cross-tenant ledger
  entry impossible at the database level.
- **Transaction safety:** posting runs inside the caller's transaction, so a
  failure rolls the whole business event back.

### What Step 8 does NOT change

Customer Khata remains `Sales + customer Payments`; Supplier Khata remains
`Purchases + supplier Payments`. The general ledger is an *additional*
accounting representation of those same events, never their source of truth.
No COGS, tax, returns, financial statements, manual journal-entry API or
auth was added in this step.

### Accounting endpoints (read-only)

```text
GET /accounts                        chart of accounts
GET /accounts/{account_id}/balance   derived balance
GET /accounts/{account_id}/ledger    ledger lines (date filter + pagination)
```

## Step 9 - Reporting & Financial Statements

Step 9 is a **read-only reporting layer** over the existing operational and
accounting data. It adds **no tables, no migration and no accounting posting
rules**: every figure is derived at query time from the source-of-truth rows
that Steps 1-8 already produce.

```text
Operational Data + Accounting Ledger + Inventory State
        ↓
Reporting Services (app/services/reporting.py)
        ↓
Financial Statements + Business Dashboard
```

### Reports and their sources of truth

| Report | Derived from |
| --- | --- |
| Trial Balance | `accounts` + `ledger_entries` |
| Profit & Loss | Revenue: ledger REVENUE accounts; COGS: historical `SaleItem.cost_price` |
| Balance Sheet | ASSET / LIABILITY / EQUITY account balances |
| Dashboard | Sales, Purchases, Payments, Inventory, plus Step 6/7 Khata aggregates |

### Trial balance semantics

Both `start_date` and `end_date` are inclusive. Each row carries the period's
`debit_total` / `credit_total` / `balance` **and** the `closing_*` figures
(everything up to `end_date`), so both "period activity" and "closing balance"
are available without ambiguity. `is_balanced` is the
`Total Debits == Total Credits` check.

### P&L: the hybrid V1 COGS source

Revenue comes from the ledger, but COGS does **not**: Step 8 posts no COGS
entry. For V1:

```text
COGS = SUM(SaleItem.quantity × SaleItem.cost_price)
```

`SaleItem.cost_price` is the weighted-average cost snapshotted at sale time
and never rewritten, so historical COGS is correct even after later receipts
move the current average. Current inventory cost is deliberately not used.

### Expense limitation (V1)

There is **no Expense domain**, so the P&L reports
`expense_reporting_available = false` and returns `expenses = null`,
`net_profit = null`. It never presents Gross Profit as if it were Net Profit.

### Balance sheet

Assets / liabilities / equity come straight from ledger account balances (no
`Cash = sales - expenses` shortcuts). Revenue and expense accounts are not yet
closed into equity, so the report exposes `difference` explicitly rather than
inventing balancing entries.

### Dashboard metrics

Today's sales and count, today's payments received and count, today's
purchases and count, today's COGS and gross profit, receivables outstanding,
payables outstanding, inventory quantity and estimated value. Receivables and
payables reuse the Step 6/7 services, so the dashboard always agrees with the
Khatas.

**Not available in V1:** low stock (no reorder/minimum-stock field exists) and
expense/net-profit reporting. Both are surfaced via explicit flags, not
fabricated numbers. "Today" is the current UTC day - no shop timezone is
configured yet.

### Reporting endpoints (read-only)

```text
GET /reports/trial-balance       ?start_date=&end_date=
GET /reports/profit-and-loss     ?start_date=&end_date=
GET /reports/balance-sheet       ?end_date=
GET /reports/dashboard
```

## Stack

- Python 3.11+
- FastAPI
- SQLAlchemy 2.x (async, typed `Mapped[]` / `mapped_column()`)
- asyncpg
- Alembic (async-aware `env.py`)
- PostgreSQL

## Setup

### 1. Create a virtualenv and install dependencies

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e ".[dev]"

### 2. Create the databases

    CREATE USER kapraos_app WITH PASSWORD 'change-me' CREATEDB;
    CREATE DATABASE kapraos OWNER kapraos_app;
    CREATE DATABASE kapraos_test OWNER kapraos_app;

### 3. Configure environment variables

    cp .env.example .env
    # then edit .env - at minimum set DATABASE_URL to match the role/db above

### 4. Run the migration

    python3 -m alembic upgrade head

### 5. Run the app

    python3 -m uvicorn app.main:app --reload
    curl http://127.0.0.1:8000/health
    # {"status":"ok"}

## Running tests

Tests need their own PostgreSQL database (default
`postgresql+asyncpg://kapraos_app:changeme@localhost:5432/kapraos_test`,
override via `TEST_DATABASE_URL`). The suite builds its schema by actually
running `alembic upgrade head` against that database.

    python3 -m pytest -v

## Code quality

    python3 -m ruff check app tests alembic
    python3 -m mypy app tests --ignore-missing-imports

## Notes / decisions made in this step

- Email uniqueness is global, not per-shop (simplest safe default; not
  specified in the architecture doc).
- The Postgres `user_role` enum is managed explicitly in the migration
  (`create_type=False` + explicit `.create()`/`.drop()`), because the
  default Alembic-autogenerated `downgrade()` drops the tables but not the
  enum type, breaking a downgrade→upgrade cycle.
- Row Level Security is intentionally deferred, per instructions.
- Authentication (JWT, password hashing) is out of scope for this step.