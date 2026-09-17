# Step 10 — Expense Domain + COGS Ledger Postings

You are continuing development of the existing fabric/fashion retail management backend.

Completed:

* Step 1 — Backend Foundation
* Step 2 — Catalog
* Step 3 — Inventory
* Step 4 — Suppliers + Purchases
* Step 5 — Sales / POS
* Step 6 — Customer Khata / Receivables
* Step 7 — Supplier Payables / Supplier Khata
* Step 8 — General Ledger / Double-Entry Accounting
* Step 9 — Reporting & Financial Statements

# IMPORTANT WORKING RULE

Work ONLY on:

# Step 10 — Expense Domain + COGS Ledger Postings

Before changing anything:

1. Inspect the repository.
2. Read the architecture/design Markdown documentation.
3. Read `db_arch.md`.
4. Inspect:

   * Sale
   * SaleItem
   * Payment
   * Purchase
   * PurchaseItem
   * Inventory
   * InventoryMovement
   * Account
   * LedgerEntry
   * accounting service
   * reporting service
   * Customer Khata
   * Supplier Khata
5. Understand the existing transaction boundaries.
6. Do NOT redesign Steps 1–9.
7. Preserve existing tenant isolation.
8. Preserve existing Decimal/money conventions.
9. Keep the application as a modular monolith.
10. Do not implement unrelated accounting features.

The objective is:

```text
Step 10
    │
    ├── Expense Domain
    │
    └── COGS Ledger Posting
             │
             ↓
      Fully accounting-aware P&L
```

---

# 1. Core Objective

After Step 10:

### Expenses

The system should have a proper Expense domain and corresponding accounting postings.

### COGS

Every qualifying sale should generate:

```text
Debit   Cost of Goods Sold
Credit  Inventory
```

using the historical cost already captured on `SaleItem`.

This means P&L can become fully ledger-derived for:

```text
Revenue
COGS
Expenses
```

and Net Profit can be calculated.

---

# 2. Do NOT Redesign Existing Accounting

Keep the existing Step 8 architecture:

```text
accounts
ledger_entries
```

Do NOT introduce:

```text
journals
journal_lines
account_balances
posting_batches
expense_ledger
cogs_entries
```

unless `db_arch.md` explicitly requires them.

The existing `_post_group()` / posting mechanism should remain the central accounting mechanism.

Reuse it.

---

# 3. COGS System Account

Add a system account for COGS.

Inspect the existing account-code conventions first.

A likely account is:

```text
5000 Cost of Goods Sold
```

but use the code specified by the architecture if one exists.

Account type:

```text
EXPENSE
```

It must be:

```text
is_system = true
```

and created by the existing system-account initialization mechanism.

Do not create a second COGS account for every shop.

Each shop gets its own system COGS account.

---

# 4. COGS Posting Rule

For every qualifying sale:

```text
Debit   Cost of Goods Sold
Credit  Inventory
```

Amount:

```text
SUM(
    SaleItem.quantity
    × SaleItem.cost_price
)
```

Use the immutable historical `SaleItem.cost_price`.

Do NOT calculate COGS using:

```text
current inventory weighted average
```

after the sale.

Do NOT recalculate historical costs.

Example:

```text
Sale:
2 meters × cost_price 500
3 meters × cost_price 700

COGS = 1000 + 2100
     = 3100
```

Ledger:

```text
Cost of Goods Sold     Debit   3100
Inventory              Credit  3100
```

The posting must balance.

---

# 5. COGS Must Be Posted Inside the Sale Transaction

This is critical.

Inspect the current `SaleService.create_sale()` transaction.

The intended lifecycle should become conceptually:

```text
Create Sale
    ↓
Validate sale
    ↓
Snapshot cost_price
    ↓
Remove inventory
    ↓
Create Payment rows if applicable
    ↓
Post Revenue / Cash / AR
    ↓
Post COGS / Inventory
    ↓
COMMIT
```

Do NOT create COGS later through a background job.

Do NOT allow:

```text
Sale exists
+
Inventory reduced
+
Revenue posted
-
COGS missing
```

The sale transaction and its accounting postings must remain atomic.

If COGS posting fails, the entire sale transaction must roll back.

---

# 6. Existing Sale Posting Must Remain Correct

Do not replace the existing revenue posting.

For:

```text
Sale total = 10,000
Paid = 4,000
Due = 6,000
COGS = 6,000
```

the accounting should become:

```text
Cash                   Debit   4,000
Accounts Receivable    Debit   6,000
Sales Revenue          Credit 10,000

Cost of Goods Sold     Debit   6,000
Inventory              Credit  6,000
```

Two balanced posting groups may exist:

```text
Revenue transaction:
Debit = 10,000
Credit = 10,000

COGS transaction:
Debit = 6,000
Credit = 6,000
```

or the existing architecture may represent them as one posting group.

Follow `db_arch.md` and the existing accounting implementation.

Do not invent a new posting architecture.

---

# 7. Zero-Cost Sales

Inspect the existing domain constraints.

If `SaleItem.cost_price` can legitimately be zero, determine how the accounting service should behave.

Do NOT create invalid ledger entries such as:

```text
Debit COGS = 0
Credit Inventory = 0
```

because Step 8 explicitly prevents zero-value ledger lines.

If zero-cost inventory is allowed, the COGS posting may need to be omitted while the sale revenue posting remains valid.

Document the chosen behavior.

Do not silently invent a non-zero cost.

---

# 8. Duplicate COGS Posting Protection

COGS must be idempotent.

Calling the sale-posting operation twice must not produce:

```text
COGS 6,000
COGS 6,000
```

for the same sale.

Use the existing Step 8 reference mechanism.

For example, if appropriate:

```text
reference_type = SALE_COGS
reference_id   = sale.id
```

or follow the exact reference conventions already implemented.

Do not add a mutable `cogs_posted` flag unless the architecture explicitly requires it.

Use database uniqueness and/or the existing `_post_group()` duplicate-protection mechanism.

---

# 9. COGS and Sale Status

Only qualifying sales should generate COGS.

Inspect the existing sale status rules.

At minimum:

```text
COMPLETED
PARTIAL
```

should remain consistent with the accounting rules already established.

Do NOT post COGS for:

```text
CANCELLED
```

or other non-qualifying statuses.

`RETURNED` currently has no actual return workflow.

Do not invent a sales-return accounting workflow in Step 10.

Keep the qualifying rule centralized.

---

# 10. Expense Domain

Create an Expense domain.

Inspect the existing project conventions before choosing the exact schema.

At minimum an Expense should represent:

```text
Expense
├── id
├── shop_id
├── category
├── description
├── amount
├── payment_method
├── expense_date / created_at
└── timestamps
```

Use UUIDs.

Use Decimal / PostgreSQL NUMERIC.

Do not use floats.

---

# 11. Expense Categories

Use a flexible but controlled approach.

Do NOT create dozens of specialized models.

A simple category field or enum is acceptable if consistent with the architecture.

Potential categories include:

```text
RENT
SALARY
UTILITIES
TRANSPORT
MARKETING
MAINTENANCE
SUPPLIES
OTHER
```

Inspect `db_arch.md` first.

If the architecture prefers a separate ExpenseCategory table, follow it.

Otherwise prefer a small enum/category representation for V1.

Do not build category management UI in this step.

---

# 12. Expense Payment Semantics

This requires careful inspection.

Determine whether Step 10 should support:

### V1 immediate paid expenses

```text
Expense = 5,000
Payment Method = CASH
```

with:

```text
Debit   Expense
Credit  Cash
```

or:

### Paid + unpaid expenses

If the existing architecture explicitly supports expense liabilities, follow it.

Do NOT automatically reuse Accounts Payable for arbitrary expenses without checking the architecture.

`Accounts Payable` currently represents supplier-related obligations.

Do not silently turn it into a generic creditor account.

For V1, if no expense-payable workflow exists, prefer a clearly defined **immediate-payment expense model** rather than inventing another liability subsystem.

Document this limitation.

---

# 13. Expense Accounting Rule

For an immediate paid expense:

```text
Debit   Expense Account
Credit  Cash / Bank
```

Example:

```text
Rent = 30,000

Debit   Rent Expense     30,000
Credit  Cash             30,000
```

For a bank-paid expense:

```text
Debit   Expense Account
Credit  Bank
```

The posting must balance.

---

# 14. Expense Accounts

Do NOT necessarily create one ledger account for every individual expense category unless the architecture requires it.

Inspect `db_arch.md`.

A sensible V1 may use a small set of system expense accounts or category-to-account mappings.

For example:

```text
5100 Rent Expense
5200 Utilities Expense
5300 Salaries Expense
5400 Marketing Expense
5900 Other Expense
```

However, these are examples.

Follow the architecture's intended account structure.

