# Step 7 — Supplier Payables / Supplier Khata

You are continuing development of the existing fabric/fashion retail management backend.

## IMPORTANT WORKING RULE

Work ONLY on **Step 7 — Supplier Payables / Supplier Khata**.

Before changing anything:

1. Inspect the existing repository structure.
2. Read the project's architecture/design Markdown documentation.
3. Read the existing implementations for:

   * Supplier
   * Purchase
   * PurchaseItem
   * Payment
   * Inventory
   * Sales
   * Customer Receivables / Step 6
4. Understand the existing tenant-isolation, transaction, relationship, and API patterns.
5. Do NOT redesign previously completed domains.
6. Do NOT introduce unrelated features.
7. Reuse the existing generic `Payment` model.
8. Keep the architecture as a modular monolith.

The goal is to implement supplier payables as the **mirror of Customer Khata**, while preserving the existing source-of-truth and financial rules.

---

# 1. Core Principle

Supplier payables must use the existing:

* `Purchase`
* `PurchaseItem`
* `Supplier`
* `Payment`

records as the source of truth.

**DO NOT create:**

* `SupplierBalance`
* `SupplierLedger`
* `PayableLedger`
* `SupplierKhataEntry`
* any other mutable balance/ledger table

Do NOT add `supplier.current_balance`.

The payable balance must be calculated from existing transactional records.

Conceptually:

```text
Outstanding Payable
    = Qualifying Purchases
    - Qualifying Supplier Payments
```

A positive balance means the shop owes the supplier.

Do not clamp negative balances to zero. Surface them because they may represent an overpayment/credit situation.

---

# 2. First Inspect the Existing Payment Model Carefully

Step 6 modified the `Payment` ↔ `Sale` relationship and added stronger integrity constraints.

Before implementing anything, inspect the current Payment model and understand its existing generic shape.

It should conceptually support:

```text
Payment
├── customer_id   nullable
├── supplier_id   nullable
├── sale_id       nullable
├── purchase_id   nullable
├── amount
├── payment_method
└── timestamps
```

Do NOT create another supplier-specific payment model.

Reuse the existing `Payment` row for supplier payments.

Also inspect whether the current database already has tenant-safe relationships involving:

```text
purchase_id + supplier_id
```

If it does not, add the appropriate integrity protection.

---

# 3. Supplier Balance Service

Create or extend:

```text
app/services/payables.py
```

Implement a service equivalent to Step 6's receivables service.

At minimum provide:

```python
get_supplier_balance(...)
get_supplier_statement(...)
get_supplier_summary(...)
record_supplier_payment(...)
```

Follow the existing project naming, typing, async SQLAlchemy, transaction, and exception conventions.

---

# 4. Qualifying Purchases

Inspect the actual Purchase model and determine its current lifecycle/status implementation.

Do NOT invent new purchase statuses.

Use the existing domain semantics.

Only purchases that genuinely represent payable supplier debt should contribute to the outstanding balance.

If the current Purchase model has no cancellation/void workflow, do not invent one merely for Step 7.

Document the qualifying rule clearly in the service, just as Step 6 centralized:

```python
QUALIFYING_SALE_STATUSES
```

If appropriate, introduce the supplier equivalent:

```python
QUALIFYING_PURCHASE_STATUSES
```

Keep this rule in one place so a future purchase-return/cancellation workflow can update it cleanly.

---

# 5. Supplier Balance

Implement:

```python
get_supplier_balance(shop_id, supplier_id)
```

It should return a useful financial summary without loading every purchase/payment row.

At minimum include:

```text
total purchases
total paid
outstanding payable
purchase count
payment count
last purchase timestamp
last payment timestamp
```

Use aggregate SQL queries where appropriate.

All queries MUST be tenant-scoped.

The supplier must first be resolved within the requested shop.

Another shop's supplier ID must behave as "not found", not leak information.

---

# 6. Supplier Statement

Implement:

```python
get_supplier_statement(...)
```

