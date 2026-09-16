# Step 8 — General Ledger / Double-Entry Accounting

You are continuing development of the existing fabric/fashion retail management backend.

This is the next architectural step after:

* Step 1 — Backend Foundation
* Step 2 — Catalog
* Step 3 — Inventory
* Step 4 — Suppliers & Purchases
* Step 5 — Sales / POS
* Step 6 — Customer Khata / Receivables
* Step 7 — Supplier Payables / Supplier Khata

## IMPORTANT WORKING RULE

Work ONLY on:

# Step 8 — General Ledger / Double-Entry Accounting

Before changing anything:

1. Inspect the existing repository.
2. Read the project's architecture/design Markdown documentation.
3. Pay particular attention to `db_arch.md`, especially the accounting / "two ledgers" architecture around §36–38.
4. Inspect the existing implementations of:

   * Sale
   * SaleItem
   * Payment
   * Purchase
   * PurchaseItem
   * Customer
   * Supplier
   * Inventory
   * Customer Receivables / Step 6
   * Supplier Payables / Step 7
5. Understand existing tenant isolation and transaction patterns.
6. Do NOT redesign Steps 1–7.
7. Do NOT introduce microservices.
8. Do NOT introduce a separate accounting database.
9. Keep accounting as a module inside the existing modular monolith.
10. Preserve all existing tests and behavior.

The accounting layer must sit ON TOP of the existing operational transaction system.

---

# 1. Core Accounting Architecture

The architecture uses two core accounting tables:

```text
accounts
ledger_entries
```

Do NOT create a large accounting schema yet.

The intended architecture is:

```text
                    Operational System
                           │
          ┌────────────────┼────────────────┐
          │                │                │
        Sales           Purchases        Payments
          │                │                │
          └────────────────┼────────────────┘
                           ↓
                  Accounting Service
                           ↓
                  ┌─────────────────┐
                  │     accounts    │
                  └────────┬────────┘
                           │
                           ↓
                  ┌─────────────────┐
                  │ ledger_entries  │
                  └─────────────────┘
                           │
                           ↓
                    General Ledger
```

The database stores accounting entries.

The accounting service is responsible for generating valid double-entry postings.

---

# 2. No Separate Balance Tables

Do NOT create:

```text
account_balances
general_ledger_balances
customer_balances
supplier_balances
journal_balances
```

Account balances must be derived from ledger entries.

Conceptually:

```text
Debit balance contribution  = debit
Credit balance contribution = credit
```

The exact normal balance presentation should be determined by account type.

Do not store mutable account balances in this step.

---

# 3. Account Model

Create:

```text
app/models/account.py
```

with an `Account` model.

Each account belongs to a shop.

Conceptually:

```text
Account
├── id
├── shop_id
├── code
├── name
├── account_type
├── is_system
├── is_active
├── created_at
└── updated_at
```

Use UUIDs consistently with the existing project.

Use an enum for account type.

At minimum support:

```text
ASSET
LIABILITY
EQUITY
REVENUE
EXPENSE
```

Do not add dozens of account types unless the existing architecture requires them.

---

# 4. Account Codes

The system needs a small default chart of accounts.

Inspect `db_arch.md` and existing project conventions before deciding exact codes.

The minimum operational accounts should represent:

```text
Cash
Bank
Accounts Receivable
Inventory
Accounts Payable
Sales Revenue
```

You may also need a basic equity/opening-balance account if required by the accounting architecture.

Do NOT create an unnecessarily large chart of accounts.

The important principle is that every shop gets its own accounts.

Example concept:

```text
1000 Cash
1010 Bank
1100 Accounts Receivable
1200 Inventory

2000 Accounts Payable

3000 Owner Equity

4000 Sales Revenue
```

These are examples only.

If the project's architecture document specifies different codes/names, follow the architecture document.

---

# 5. System Accounts

Some accounts should be system-managed.

For example:

```text
Cash
Bank
Accounts Receivable
Inventory
Accounts Payable
Sales Revenue
```

Use:

```text
is_system = true
```

for accounts that are created/managed by the application.

Users should not accidentally delete or deactivate required system accounts.

Do not build a full account-management UI in this step.

---

# 6. Tenant Isolation

Every Account MUST belong to exactly one Shop.

Every LedgerEntry MUST also belong to exactly one Shop.

Never allow:

```text
Shop A ledger entry
→ Shop B account
```

The database should enforce the relationship where practical.