Do not create an unnecessarily complicated chart of accounts.

---

# 15. Expense Service

Create:

```text
app/services/expenses.py
```

At minimum implement functionality equivalent to:

```python
create_expense(...)
get_expense(...)
list_expenses(...)
```

and whatever minimal read/update behavior the architecture requires.

The service must:

* validate shop
* validate amount
* validate category
* validate payment method
* resolve the appropriate accounts
* create the expense
* post its ledger entries
* keep the operation atomic

Do not put accounting logic directly inside the API route.

Reuse:

```text
app/services/accounting.py
```

for posting.

---

# 16. Expense API

Create a focused API.

Likely:

```text
POST /expenses
GET  /expenses
GET  /expenses/{expense_id}
```

Only implement endpoints justified by the existing architecture.

Do not build a huge CRUD system.

If update/delete is supported, carefully consider accounting history.

Do NOT allow destructive deletion of an already-posted financial transaction unless the architecture already has reversal/void semantics.

If V1 does not have reversal accounting, it is safer to make posted expenses immutable.

Follow existing project conventions.

---

# 17. Expense Schema

Create:

```text
app/schemas/expenses.py
```

Use explicit Pydantic request/response models.

Do not expose ORM objects directly.

Use Decimal-compatible serialization consistent with existing APIs.

---

# 18. Expense Tenant Isolation

Every Expense belongs to exactly one Shop.

Every expense query must filter:

```text
shop_id
```

The shop must come from the existing server-side dependency.

Do NOT trust:

```text
shop_id
```

from the frontend request body.

A user from Shop A must never:

* read Shop B expenses
* create an expense for Shop B
* access Shop B ledger accounts

---

# 19. Expense Database Constraints

Use database constraints for:

* amount > 0
* valid category/payment method
* tenant ownership
* appropriate account relationships

Do not rely exclusively on Python validation.

Use existing project money precision.

---

# 20. Reporting Changes

Update `app/services/reporting.py`.

The Step 9 P&L currently reports:

```text
Revenue
COGS
Gross Profit
Expenses unavailable
Net Profit unavailable
```

After Step 10 it should become:

```text
Revenue
- COGS
----------------
Gross Profit
- Expenses
----------------
Net Profit
```

Revenue:

```text
ledger revenue accounts
```

COGS:

```text
ledger COGS account(s)
```

Expenses:

```text
ledger EXPENSE accounts
```

Net Profit:

```text
Revenue - COGS - Expenses
```

The goal is for P&L to become **fully ledger-derived**.

Do not continue calculating COGS from SaleItem in the final Step 10 P&L if the COGS ledger posting is now available.

The historical SaleItem cost remains the source used to CREATE the COGS posting.

The ledger becomes the reporting source.

---

# 21. COGS Reporting Consistency

Add tests proving:

```text
P&L COGS
==
COGS ledger balance
```

for the same reporting period.

Also prove that:

```text
SaleItem.cost_price
```

is used at posting time.

Example:

```text
Initial inventory cost = 500

Sale:
quantity = 2
cost_price = 500

COGS posted = 1,000
```

Later purchase:

```text
new inventory cost = 1,000
```

must NOT change the historical COGS posting.

---

# 22. Gross Profit

After Step 10:

```text
Gross Profit =
Revenue - COGS
```

Example:

```text
Revenue = 100,000
COGS    = 65,000

Gross Profit = 35,000
```

This should be derived from ledger data.

---

# 23. Net Profit

If Expense postings exist:

```text
Net Profit =
Revenue
- COGS
- Expenses
```

Example:

```text
Revenue  = 100,000
COGS     = 60,000
Expenses = 15,000

Gross Profit = 40,000
Net Profit   = 25,000
```

Use Decimal.

Do not use floats.

---

# 24. Balance Sheet Effect

COGS posting:

```text
Debit COGS
Credit Inventory
```

changes:

```text
Inventory ↓
Expenses/COGS ↑
```

Expense posting:

```text
Debit Expense
Credit Cash/Bank
```

changes:

```text
Expense ↑
Cash/Bank ↓
```

The existing Balance Sheet should automatically reflect these ledger changes.

Do not create special Balance Sheet calculations for COGS or expenses.

---

# 25. Cash/Bank Account Mapping

Reuse the existing payment-method mapping from Step 8.

Inspect the current implementation.

Do not create a second mapping system.

For example:

```text
cash     → Cash
bank     → Bank
```

For:

```text
card
jazzcash
easypaisa
other
```

follow the existing accounting mapping.

Keep the mapping centralized.

---

# 26. Transaction Safety

Expense creation must be atomic:

```text
Create Expense
    ↓
Post Expense Ledger
    ↓
COMMIT
```

If posting fails:

```text
ROLLBACK
```

No expense should exist without its corresponding ledger posting.

Likewise:

```text
Sale
+
Revenue posting
+
COGS posting
```

must remain one atomic business transaction.

No partial financial state.

---

# 27. Existing Sale Service

Modify `app/services/sales.py` only as much as necessary.

The existing SaleService already:

* validates sale
* calculates totals
* snapshots cost
* removes stock
* records payments
* posts accounting

Extend it to also post COGS.

Do NOT duplicate sale logic.

Do NOT rewrite SaleService.

Reuse the existing `SaleItem.cost_price`.

---

# 28. Inventory Integrity

Do not directly modify Inventory from the accounting service.

The accounting service should only create:

```text
Debit COGS
Credit Inventory
```

ledger entries.

Inventory quantity remains controlled by:

```text
app/services/inventory.py
```

The economic event therefore has two synchronized representations:

```text
Inventory service
    → physical stock quantity

Accounting service
    → financial inventory value / COGS
```

Do not make the accounting service responsible for stock quantities.

---

# 29. Important Inventory Accounting Limitation

Inspect how inventory adjustments currently work.

If the existing system supports:

```text
DAMAGE
ADJUSTMENT
CUSTOMER_RETURN
SUPPLIER_RETURN
```

do NOT automatically add accounting postings for all of them in Step 10.

Only implement COGS for qualifying Sales.

Inventory adjustment accounting can be a later step unless explicitly required by `db_arch.md`.

---

# 30. Database Migration

A migration will likely be required for:

```text
expenses
```

and potentially additional system accounts.

Create the smallest migration necessary.

Do not alter unrelated Step 1–9 tables unless required.

Do not create:

```text
expense_balances
expense_ledger
cogs_entries
```

---

# 31. Tests — COGS

Extend/create:

```text
tests/test_accounting.py
```

or a focused test module if that better matches the existing structure.

At minimum test:

### Sale COGS

* one-item sale
* multiple-item sale
* decimal quantity
* decimal cost
* correct COGS amount
* correct Inventory credit
* correct COGS debit
* balanced posting

### Historical cost

Prove that a future purchase changing weighted-average cost does NOT change the COGS already posted for an earlier sale.

### Duplicate protection

Posting the same sale twice must not duplicate COGS.

### Cancelled sale

Cancelled sales must not receive COGS postings.

### Transaction rollback

If COGS posting fails:

```text
sale
inventory reduction
revenue posting
COGS posting
```

must all roll back.

### Tenant isolation

Shop A cannot create/read COGS postings against Shop B.

---

# 32. Tests — Expenses

Create:

```text
tests/test_expenses.py
```

At minimum test:

### Creation

* valid expense
* amount validation
* category validation
* payment method validation
* Decimal precision

### Accounting

For:

```text
Expense = 10,000 cash
```

verify:

```text
Expense account    Debit 10,000
Cash               Credit 10,000
```

For bank:

```text
Expense account    Debit 10,000
Bank               Credit 10,000
```

### Double-entry

Verify:

```text
debits == credits
```

### Atomicity

If ledger posting fails:

```text
Expense is rolled back
Ledger entries are rolled back
```

### Tenant isolation

Two shops must remain completely isolated.

### History

A posted expense must not be destructively deleted if the architecture does not support accounting reversals.

---

# 33. Tests — Reporting

Update reporting tests.

Verify:

### P&L

```text
Revenue
COGS
Gross Profit
Expenses
Net Profit
```

are all correct.

### Ledger consistency

Verify:

```text
P&L Revenue == ledger revenue
P&L COGS == ledger COGS
P&L Expenses == ledger expenses
```

### Historical COGS

Future purchases do not change previous-period COGS.

### Balance Sheet

Verify the accounting effects of:

* COGS
* expenses

appear naturally through account balances.

### Dashboard

Update dashboard metrics if necessary.

At minimum verify that gross profit now uses ledger COGS.

If net profit is currently absent, add it where appropriate.

---

# 34. API Tests

Test the new expense endpoints.

Also verify existing report endpoints now expose:

```text
expenses
net_profit
```

where appropriate.

Do not break existing response contracts unnecessarily.

