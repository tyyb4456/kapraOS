# Step 9 — Reporting & Financial Statements

You are continuing development of the existing fabric/fashion retail management backend.

Completed architecture:

* Step 1 — Backend Foundation
* Step 2 — Catalog
* Step 3 — Inventory
* Step 4 — Suppliers + Purchases
* Step 5 — Sales / POS
* Step 6 — Customer Khata / Receivables
* Step 7 — Supplier Payables / Supplier Khata
* Step 8 — General Ledger / Double-Entry Accounting

# IMPORTANT WORKING RULE

Work ONLY on:

# Step 9 — Reporting & Financial Statements

Before changing anything:

1. Inspect the existing repository.
2. Read the architecture/design Markdown documentation.
3. Read `db_arch.md`, especially the accounting and reporting sections.
4. Inspect:

   * Account
   * LedgerEntry
   * Sale
   * SaleItem
   * Payment
   * Purchase
   * PurchaseItem
   * Inventory
   * Customer
   * Supplier
   * Customer Receivables
   * Supplier Payables
   * Accounting service
5. Understand the existing transaction and tenant-isolation patterns.
6. Do NOT redesign Steps 1–8.
7. Do NOT introduce new accounting posting rules.
8. Do NOT create mutable reporting/balance tables.
9. Keep reporting read-only.
10. Preserve all existing tests and behavior.

---

# 1. Core Principle

Step 9 is a **read-only reporting layer** over the existing operational and accounting data.

The architecture should be:

```text
Operational Data
      +
Accounting Ledger
      +
Inventory State
      ↓
Reporting Services
      ↓
Financial Statements
      +
Business Dashboard
```

Do NOT create:

```text
report_snapshots
daily_balances
monthly_balances
profit_records
dashboard_cache
financial_statement_entries
```

unless the existing architecture explicitly requires a cache.

All values should be derived from existing source-of-truth tables.

---

# 2. Important COGS Constraint

The current Step 8 accounting implementation does NOT post COGS.

Therefore:

**DO NOT claim that gross profit is derived entirely from the General Ledger.**

For the current architecture:

```text
Revenue
    → derived from Sales Revenue ledger account

COGS
    → derived from existing SaleItem.cost_price × quantity
```

This is intentional for Step 9.

Do NOT add COGS accounting postings in this step.

Do NOT modify Sale posting logic.

Do NOT introduce inventory-accounting redesign.

If the architecture document specifies another existing COGS source, follow that instead.

---

# 3. Reporting Service

Create:

```text
app/services/reporting.py
```

Keep reporting logic centralized.

At minimum implement reporting operations equivalent to:

```python
get_trial_balance(...)
get_profit_and_loss(...)
get_balance_sheet(...)
get_dashboard_summary(...)
```

Use:

* async SQLAlchemy
* Decimal
* PostgreSQL NUMERIC
* tenant-scoped queries
* database aggregation where practical

Do not load entire tables into Python when SQL aggregation can perform the calculation.

---

# 4. Trial Balance

Implement:

```python
get_trial_balance(...)
```

The Trial Balance should be derived from:

```text
accounts
+
ledger_entries
```

For each active/relevant account include:

```text
account_id
code
name
account_type
debit_total
credit_total
balance
```

The report should show:

```text
Total Debits
Total Credits
```

and verify:

```text
Total Debits == Total Credits
```

for the selected period/ledger scope.

Do not store these totals.

---

# 5. Trial Balance Date Semantics

Inspect the architecture and existing timestamp conventions.

Support a practical date range:

```text
start_date
end_date
```

where appropriate.

Be precise about whether the Trial Balance represents:

### Period activity

```text
debits/credits posted during the selected period
```

or:

### Closing balance

```text
all activity up to end_date
```

A useful implementation may expose both:

```text
period_debits
period_credits
closing_balance
```

if this matches the architecture.

Do not invent ambiguous accounting semantics.

Document the chosen behavior.

---

# 6. Account Normal Balance

Use the existing account types:

```text
ASSET
LIABILITY
EQUITY
REVENUE
EXPENSE
```

Normal balance:

```text
ASSET
EXPENSE
    Debit increases balance

LIABILITY
EQUITY
REVENUE
    Credit increases balance
```

Centralize this calculation.

Do not duplicate account-balance logic already implemented in `accounting.py`.

Reuse existing accounting service functionality where practical.

---

# 7. Profit & Loss

Implement:

```python
get_profit_and_loss(...)
```

The P&L should be read-only.

At minimum:

```text
Revenue
- COGS
----------------
Gross Profit

- Expenses
----------------
Net Profit
```