Use composite tenant-safe foreign keys if they are consistent with the existing architecture.

For example:

```text
(shop_id, account_id)
```

should resolve to the same shop.

Do not rely only on application-level validation for this critical accounting relationship.

---

# 7. Ledger Entry Model

Create:

```text
app/models/ledger_entry.py
```

A ledger entry represents one side of a double-entry posting.

Conceptually:

```text
LedgerEntry
├── id
├── shop_id
├── account_id
├── debit
├── credit
├── reference_type
├── reference_id
├── description
└── created_at
```

Use Decimal / PostgreSQL NUMERIC for money.

Never use float.

---

# 8. Debit / Credit Integrity

A ledger entry must represent either:

```text
debit > 0
credit = 0
```

OR:

```text
debit = 0
credit > 0
```

Never allow:

```text
debit > 0 AND credit > 0
```

and never allow:

```text
debit = 0 AND credit = 0
```

Add appropriate database-level checks.

Use the project's existing money precision convention.

For example:

```text
NUMERIC(14,2)
```

if consistent with the existing financial models.

---

# 9. Double-Entry Invariant

Every accounting transaction must balance.

For a posting group:

```text
SUM(debits) = SUM(credits)
```

This is the most important invariant in Step 8.

The accounting service must refuse to persist an unbalanced posting.

Do NOT rely only on tests.

The service itself must validate that the posting is balanced before writing it.

If the architecture can support database-level protection for a posting group without introducing another table, use it.

Otherwise enforce the invariant transactionally in the accounting service.

Do NOT create a `Journal` table merely to solve this unless the existing architecture document explicitly requires one.

The intended Step 8 architecture is still:

```text
accounts
ledger_entries
```

---

# 10. Posting Groups

Because one business event can create multiple ledger lines, all lines belonging to the same accounting event must be identifiable.

Use the existing architecture's preferred mechanism.

A practical approach is:

```text
reference_type
reference_id
```

where all ledger lines generated from one source transaction share the same reference.

For example:

```text
reference_type = "SALE"
reference_id   = sale.id
```

or:

```text
reference_type = "PURCHASE"
reference_id   = purchase.id
```

Inspect `db_arch.md` before finalizing this.

Do not invent a separate journal abstraction if the architecture explicitly intends only `accounts + ledger_entries`.

---

# 11. Accounting Service

Create:

```text
app/services/accounting.py
```

Keep accounting posting logic centralized.

At minimum provide operations equivalent to:

```python
post_sale(...)
post_purchase(...)
post_customer_payment(...)
post_supplier_payment(...)
get_account_balance(...)
get_account_ledger(...)
```

Use the project's existing async SQLAlchemy conventions.

Do not put accounting logic directly inside API routes.

Do not scatter debit/credit logic throughout models.

---

# 12. Sale Posting

A completed/qualifying Sale must produce balanced accounting entries.

The architecture specified in `db_arch.md` is conceptually:

```text
Sale
    → Revenue
    → Accounts Receivable / Cash
```

The exact debit/credit direction must follow normal double-entry accounting.

For a credit sale:

```text
Debit   Accounts Receivable
Credit  Sales Revenue
```

For a cash sale:

```text
Debit   Cash
Credit  Sales Revenue
```

For a sale containing both paid and due amounts:

```text
Debit   Cash                 paid amount
Debit   Accounts Receivable  due amount
Credit  Sales Revenue        total sale
```

The three lines must balance:

```text
paid + due = total
```

Do NOT create duplicate accounting entries if the same Sale is posted twice.

Posting must be idempotent.

If a Sale has already been posted, the service should detect that state and refuse or safely no-op according to the existing architecture.

Do not invent a mutable `posted` flag unless the architecture requires it.

Prefer detecting an existing posting using the reference information.

---

# 13. Purchase Posting

A qualifying Purchase should post:

```text
Debit   Inventory
Credit  Accounts Payable
```

For example:

```text
Purchase = 100,000

Debit   Inventory         100,000
Credit  Accounts Payable  100,000
```

Do NOT introduce COGS or inventory valuation accounting in this step unless `db_arch.md` explicitly requires it.

Step 8 should implement the accounting architecture already defined, not expand it.

If a purchase has an existing paid amount, inspect the architecture and determine whether the purchase posting should split:

```text
Debit  Inventory
Credit Accounts Payable
```

followed by a separate payment posting, or whether the original purchase event needs to account for the paid portion.