Build the statement as a read model from:

```text
Purchase
+
Supplier Payment
```

Do NOT create statement-entry storage.

Conceptually:

```text
Purchase  → DEBIT  → shop owes supplier more
Payment   → CREDIT → shop owes supplier less
```

For supplier Khata, use terminology that makes the direction clear.

Example:

```text
Purchase:  +50,000
Payment:   -20,000
Payment:   -10,000
--------------------
Outstanding: 20,000
```

The running balance must be calculated deterministically.

Use:

```text
running_balance =
    previous_balance
    + purchase_amount
    - supplier_payment_amount
```

---

# 7. Ordering Must Be Deterministic

Do not order statement entries by `created_at` alone.

Two records can legitimately have the same timestamp because they were created in the same transaction.

Use a stable ordering strategy involving:

```text
created_at
+
entry type
+
stable unique ID
```

The exact ordering should be consistent with the existing Step 6 implementation.

Most importantly:

* opening balance must be correct
* page 2 must continue from page 1
* identical timestamps must never produce nondeterministic balances

Preserve the same pagination semantics already established by Customer Khata.

---

# 8. Date Filtering + Pagination

Support the same practical filtering behavior as Step 6 where appropriate:

```text
start_date
end_date
limit
offset
```

Dates should be inclusive according to the existing Step 6 convention.

Entries before the requested window must be folded into:

```text
opening_balance
```

Do NOT simply discard earlier entries and start the running balance at zero.

Example:

```text
Purchase before date range: 100,000
Payment before date range:   40,000

Opening balance = 60,000
```

Then entries inside the requested date range continue from 60,000.

Preserve the existing pagination behavior from Step 6.

---

# 9. Supplier Payment Recording

Implement:

```python
record_supplier_payment(...)
```

This should create a normal existing `Payment` row.

Do NOT create a `SupplierPayment` model.

Validate, in a sensible order:

1. Supplier exists in the current shop.
2. Amount is greater than zero.
3. Payment method is valid.
4. If `purchase_id` is supplied:

   * purchase exists
   * purchase belongs to the current shop
   * purchase belongs to the specified supplier
   * purchase is settleable according to the current purchase rules
5. Calculate current supplier outstanding.
6. Prevent invalid over-settlement according to the V1 business rule.
7. Create the Payment.
8. Keep the operation atomic.

---

# 10. Purchase-Specific vs Unallocated Supplier Payment

Support the generic Payment model correctly.

A supplier payment may be:

### Allocated

```text
supplier_id = X
purchase_id = Y
```

This payment is associated with a specific purchase.

### Unallocated

```text
supplier_id = X
purchase_id = NULL
```

This reduces the supplier's overall payable balance but does NOT mutate an individual Purchase.

Follow the same philosophy as Step 6 Customer Khata.

Do not create hidden allocation tables.

---

# 11. Purchase `paid_amount`

This point requires careful inspection.

Step 5 introduced:

```text
Purchase.paid_amount
Purchase.due_amount
```

and Step 6 established a generic Payment source-of-truth pattern.

Before implementing, inspect how the current Purchase model treats `paid_amount`.

The supplier payment implementation must ensure that:

* `Purchase.paid_amount` remains truthful
* existing Step 5 semantics are preserved
* supplier payments do not silently make `Purchase.paid_amount` inconsistent
* unallocated supplier payments do not arbitrarily mutate a Purchase

If `Purchase.paid_amount` is currently a stored value/cache rather than a computed property, determine the safest minimal approach based on the existing architecture.

Do NOT blindly update it without understanding how Step 5 implemented purchase payments.

If necessary, refactor minimally so the source of truth remains consistent.

Do not redesign the entire purchasing domain.

---

# 12. Concurrency

Supplier settlement has the same race-condition risk as Customer Khata.

Example:

```text
Supplier outstanding = 10,000

Request A → tries to pay 8,000
Request B → tries to pay 8,000
```

Both requests must NOT independently observe 10,000 and succeed.

