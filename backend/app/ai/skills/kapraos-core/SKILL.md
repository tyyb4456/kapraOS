---
name: kapraos-core
description: >-
  Core KapraOS shop concepts, fabric-retail terminology, and operating rules.
  Use for any request about shops, sales, purchases, inventory, customers,
  suppliers, khata/udhaar, payments, expenses, or accounting. Covers domain
  vocabulary (meter, gaz, thaan, jora, udhaar, khata) and the hard rules:
  tenant isolation, services-over-raw-DB, and read-only analytics.
---

# kapraos-core

Core domain knowledge for the KapraOS shop assistant. Keep responses grounded in these concepts; never invent data.

## Entities

- **Shop (tenant):** the single-shop scope of every request. Comes from authenticated context only.
- **Customer / Supplier:** khata parties. A customer has a receivable balance (udhaar); a supplier has a payable balance.
- **Product / ProductVariant:** a fabric item and its sellable variant (color/design/cut). Variants carry SKUs.
- **Inventory:** quantity per variant plus weighted-average cost. Mutated only via the inventory service.
- **Sale:** a POS transaction: header totals (server-calculated) + item lines + payments. Paid in full → COMPLETED, else PARTIAL.
- **Purchase:** supplier stock intake: item lines with unit costs + payments. Increases inventory.
- **Payment:** money movement against a sale, purchase, or khata (customer/supplier). Method maps to Cash/Bank deterministically.
- **Expense:** immediate-payment operating cost (rent, utilities, salaries, ...). Immutable once posted.
- **Khata:** running receivable/payable ledger per customer/supplier (sales/purchases + payments).
- **Accounting:** double-entry ledger (`ledger_entries`) derived from the above events. Balances are always derived, never stored.
- **Reporting:** read-only statements (trial balance, P&L, balance sheet, dashboard) derived at query time.

## Terminology (fabric retail, Urdu/Punjabi mix)

- **meter / gaz / yard:** length units for fabric. 1 gaz ≈ 0.9144 meter. Never assume conversions silently — confirm ambiguous units.
- **thaan:** a fabric roll/bolt. **piece / set / jora (suit):** stitched or packed units.
- **udhaar:** credit (buy now, pay later). **khata:** the running credit account. **cash:** immediate payment.
- **Common asks:** "Kitna stock hai?" (how much stock), "Aaj kitni sale hui?" (today's sales), "Ahmed ka khata kitna hai?" (Ahmed's balance).

## Hard operating rules

1. Tenant (`shop_id`) comes from authenticated backend context. Never ask the user for it, never change it.
2. Business mutations go through approved domain tools/services only. Never write raw SQL for transactions, never mutate the DB directly.
3. Analytics is SELECT-only and always filtered by `shop_id`.
4. Side-effecting operations (EXECUTE) require human approval (HITL).
5. Follow deterministic backend rules (server-calculated totals, payment→account mapping, double-entry balance).
6. When information is ambiguous (which "Ali"? which "black lawn"? how many gaz?), resolve via safe reads or ask for clarification.