Prefer the architecture that keeps Payment as the settlement event.

Do not duplicate the same economic event.

---

# 14. Customer Payment Posting

A customer payment settles receivables.

The intended accounting is:

```text
Debit   Cash / Bank
Credit  Accounts Receivable
```

The payment method determines the destination asset account.

For example:

```text
Cash payment:

Debit   Cash
Credit  Accounts Receivable
```

Bank/card/digital payment methods should map according to the existing architecture.

Do not create one new account for every payment method unless the architecture requires it.

Inspect the existing `PaymentMethod` enum:

```text
cash
card
bank
jazzcash
easypaisa
other
```

and create a sensible deterministic mapping.

If the architecture document specifies how card/JazzCash/Easypaisa should map, follow it.

---

# 15. Supplier Payment Posting

Supplier settlement should be:

```text
Debit   Accounts Payable
Credit  Cash / Bank
```

Example:

```text
Supplier payment = 30,000

Debit   Accounts Payable  30,000
Credit  Cash              30,000
```

Again, the payment method determines the cash/bank-side account.

Do not create a separate supplier-payment ledger model.

Reuse the existing generic Payment model.

---

# 16. Important Separation From Step 6/7

Do NOT replace:

```text
Customer Khata
Supplier Khata
```

with the General Ledger.

They serve different purposes.

Customer Khata remains:

```text
Sales + customer Payments
```

Supplier Khata remains:

```text
Purchases + supplier Payments
```

General Ledger becomes the accounting representation of those same business events.

Conceptually:

```text
Operational source
        ↓
Sale / Purchase / Payment
        ↓
 ┌──────┴──────┐
 ↓             ↓
Khata        Accounting
read model   ledger
```

Do not make Step 6/7 dependent on the ledger for their balances.

The existing source-of-truth rules must remain intact.

---

# 17. Do Not Mutate Existing Financial History

Accounting posting must not modify:

```text
Sale.total
Sale.paid_amount
Purchase.total
Purchase.paid_amount
Payment.amount
Inventory.quantity
```

simply to create ledger entries.

Accounting reads the business event and records its accounting representation.

Do not create circular dependencies.

---

# 18. Transaction Safety

Accounting posting must be atomic.

For example:

```text
Create Sale
    ↓
Create Payment(s)
    ↓
Post accounting entries
    ↓
COMMIT
```

If accounting posting fails:

```text
ROLLBACK
```

No partially posted accounting event should remain.

However, inspect the existing Step 5/6/7 transaction boundaries before integrating posting.

Do not introduce nested commits.

Follow the existing rule that services generally do not commit unless the current architecture explicitly requires it.

---

# 19. Historical Posting / Existing Data

The repository already contains operational-domain tests and potentially existing database data.

Do NOT silently fabricate ledger entries for existing historical transactions.

If a migration or initialization process needs to create system accounts, that is acceptable.

For existing Sales/Purchases/Payments, determine from `db_arch.md` whether Step 8 requires:

* explicit posting when transactions are created, or
* a controlled posting/rebuild operation.

Do not invent a dangerous automatic historical migration.

If historical posting is required by the architecture, implement it explicitly and idempotently.

Otherwise keep historical backfill out of this step and clearly document it.

---

# 20. Account Balance

Implement:

```python
get_account_balance(...)
```

Balance must be derived from ledger entries.

Do not store:

```text
Account.balance
```

The calculation should use Decimal.

Account type determines normal balance presentation:

```text
ASSET
EXPENSE
    debit increases balance

LIABILITY
EQUITY
REVENUE
    credit increases balance
```

Keep this logic centralized.

---

# 21. Account Ledger

Implement:

```python
get_account_ledger(...)
```

Return ledger entries for one account with:

* date/time
* description
* debit
* credit
* running balance
* reference type
* reference ID

Support practical:

```text
start_date
end_date
limit
offset
```

if consistent with Step 6/7 statement APIs.

Use deterministic ordering.

Do not load unnecessary rows.

---

# 22. Duplicate Posting Protection

This is critical.

The same source transaction must not produce duplicate accounting entries.

For example:

```text
Sale #123
```

must not accidentally result in:

```text
Revenue +100,000
Revenue +100,000
```

because `post_sale()` was called twice.

Use an appropriate database uniqueness strategy based on the chosen reference design.

If necessary, add a unique constraint/index around:

```text
shop_id
reference_type
reference_id
account_id
```

