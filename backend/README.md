# KapraOS - Backend

Multi-tenant Fabric & Fashion Retail Management SaaS - KapraOS backend.

Built step by step per the phased plan in the architecture docs
(Foundation → Catalog → Inventory → Purchases → POS → Customer Khata →
Supplier Payables → Accounting → Intelligence). Steps 1-10 are implemented:
project foundation, catalog, inventory, suppliers & purchases, sales/POS,
customer receivables (Khata), supplier payables (Khata), the general
ledger (double-entry accounting), read-only reporting & financial
statements, and the expense domain plus COGS ledger postings.

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
No tax, returns, manual journal-entry API or auth was added in this step.

## Step 10 — Expense Domain + COGS Ledger Postings

### COGS (Cost of Goods Sold)

Every qualifying sale (`COMPLETED` / `PARTIAL`) now generates a COGS
posting inside the same atomic transaction as the sale itself.

```text
Sale (COMPLETED / PARTIAL)
    → Revenue group:  Dr Cash/Bank + Dr AR,  Cr Sales Revenue
    → COGS group:     Dr Cost of Goods Sold,  Cr Inventory
```

The amount is `SUM(SaleItem.quantity × SaleItem.cost_price)` using the
**immutable historical cost** snapshotted on each `SaleItem` at sale
time. The current weighted-average inventory cost is never consulted,
so a later, more expensive purchase cannot rewrite what an earlier
sale cost.

**Zero-cost sales** are permitted (zero-cost inventory is allowed) and
are skipped silently — Step 8 forbids zero-value ledger lines, so no
invalid line is written for a free sample.

**Cancelled sales** generate no COGS: `post_cogs()` returns early
unless the sale is `COMPLETED` or `PARTIAL`. Because V1 has no
cancellation workflow, a sale that was posted as COMPLETED and then
had its status flipped to CANCELLED afterwards keeps its ledger entries
(post-hoc cancellation does not reverse the ledger — that is a future
accounting-reversal step).

**Duplicate protection** is unchanged: the reference
`("SALE_COGS", sale.id)` is unique per `(shop_id, reference_type,
reference_id, account_id)`, so replaying a sale writes no second COGS
group.

New system accounts (added to `DEFAULT_ACCOUNTS` by
`ensure_system_accounts()`, idempotently):

```text
5000  Cost of Goods Sold   EXPENSE
5100  Rent Expense         EXPENSE
5200  Utilities Expense    EXPENSE
5300  Salaries Expense     EXPENSE
5400  Marketing Expense    EXPENSE
5500  Transport Expense    EXPENSE
5600  Maintenance Expense  EXPENSE
5700  Supplies Expense     EXPENSE
5900  Other Expense        EXPENSE
```

Several categories share the "Other Expense" account by design — the
chart of accounts stays small and a future step can split it without
touching this step.

### Expense domain

An immediate-payment operating expense is recorded via `POST /expenses`.
There is deliberately **no payable/liability workflow** for expenses in
V1 — `Accounts Payable` stays supplier-only.

```text
POST /expenses   { category, amount, payment_method, description?, expense_date? }
GET  /expenses   (date filter, pagination)
GET  /expenses/{id}
```

```text
Expense (cash)
    → Dr Rent Expense,  Cr Cash

Expense (bank)
    → Dr Marketing Expense,  Cr Bank
```

Posted expenses are **immutable**: there is no update or delete route,
because a posted expense already has ledger entries and V1 has no
reversal/void workflow (`step_10_desc.md` sections 16 and 37).

### P&L is now fully ledger-derived

```text
Revenue      →  ledger REVENUE accounts
COGS         →  ledger COGS account
Expenses     →  ledger EXPENSE accounts (COGS excluded)
Gross Profit = Revenue - COGS
Net Profit   = Gross Profit - Expenses
```

All three components are now read from `ledger_entries`;
`SaleItem.cost_price` is only the source used to *create* the COGS
posting.

### Dashboard

`today_cogs` now reflects the ledger COGS. New fields
`today_expenses` and `today_net_profit` are reported. Gross profit
uses ledger COGS.

### Expense accounting endpoints

```text
GET /accounts                        chart of accounts
GET /accounts/{account_id}/balance   derived balance
GET /accounts/{account_id}/ledger    ledger lines
```

### Integrity guarantees added in this step

- **COGS atomicity:** `post_cogs()` runs inside the sale transaction,
  so a COGS failure rolls the entire sale back.
- **Zero-line guard:** a COGS of zero returns `[]` rather than writing
  an invalid ledger line.
- **Qualifying-sales guard:** `post_cogs()` returns early for
  `CANCELLED` sales, keeping the rule centralised in one place.
- **Idempotency:** both COGS (`"SALE_COGS"`) and expense (`"EXPENSE"`)
  postings reuse the `(shop_id, reference_type, reference_id,
  account_id)` unique constraint.
- **Tenant isolation:** unchanged — a shop cannot create or read
  another shop's COGS or expenses.

### What Step 10 does NOT change

Steps 1-9 tables are untouched. No `journals`, `posting_batches`,
`cogs_entries`, `expense_ledger` or `expense_balances` tables were
created — the ledger remains the single representation. No manual
journal-entry or ledger-mutation API exists. Inventory quantity stays
in the inventory service; the accounting service only writes the
financial side. Tax, returns, supplier/customer returns, cash-flow
statement, fiscal periods and payroll remain out of scope.

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

### P&L: fully ledger-derived (Step 10)

All three components are read from `ledger_entries`:

```text
Revenue  →  ledger REVENUE accounts
COGS     →  ledger Cost of Goods Sold account (5000)
Expenses →  ledger EXPENSE accounts (5100-5900), excluding COGS
Gross Profit = Revenue - COGS
Net Profit   = Gross Profit - Expenses
```

`SaleItem.cost_price` is now only the source used to *create* the COGS
posting. The ledger is the reporting source.

**Note on cancellation:** because there is no reversal workflow in V1,
post-hoc status changes do not reverse ledger entries, so a sale that
was posted as COMPLETED and later cancelled retains its COGS in the
ledger.

### Balance sheet

Assets / liabilities / equity come straight from ledger account balances (no
`Cash = sales - expenses` shortcuts). Revenue and expense accounts are not yet
closed into equity, so the report exposes `difference` explicitly rather than
inventing balancing entries. COGS reduces `Inventory` and `Expenses` instead,
which the balance sheet reflects naturally through the account balances.

### Dashboard metrics

Today's sales and count, today's payments received and count, today's
purchases and count, today's COGS, today's expenses, today's gross profit
and today's net profit, receivables outstanding, payables outstanding,
inventory quantity and estimated value. Receivables and payables reuse the
Step 6/7 services, so the dashboard always agrees with the Khatas.

**Not available in V1:** low stock (no reorder/minimum-stock field exists).
"Today" is the current UTC day - no shop timezone is configured yet.

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