However, carefully inspect whether an Expense domain exists.

If there is currently NO expense model/domain:

**Do NOT create one in Step 9 unless `db_arch.md` explicitly requires it.**

In that case:

```text
Expenses = 0 / unavailable
```

should be represented honestly according to the project's response contract.

Do not fabricate expense data.

---

# 8. Revenue

Revenue should be derived from the existing accounting ledger.

Use the system Sales Revenue account:

```text
4000 Sales Revenue
```

or whatever code/name is actually established by the current repository.

For the selected period:

```text
Revenue =
credit activity of revenue accounts
```

Respect the existing account-type semantics.

Do not sum arbitrary Sale totals if the accounting ledger is intended to be the accounting source for revenue.

---

# 9. COGS

Until COGS posting exists, derive COGS from the existing sales data.

Use the immutable historical:

```text
SaleItem.cost_price
```

captured in Step 5.

Conceptually:

```text
COGS =
Σ(SaleItem.quantity × SaleItem.cost_price)
```

for qualifying sales in the selected period.

Use the same Sale status qualification rules established by Customer Khata / accounting.

Do NOT use current inventory weighted-average cost to reconstruct historical COGS.

The historical `SaleItem.cost_price` exists specifically to preserve sale-time cost.

Use Decimal arithmetic.

---

# 10. Gross Profit

Calculate:

```text
Gross Profit = Revenue - COGS
```

Do not call this "net profit."

Example:

```text
Revenue = 100,000
COGS    = 65,000

Gross Profit = 35,000
```

If Revenue comes from the ledger and COGS from SaleItem historical cost, clearly document this hybrid V1 reporting source.

---

# 11. Expenses

First inspect whether an existing Expense domain exists.

If it does:

* use its existing source of truth
* aggregate expenses for the selected period
* do not duplicate expense records

If it does NOT exist:

Do not create the Expense domain in Step 9 unless explicitly required by `db_arch.md`.

Instead report the limitation clearly.

For example:

```text
expenses = 0
net_profit = gross_profit
```

ONLY if that interpretation is explicitly appropriate.

Otherwise expose:

```text
expense_reporting_available = false
```

and avoid presenting an incomplete figure as a fully fledged net profit.

Do not invent financial data.

---

# 12. Balance Sheet

Implement:

```python
get_balance_sheet(...)
```

The Balance Sheet should be derived from account balances.

At minimum include:

```text
Assets
Liabilities
Equity
```

Conceptually:

```text
Assets = Liabilities + Equity
```

Use account types:

```text
ASSET
LIABILITY
EQUITY
```

and the normal-balance rules from Step 8.

---

# 13. Balance Sheet Accounts

The system accounts should naturally contribute:

```text
1000 Cash
1010 Bank
1100 Accounts Receivable
1200 Inventory

2000 Accounts Payable

3000 Owner Equity
```

according to their ledger-derived balances.

Do not manually calculate:

```text
Cash = sales - expenses
```

or similar shortcuts.

The Balance Sheet should use actual ledger account balances.

---

# 14. Balance Sheet Equation

Return enough information to verify:

```text
Total Assets
Total Liabilities
Total Equity
Liabilities + Equity
Difference
```

The expected invariant is:

```text
Total Assets == Total Liabilities + Total Equity
```

subject to the limitations of the current accounting implementation.

If the existing transaction set can legitimately produce an imbalance because opening equity/opening balances are not yet supported, do NOT silently hide it.

Report the difference.

Do not add automatic balancing entries.

---

# 15. Dashboard Summary

Implement:

```python
get_dashboard_summary(...)
```

This is a business overview, not a second source of truth.

At minimum include:

```text
today's sales
today's sales count
today's payments received
today's purchases
receivables outstanding
payables outstanding
current inventory quantity/value where safely available
low-stock variant count
```

Also include gross profit if it can be correctly derived using:

```text
Sales Revenue
-
SaleItem historical COGS
```

Do not include metrics that cannot be calculated reliably.

---

# 16. Today's Sales

Use the existing Sale domain.

Respect the existing Sale status semantics.

Do not count:

```text
CANCELLED
```

sales.

Be consistent with the existing Customer Khata / accounting qualification rules.

Use the shop's timezone/date conventions already present in the application.

Do not assume UTC calendar boundaries if the project has a configured timezone.

If timezone support is not yet implemented, document the current behavior rather than inventing a new timezone subsystem.

---

# 17. Today's Purchases

Derive today's purchases from the existing Purchase domain.

Do not create a purchase reporting table.

Use server-side aggregation.

---