or another structure that correctly identifies posting lines.

Be careful: the constraint must still allow multiple accounts for one source transaction.

Do not add an incorrect uniqueness constraint that prevents legitimate multi-line postings.

---

# 23. System Account Initialization

Create a small mechanism for ensuring each Shop has its required system accounts.

For example:

```python
ensure_system_accounts(...)
```

This should be:

* idempotent
* tenant-safe
* transaction-safe

Calling it twice must not create duplicates.

Do not create duplicate accounts for an existing shop.

Do not require users to manually create Cash / AR / AP / Revenue / Inventory before the system can operate.

---

# 24. Database Constraints

Add appropriate constraints for:

### Accounts

* valid account type
* non-empty code/name
* unique account code within a shop
* tenant ownership
* system-account integrity where appropriate

### Ledger entries

* debit/credit non-negative
* exactly one side populated
* valid account
* same-shop account relationship
* valid money precision

Use database constraints whenever practical.

Do not rely exclusively on Python validation.

---

# 25. Migration

Create an Alembic migration if required.

Likely additions:

```text
accounts
ledger_entries
```

plus indexes/constraints.

Do NOT modify existing operational tables unless absolutely necessary.

Do NOT rewrite Steps 4–7.

Do NOT add:

```text
account_balances
journals
journal_lines
general_ledger_balances
```

unless the existing architecture document explicitly requires them.

---

# 26. Tests

Create:

```text
tests/test_accounting.py
```

Follow the project's existing testing style.

At minimum test:

## Accounts

* system accounts created
* account codes unique within shop
* same code allowed across different shops
* account tenant isolation
* account type validation
* system account initialization idempotency

## Ledger entries

* valid debit entry
* valid credit entry
* debit and credit both rejected
* zero debit/credit rejected
* negative values rejected
* Decimal precision

## Double-entry

Test:

```text
debits == credits
```

for every posting operation.

Also test that an intentionally unbalanced posting is rejected and rolled back.

## Sale

Test:

```text
cash sale
credit sale
partially paid sale
```

Verify correct accounts and amounts.

Example:

```text
Sale total = 1000
Paid = 300
Due = 700

Debit Cash             300
Debit AccountsReceivable 700
Credit SalesRevenue   1000
```

## Purchase

Test:

```text
Purchase = 5000

Debit Inventory       5000
Credit AccountsPayable 5000
```

according to the architecture.

## Customer payment

Test:

```text
Debit Cash/Bank
Credit Accounts Receivable
```

with the correct amount.

## Supplier payment

Test:

```text
Debit Accounts Payable
Credit Cash/Bank
```

with the correct amount.

## Payment method

Test the mapping for at least:

* cash
* bank
* card
* JazzCash
* Easypaisa
* other

according to the architecture's mapping rules.

## Duplicate posting

Calling the same posting function twice must not create duplicate ledger entries.

## Tenant isolation

Shop A must never post against Shop B accounts.

## Atomicity

If any ledger line fails:

```text
no partial ledger posting remains
```

## Account balance

Verify derived balances from ledger entries.

## Account ledger

Verify:

* entries
* ordering
* running balance
* date filtering
* pagination

## Regression

All existing Step 1–7 tests must continue passing.

---

# 27. API Scope

Do NOT build a large accounting API yet.

If the existing architecture calls for accounting inspection endpoints, keep them minimal.

Potential endpoints:

```text
GET /accounts
GET /accounts/{account_id}/balance
GET /accounts/{account_id}/ledger
```

Only add these if they fit the existing API architecture.

Do NOT expose an endpoint allowing arbitrary users to manually create debit/credit ledger entries.

The application should generate ledger entries from business events.

Do not add authentication/JWT in this step.

---

# 28. Important: No Manual Arbitrary Journal Entry API

For V1:

DO NOT create:

```text
POST /ledger-entries
```

for arbitrary debit/credit manipulation.

Accounting entries should come from controlled posting services:

```text
Sale
Purchase
Customer Payment
Supplier Payment
```

This protects accounting integrity.

Manual journal entries can be a future accounting feature.

---

# 29. No COGS Yet Unless Architecture Requires It

Do not automatically expand this step into full inventory accounting.

Specifically, do NOT add:

```text
Cost of Goods Sold
Inventory adjustment accounting
Purchase returns accounting
Sales returns accounting
Tax accounting
```

unless `db_arch.md` explicitly requires them for Step 8.