---

# 35. API Scope

Do NOT create:

```text
POST /ledger-entries
```

or any arbitrary accounting mutation API.

Users create:

```text
Expense
```

and the application creates the accounting entries.

Similarly:

```text
Sale
```

creates Revenue + COGS postings.

Accounting remains controlled by domain services.

---

# 36. No Manual COGS Editing

Do NOT expose an API allowing users to manually modify:

```text
COGS
Inventory accounting value
Revenue
Expense ledger entries
```

Ledger history must remain controlled.

---

# 37. Expense Editing / Deletion

Inspect existing financial-domain conventions.

Because an Expense creates accounting entries:

Do NOT casually allow:

```text
DELETE /expenses/{id}
```

after posting.

If the current architecture has no reversal mechanism, treat posted expenses as immutable.

If the user needs correction later, a future accounting-reversal step can handle it.

Do not invent reversals in Step 10 unless explicitly required.

---

# 38. Documentation

Update README / architecture documentation.

Document:

## Expenses

```text
Expense
   ↓
Expense Account
   ↓
Cash / Bank
```

## COGS

```text
Sale
   ↓
COGS = historical SaleItem cost
   ↓
Debit COGS
Credit Inventory
```

## P&L

```text
Revenue
- COGS
----------------
Gross Profit
- Expenses
----------------
Net Profit
```

Clearly state that:

```text
SaleItem.cost_price
```

is the source used when creating the COGS posting.

After posting, financial reporting uses the ledger.

---

# 39. V1 Limitations

Do not silently solve these in Step 10:

* expense payables/creditors
* expense returns/refunds
* COGS for sales returns
* inventory adjustment accounting
* supplier returns accounting
* customer returns accounting
* tax accounting
* opening balances
* fiscal periods
* closing periods
* depreciation
* payroll
* cash-flow statement
* manual journal entries

Document them as future capabilities if appropriate.

---

# 40. Regression Safety

Run the complete test suite.

Known baseline before Step 10:

```text
201 tests after Step 8
232 tests after Step 9
```

Do NOT assume the final count.

Report:

```text
Previous tests: X
New Step 10 tests: Y
Total: Z
```

Run where available:

```text
pytest
ruff
mypy
alembic check
migration downgrade → upgrade
application startup
/health
```

If `ruff` or `mypy` are not installed, clearly state that.

Never fabricate results.

---

# 41. Final Verification

Before declaring Step 10 complete, verify this complete flow:

```text
Purchase
    ↓
Inventory increases
    ↓
Inventory accounting increases
    ↓
Accounts Payable increases


Sale
    ↓
Inventory decreases
    ↓
Revenue increases
    ↓
Receivable/Cash changes
    ↓
COGS increases
    ↓
Inventory accounting decreases


Customer Payment
    ↓
Cash/Bank increases
    ↓
Accounts Receivable decreases


Supplier Payment
    ↓
Accounts Payable decreases
    ↓
Cash/Bank decreases


Expense
    ↓
Expense increases
    ↓
Cash/Bank decreases
```

Then verify:

```text
P&L
    Revenue
    - COGS
    - Expenses
    = Net Profit
```

and:

```text
Balance Sheet
    Assets
    = Liabilities + Equity
```

where supported by the current opening-balance architecture.

---

# 42. Final Report

When finished, report:

## Implemented

List all files added/modified.

## Expense Domain

Explain:

* model
* categories
* payment behavior
* APIs
* accounting behavior

## COGS

Explain:

* calculation source
* posting timing
* ledger accounts
* duplicate protection
* transaction safety

## Accounting

List all new system accounts.

## P&L

Confirm whether it is now:

```text
Revenue - COGS - Expenses
```

and whether all three are ledger-derived.

## Reporting

Explain any changes to:

* Trial Balance
* P&L
* Balance Sheet
* Dashboard

## Database

List migrations and constraints.

## Tenant Isolation

Explain how Shop A and Shop B remain isolated.

## Tests

Report:

```text
Previous tests: X
New Step 10 tests: Y
Total: Z
```

Include actual validation results.

## Limitations

Explicitly list what remains outside Step 10.

---

# STOP CONDITION

After completing Step 10:

1. Run the complete test suite.
2. Run database migration checks.
3. Verify COGS and Expense accounting.
4. Verify P&L is consistent with the ledger.
5. Report actual results.
6. Suggest **Step 11 only**.
7. Do NOT implement Step 11.
8. STOP.