# 18. Receivables

Do not calculate receivables using a new reporting formula.

Reuse the existing Step 6 service:

```text
get_customer_balance()
```

or an appropriate aggregate reporting method.

The dashboard should reflect the same Customer Khata source of truth:

```text
Sales + Customer Payments
```

Do not introduce a second receivables calculation that could drift.

---

# 19. Payables

Likewise, reuse Step 7:

```text
get_supplier_balance()
```

or an appropriate aggregate reporting method.

The dashboard must agree with Supplier Khata.

Do not introduce another payable source of truth.

---

# 20. Inventory Value

Inspect the current Inventory model and weighted-average-cost implementation.

If inventory valuation can be safely derived from existing fields:

```text
inventory value =
quantity × weighted_average_cost
```

provide it.

If the architecture does not support reliable valuation for all inventory types, do not fabricate it.

Clearly distinguish:

```text
inventory quantity
inventory estimated value
```

if necessary.

Do not redesign inventory accounting.

---

# 21. Low Stock

Inspect the current Inventory and ProductVariant models.

If there is already a reorder/minimum-stock field:

```text
reorder_level
minimum_stock
```

use it.

If no such field exists:

**Do not invent a new inventory threshold system in Step 9.**

Either omit the low-stock metric or implement only what the existing architecture supports.

Do not modify Inventory merely to produce a dashboard card.

---

# 22. Reporting APIs

Add a focused read-only router:

```text
app/api/reporting.py
```

Include it in `main.py`.

Potential endpoints:

```text
GET /reports/trial-balance
GET /reports/profit-and-loss
GET /reports/balance-sheet
GET /reports/dashboard
```

Use query parameters where appropriate:

```text
start_date
end_date
```

Do not expose arbitrary SQL/report-builder functionality.

Do not create mutation endpoints.

---

# 23. API Contracts

Create:

```text
app/schemas/reporting.py
```

Use explicit response schemas.

Do not return unstructured dictionaries everywhere.

Responses should contain clear financial fields.

Use strings/Decimal serialization consistently with the existing API conventions.

Inspect existing schemas before deciding exact representation.

---

# 24. Tenant Isolation

Every report must be scoped to:

```text
shop_id
```

No report may accidentally aggregate across shops.

For example:

```text
Shop A dashboard
```

must never include:

```text
Shop B sales
Shop B purchases
Shop B ledger entries
Shop B inventory
```

The shop must continue to be resolved server-side using the existing development dependency.

Do not trust a frontend-supplied shop ID.

Do not introduce authentication/JWT in Step 9.

---

# 25. Performance

Reports should use SQL aggregation where possible.

Avoid:

```python
all_sales = await session.execute(...)
for sale in all_sales:
    ...
```

when the same calculation can be performed using SQL `SUM`, `COUNT`, `GROUP BY`, etc.

However, do not over-optimize prematurely.

Keep queries understandable and testable.

Add indexes only where a report query genuinely needs one and an appropriate index does not already exist.

---

# 26. No Caching Yet

Do NOT create:

```text
daily_report_cache
dashboard_cache
monthly_report_cache
```

in Step 9.

Correctness is more important than premature caching.

If performance becomes an issue later, caching/materialized reporting can be a separate step.

---

# 27. No New Accounting Postings

This is critical.

Step 9 must NOT modify:

```text
post_sale()
post_purchase()
post_customer_payment()
post_supplier_payment()
```

unless a bug is discovered that directly prevents reporting from being correct.

Do not add:

```text
COGS posting
expense posting
tax posting
inventory adjustment posting
```

as part of reporting.

Step 9 is read-only.

---

# 28. Financial Statement Consistency

The reports must agree with each other.

For example:

```text
Trial Balance
      ↓
Revenue accounts
      ↓
P&L Revenue
```

and:

```text
Accounts Receivable
      ↓
Balance Sheet AR
      ↓
Customer Khata aggregate
```

and:

```text
Accounts Payable
      ↓
Balance Sheet AP
      ↓
Supplier Khata aggregate
```

Where differences exist because of V1 limitations, document them rather than hiding them.

---

# 29. Tests

Create:

```text
tests/test_reporting.py
```

At minimum test:

## Trial Balance

* empty shop
* system accounts
* balanced ledger
* multiple accounts
* debit totals
* credit totals
* account balances
* date filtering
* tenant isolation
* Decimal precision
* trial balance totals equal

## Profit & Loss

Test:

```text
Revenue
COGS
Gross Profit
```

Example:

```text
Sale Revenue = 10,000
COGS = 6,000

Gross Profit = 4,000
```