Use the same concurrency strategy established in Step 6 where appropriate.

A suitable existing row should be locked with:

```text
SELECT ... FOR UPDATE
```

before the final outstanding validation.

Do not introduce a new balance table merely to solve concurrency.

The final operation must remain atomic.

---

# 13. Database Integrity

Inspect the current relationships.

The database should prevent this invalid state:

```text
Supplier A
Purchase belonging to Supplier B
Payment references Supplier A + Purchase B
```

If the current schema does not prevent this, add a tenant/domain-safe composite foreign key similar to Step 6's customer-sale protection.

Conceptually:

```text
(supplier_id, purchase_id)
```

must resolve to the same supplier relationship on Purchase.

You may need a supporting unique constraint such as:

```text
UNIQUE(id, supplier_id)
```

on Purchase.

Use the smallest correct migration necessary.

Do NOT create a new payable table.

---

# 14. API

If the existing Step 6 API pattern is appropriate, add only the focused supplier-payables endpoints:

```text
GET /suppliers/{supplier_id}/balance

GET /suppliers/{supplier_id}/statement
    ?start_date=
    &end_date=
    &limit=
    &offset=

GET /suppliers/{supplier_id}/summary

POST /suppliers/{supplier_id}/payments
```

Follow the existing schemas and response conventions.

Do not build a giant Supplier CRUD API in this step.

Do not add authentication/JWT yet if it is still intentionally out of scope.

Use the existing centralized development tenant-resolution dependency.

Do NOT trust a frontend-supplied `shop_id`.

---

# 15. Tenant Isolation

Every supplier-payables query must be scoped by:

```text
shop_id
+
supplier_id
```

For purchase/payment relationships, validate:

```text
shop_id
+
supplier_id
+
purchase_id
```

A supplier from another shop must never be readable or usable.

Preserve the existing development `X-Shop-Id` approach if that is still the current architecture.

Do not introduce JWT/authentication in Step 7.

---

# 16. Financial Precision

Continue the project's existing financial rules:

* PostgreSQL `NUMERIC`
* Python `Decimal`
* never use float for money
* server-computed totals
* preserve existing rounding conventions

Do not introduce floating-point arithmetic anywhere in the new service.

---

# 17. Tests

Create focused tests, following the structure/style of:

```text
tests/test_receivables.py
```

from Step 6.

At minimum test:

### Basic balance

* supplier with no purchases
* one unpaid purchase
* fully paid purchase
* partially paid purchase
* multiple purchases
* multiple supplier payments

### Statement

* purchase appears as debit
* payment appears as credit
* running balance
* correct opening balance
* pagination
* page 2 continues correctly
* date filtering
* inclusive start/end dates
* inverted date range if Step 6 validates this
* deterministic ordering for identical timestamps

### Payment

* valid supplier payment
* invalid/zero amount
* negative amount
* invalid payment method
* allocated payment
* unallocated payment
* payment against wrong supplier
* payment against wrong purchase
* payment against another shop's purchase
* payment against another shop's supplier
* overpayment rejection according to V1 rules

### Integrity

Explicitly test the database-level protection against:

```text
supplier A + purchase belonging to supplier B
```

### Tenant isolation

Test that:

```text
Shop A cannot read Shop B supplier balance
Shop A cannot read Shop B supplier statement
Shop A cannot create payment against Shop B supplier
Shop A cannot create payment against Shop B purchase
```

### Concurrency

If the project's test infrastructure supports it, add a real concurrent settlement test similar to Step 6.

Example:

```text
Outstanding = 10,000

Concurrent payment A = 7,000
Concurrent payment B = 7,000

Expected:
exactly one succeeds
total accepted payments <= 10,000
```

### Decimal correctness

Use Decimal values that would expose float errors.

---

# 18. Regression Safety

Run the complete existing test suite.

Do not only run the new tests.

The previous Step 6 report had:

```text
101 baseline tests
37 Step 6 tests
138 total
```

Your implementation must preserve all existing behavior.