The immediate goal is the documented:

```text
Sale
Purchase
Customer Payment
Supplier Payment
```

double-entry flow.

---

# 30. Expected Example

After:

```text
Sale = 10,000
Paid = 4,000
Due = 6,000
```

the ledger should conceptually contain:

```text
Accounts Receivable    Debit   6,000
Cash                   Debit   4,000
Sales Revenue          Credit 10,000
```

Then when the customer pays the remaining 6,000:

```text
Cash                   Debit   6,000
Accounts Receivable    Credit  6,000
```

AR becomes zero for that transaction.

---

For:

```text
Purchase = 20,000
```

the ledger should contain:

```text
Inventory              Debit   20,000
Accounts Payable       Credit  20,000
```

Then when the shop pays the supplier:

```text
Accounts Payable       Debit   20,000
Cash/Bank              Credit  20,000
```

The accounting layer therefore reflects the same economic events already represented by Steps 4–7.

---

# 31. Do Not Break Existing Khata

After Step 8:

Customer Khata must still calculate from:

```text
Sale + Customer Payment
```

Supplier Khata must still calculate from:

```text
Purchase + Supplier Payment
```

The new ledger is an additional accounting representation.

Do not change their existing formulas merely because a General Ledger now exists.

---

# 32. Performance

Add sensible indexes for:

```text
accounts(shop_id, code)

ledger_entries(shop_id, account_id, created_at)

ledger_entries(shop_id, reference_type, reference_id)
```

Adapt to the actual schema.

Do not add redundant indexes that duplicate existing prefixes.

---

# 33. Documentation

Update the architecture/README documentation with a concise Step 8 section.

Document:

```text
accounts
ledger_entries
```

and the posting rules:

```text
Sale
    Cash/AR → Sales Revenue

Purchase
    Inventory → Accounts Payable

Customer Payment
    Cash/Bank → Accounts Receivable

Supplier Payment
    Accounts Payable → Cash/Bank
```

Clearly state that:

```text
Customer Khata remains based on Sales + Payments.
Supplier Khata remains based on Purchases + Payments.
General Ledger is the accounting representation of those events.
```

---

# 34. Things NOT To Implement

Strictly do NOT implement:

* full accounting software
* manual journal-entry UI
* chart-of-accounts management UI
* tax accounting
* GST/VAT
* COGS unless explicitly required
* purchase returns
* sales returns
* depreciation
* payroll
* financial statements
* profit & loss dashboard
* balance sheet dashboard
* cash-flow statement
* fiscal years
* closing periods
* accounting locks
* audit-log redesign
* authentication/JWT
* RBAC
* React frontend
* AI accounting agent

Those are future steps.

---

# 35. Regression Safety

Run the entire existing test suite.

The current known baseline before Step 8 is:

```text
Steps 1–5: 101 tests
Step 6:     +37 tests
Step 7:     additional tests from supplier payables
```

Do NOT assume the final number.

Report the actual number.

Also run where available:

```text
pytest
ruff
alembic check
migration downgrade → upgrade
application startup
/health
```

If PostgreSQL is unavailable, clearly state that database/integration validation could not be performed.

Never fabricate test results.

---

# 36. Final Report

When finished, report:

## Implemented

List all new/modified files.

## Accounting Architecture

Confirm:

```text
accounts
ledger_entries
```

are the core accounting tables.

## Account Types

List the implemented account types.

## System Accounts

List the default accounts created per shop.

## Posting Rules

Show the actual debit/credit rules for:

* Sale
* Purchase
* Customer Payment
* Supplier Payment

## Double-Entry Integrity

Explain how the system guarantees:

```text
SUM(debits) == SUM(credits)
```

for every posting.

## Duplicate Protection

Explain how duplicate posting is prevented.

## Tenant Isolation

Explain how Shop A cannot use Shop B's accounts or ledger.

## Transaction Safety

Explain rollback behavior.

## Existing Domains

Explicitly confirm that Customer Khata and Supplier Khata remain operationally independent from the ledger.

## Database

List migrations, constraints and indexes.

## Tests

Report:

```text
Previous tests: X
New Step 8 tests: Y
Total: Z
```

and state exactly which validation commands actually passed.

---

# STOP CONDITION

After completing Step 8:

1. Run tests and validation.
2. Report actual results.
3. Summarize the accounting architecture.
4. Suggest **Step 9 only**.
5. Do NOT implement Step 9.
6. STOP.