Verify COGS uses:

```text
SaleItem.cost_price
```

rather than current inventory cost.

Test:

* multiple sales
* cancelled sales excluded
* date filtering
* tenant isolation
* Decimal precision

## Balance Sheet

Test:

```text
Assets
Liabilities
Equity
```

and:

```text
Assets = Liabilities + Equity
```

where the current architecture supports that invariant.

Test account-type classification.

## Dashboard

Test:

* today's sales
* sales count
* today's payments
* today's purchases
* receivables
* payables
* inventory metrics where supported
* low stock where supported
* tenant isolation

## Cross-report consistency

Verify:

```text
Dashboard receivables == Customer Khata aggregate
Dashboard payables == Supplier Khata aggregate
```

where applicable.

## Tenant isolation

Explicitly create two shops and verify every report only sees its own data.

## No mutation

Verify that requesting reports does not change:

```text
Sales
Purchases
Payments
Inventory
Accounts
LedgerEntries
```

---

# 30. API Tests

Test:

```text
GET /reports/trial-balance
GET /reports/profit-and-loss
GET /reports/balance-sheet
GET /reports/dashboard
```

Verify:

* successful responses
* response schema
* date parameters
* tenant isolation
* invalid date ranges according to existing conventions

Do not add authentication.

---

# 31. Database Migration

Prefer:

```text
NO MIGRATION
```

for Step 9.

Reporting should use the existing database schema.

Only create a migration if inspection proves an existing index/constraint is genuinely required.

Do NOT create reporting tables.

Do NOT modify the accounting schema simply to make reporting easier.

---

# 32. Documentation

Update README / architecture documentation with:

```text
Step 9 — Reporting & Financial Statements
```

Document:

### Trial Balance

Derived from:

```text
accounts + ledger_entries
```

### P&L

```text
Revenue
-
historical SaleItem COGS
```

with the current limitation that COGS is not yet an accounting ledger posting.

### Balance Sheet

Derived from:

```text
asset + liability + equity account balances
```

### Dashboard

Derived from existing operational/accounting sources.

Clearly document that Step 9 introduces:

```text
read-only reporting
```

and no new accounting posting rules.

---

# 33. Important V1 Limitations

Do not hide these limitations.

If the repository currently lacks:

* Expense domain
* COGS ledger postings
* opening balances
* fiscal periods
* tax accounting

then report them clearly.

Do NOT solve all of them in Step 9.

The goal is a correct V1 reporting layer over the architecture that currently exists.

---

# 34. Things NOT To Implement

Strictly do NOT implement:

* Expense management
* COGS posting
* tax accounting
* GST/VAT
* accounting periods
* closing periods
* opening-balance workflow
* manual journal entries
* purchase returns
* sales returns
* depreciation
* payroll
* cash-flow accounting
* audit-log redesign
* accounting cache
* materialized reporting tables
* AI analytics
* forecasting
* authentication/JWT
* RBAC
* React dashboard
* frontend redesign

Those are future steps.

---

# 35. Regression Safety

Run the entire existing test suite.

The known Step 8 result was:

```text
Previous tests: 173
New Step 8 tests: 28
Total: 201
```

Step 9 must preserve all of them.

Report the actual final count rather than assuming it.

Run where available:

```text
pytest
ruff
mypy
alembic check
application startup
/health
```

Also verify that no migration is generated unnecessarily.

If `ruff` or `mypy` are unavailable, explicitly state that.

Never fabricate validation results.

---

# 36. Final Report

When finished, report:

## Implemented

List all files added/modified.

## Reports

List:

```text
Trial Balance
Profit & Loss
Balance Sheet
Dashboard
```

and describe their sources of truth.

## P&L COGS

Explicitly state how COGS is calculated.

Confirm whether it comes from:

```text
SaleItem.cost_price × quantity
```

or another existing source.

## Expenses

State whether an Expense domain exists and exactly how P&L handles expenses.

## Balance Sheet

Show how Assets, Liabilities and Equity are derived.

## Dashboard

List all metrics actually implemented.

## APIs

List all new endpoints.

## Database

State whether a migration was required.

## Tenant Isolation

Explain how reports are scoped to each Shop.

## Tests

Report:

```text
Previous tests: X
New Step 9 tests: Y
Total: Z
```

and list the actual validation results.

Do not fabricate anything.

---

# STOP CONDITION

After completing Step 9:

1. Run tests and validation.
2. Report the actual results.
3. Clearly document V1 reporting limitations.
4. Suggest **Step 10 only**.
5. Do NOT implement Step 10.
6. STOP.