Report the actual number of tests after implementation.

Also run, where configured:

```text
ruff
alembic check
migration upgrade/downgrade cycle
```

and the application's health/startup check.

If Postgres is unavailable in the environment, do NOT pretend the database tests passed.

Clearly report what was actually executed.

---

# 19. Migration Rules

Only create a migration if the schema genuinely needs one.

Likely candidates include:

* composite supplier/purchase integrity FK
* supporting unique constraint
* supporting indexes

Do NOT create:

```text
supplier_balances
supplier_ledger
payable_entries
khata_entries
```

If no schema change is necessary, do not create an empty migration.

Verify Alembic has no unintended drift.

---

# 20. Documentation

If the repository has a README or architecture document containing Step 6's Customer Khata API documentation, add a concise Step 7 section.

Example concept:

```markdown
## Supplier Khata / Payables (Step 7)

    GET  /suppliers/{id}/balance
    GET  /suppliers/{id}/statement?start_date=&end_date=&limit=&offset=
    GET  /suppliers/{id}/summary
    POST /suppliers/{id}/payments

Supplier payable balances are derived from Purchase + Payment records.
No separate mutable supplier balance/ledger table is used.

Auth is still out of scope; tenant resolution continues through the
existing development dependency.
```

Adapt this to the actual documentation structure.

---

# 21. Do NOT Implement These Yet

Strictly keep these outside Step 7:

* accounting/general ledger
* double-entry accounting
* chart of accounts
* journal entries
* expense management
* cashflow reports
* tax accounting
* supplier returns workflow
* purchase cancellation workflow
* inventory redesign
* authentication/JWT
* RBAC
* advanced reporting
* AI features
* dashboards beyond the focused supplier summary
* React frontend/POS UI

Those belong to later steps.

---

# 22. Expected Architecture

The intended relationship is:

```text
                 ┌──────────────┐
                 │   Supplier   │
                 └──────┬───────┘
                        │
                        ▼
                 ┌──────────────┐
                 │   Purchase   │
                 └──────┬───────┘
                        │
                 increases payable
                        │
                        ▼
                 ┌──────────────┐
                 │   Payment    │
                 └──────┬───────┘
                        │
                 decreases payable
                        │
                        ▼
              ┌─────────────────────┐
              │ Supplier Payables   │
              │    Read Model       │
              └─────────────────────┘
```

The database stores transactions.

The service derives the financial position.

There should be no duplicated mutable balance state.

---

# 23. Implementation Quality

Keep the implementation:

* async
* typed
* modular
* transaction-safe
* tenant-safe
* Decimal-safe
* database-integrity-safe
* consistent with Step 6
* easy to replace with JWT tenant resolution later

Prefer simple explicit code over unnecessary abstractions.

Do not refactor unrelated code merely because you notice opportunities.

---

# 24. Final Verification Report

When finished, report:

### Implemented

List the files/features added or modified.

### Source of truth

Explicitly confirm whether Supplier Payables uses:

```text
Purchase + Payment
```

as the source of truth.

### Balance rules

State exactly which purchase/payment records qualify.

### Payment behavior

Explain allocated vs unallocated supplier payments.

### Purchase paid amount

Explain exactly how `Purchase.paid_amount` remains consistent with the existing architecture.

### Database

List every migration/schema change and why it was necessary.

### Tenant isolation

Explain how supplier/payments/purchases are protected across shops.

### Concurrency

Explain how concurrent supplier settlements are handled.

### Tests

Report:

```text
Previous tests: X
New Step 7 tests: Y
Total: Z
```

and explicitly state whether Postgres/integration tests actually ran.

### Validation

Report the actual results of:

```text
pytest
ruff
alembic check
migration cycle
health/startup
```

Do not fabricate results.

---

## STOP CONDITION

After completing Step 7:

1. Run the relevant tests and validation.
2. Report the implementation and results.
3. Suggest **Step 8** only.
4. Do NOT implement Step 8.
5. STOP.
