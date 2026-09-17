Yep brother. Now we're getting into the **real architecture**.

I’d design this as a **multi-tenant retail system**, but keep the domain model flexible enough for:

* open fabric sold by meter
* ready-made/unstitched suits sold by piece
* boutique products
* branded products
* arbitrary product attributes
* suppliers and purchases
* customers and credit/khata
* inventory movements
* sales and returns
* payments
* expenses
* accounting/financial ledger

The most important thing: **inventory and finance should be transaction-based, not just mutable counters.**

---

# 1. Final ERD

Here is the core relationship model I'd use:

```text
                                      ┌──────────────┐
                                      │    shops     │
                                      └──────┬───────┘
                                             │
              ┌──────────────────────────────┼────────────────────────────┐
              │                              │                            │
              ▼                              ▼                            ▼
        ┌───────────┐                  ┌───────────┐                ┌────────────┐
        │   users   │                  │ customers │                │ suppliers  │
        └───────────┘                  └─────┬─────┘                └─────┬──────┘
                                             │                            │
                                             │                            │
                                      ┌──────▼───────┐              ┌─────▼──────┐
                                      │    sales     │              │  purchases │
                                      └──────┬───────┘              └─────┬──────┘
                                             │                            │
                                      ┌──────▼───────┐              ┌─────▼────────┐
                                      │  sale_items  │              │purchase_items│
                                      └──────┬───────┘              └─────┬────────┘
                                             │                            │
                                             └──────────┬─────────────────┘
                                                        │
                                                        ▼
                                               ┌─────────────────┐
                                               │ product_variants│
                                               └────────┬────────┘
                                                        │
                                      ┌─────────────────┼────────────────┐
                                      │                 │                │
                                      ▼                 ▼                ▼
                                ┌──────────┐    ┌────────────┐   ┌───────────────┐
                                │ products │    │ inventory  │   │variant_attrs  │
                                └────┬─────┘    └─────┬──────┘   └───────────────┘
                                     │                │
                              ┌──────▼──────┐         │
                              │ categories  │         ▼
                              └─────────────┘ ┌────────────────────┐
                                              │inventory_movements │
                                              └────────────────────┘


        ┌──────────────┐
        │   payments   │
        └──────┬───────┘
               │
               ├──── sales
               ├──── purchases
               └──── customers/suppliers


        ┌──────────────┐
        │   accounts   │
        └──────┬───────┘
               │
               ▼
        ┌────────────────┐
        │ ledger_entries │
        └────────────────┘


        ┌──────────────┐
        │   expenses    │
        └──────────────┘
```

There are a couple of additional association tables I'll explain below.

---

# 2. PostgreSQL data types

Before writing models, I'd establish some rules.

For money:

```python
Numeric(14, 2)
```

**Never use Float for money.**

For fabric quantity:

```python
Numeric(12, 3)
```

Because you may have:

```text
3.5 meters
3.75 meters
12.250 meters
```

For IDs:

```python
UUID
```

rather than integer IDs.

For timestamps:

```python
DateTime(timezone=True)
```

And PostgreSQL should use:

```text
TIMESTAMPTZ
```

---

# 3. Project structure

I'd structure SQLAlchemy like this:

```text
backend/
│
├── app/
│   ├── models/
│   │   ├── base.py
│   │   ├── shop.py
│   │   ├── user.py
│   │   ├── category.py
│   │   ├── brand.py
│   │   ├── attribute.py
│   │   ├── product.py
│   │   ├── inventory.py
│   │   ├── customer.py
│   │   ├── supplier.py
│   │   ├── sale.py
│   │   ├── purchase.py
│   │   ├── payment.py
│   │   ├── expense.py
│   │   └── accounting.py
│   │
│   ├── services/
│   │   ├── sales.py
│   │   ├── purchases.py
│   │   ├── inventory.py
│   │   └── accounting.py
│   │
│   └── database.py
│
└── alembic/
```

Don't dump 25 models into `models.py`. It'll become a graveyard pretty quickly.

---

# 4. Base model

```python
# models/base.py

import uuid

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

---

# 5. Shop

Everything belongs to a shop.

```python
# models/shop.py

import uuid

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, TimestampMixin


class Shop(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "shops"

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    phone: Mapped[str | None] = mapped_column(String(30))

    address: Mapped[str | None] = mapped_column(String(300))

    currency: Mapped[str] = mapped_column(
        String(3),
        default="PKR",
        nullable=False,
    )

    users = relationship("User", back_populates="shop")

    products = relationship("Product", back_populates="shop")

    customers = relationship("Customer", back_populates="shop")

    suppliers = relationship("Supplier", back_populates="shop")
```

This gives us the SaaS foundation:

```text
Shop A → completely isolated data
Shop B → completely isolated data
```

---

# 6. User

```python
# models/user.py

from enum import Enum

from sqlalchemy import ForeignKey, String, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, TimestampMixin


class UserRole(str, Enum):
    OWNER = "owner"
    MANAGER = "manager"
    CASHIER = "cashier"
    INVENTORY_MANAGER = "inventory_manager"


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[UserRole] = mapped_column(
        SQLEnum(UserRole),
        nullable=False,
        default=UserRole.CASHIER,
    )

    shop = relationship("Shop", back_populates="users")
```

---

# 7. Category

This is hierarchical.

```python
# models/category.py

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, TimestampMixin


class Category(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "categories"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    parent = relationship(
        "Category",
        remote_side="Category.id",
        back_populates="children",
    )

    children = relationship(
        "Category",
        back_populates="parent",
    )

    products = relationship(
        "Product",
        back_populates="category",
    )
```

Now:

```text
Women
 └── Open Fabric
      └── Lawn
```

is naturally represented.

---

# 8. Brands

```python
# models/brand.py

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, TimestampMixin


class Brand(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "brands"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    products = relationship(
        "Product",
        back_populates="brand",
    )
```

---

# 9. Attributes

This is what solves the huge-category problem.

```python
# models/attribute.py

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, TimestampMixin


class Attribute(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "attributes"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    values = relationship(
        "AttributeValue",
        back_populates="attribute",
        cascade="all, delete-orphan",
    )


class AttributeValue(Base, UUIDMixin):
    __tablename__ = "attribute_values"

    attribute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attributes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    value: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    attribute = relationship(
        "Attribute",
        back_populates="values",
    )
```

So:

```text
Attribute
   ↓
Dupatta
   ↓
Chiffon
Silk
Organza
Net
```

---

# 10. Product

```python
# models/product.py

from enum import Enum
import uuid

from sqlalchemy import (
    ForeignKey,
    String,
    Text,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, TimestampMixin


class ProductType(str, Enum):
    OPEN_FABRIC = "open_fabric"
    READY_SUIT = "ready_suit"
    BOUTIQUE = "boutique"
    OTHER = "other"


class Product(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "products"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=False,
    )

    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brands.id"),
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    product_type: Mapped[ProductType] = mapped_column(
        SQLEnum(ProductType),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text
    )

    shop = relationship(
        "Shop",
        back_populates="products",
    )

    category = relationship(
        "Category",
        back_populates="products",
    )

    brand = relationship(
        "Brand",
        back_populates="products",
    )

    variants = relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
    )
```

---

# 11. Product Variant

This is where the actual inventory item lives.

```python
from decimal import Decimal
from enum import Enum
import uuid

from sqlalchemy import (
    ForeignKey,
    Numeric,
    String,
    Boolean,
    Enum as SQLEnum,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class Unit(str, Enum):
    METER = "meter"
    YARD = "yard"
    PIECE = "piece"
    SET = "set"
    ROLL = "roll"


class ProductVariant(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "product_variants"

    __table_args__ = (
        UniqueConstraint(
            "shop_id",
            "sku",
            name="uq_variant_shop_sku",
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sku: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    barcode: Mapped[str | None] = mapped_column(
        String(100),
        index=True,
    )

    purchase_price: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    selling_price: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    unit: Mapped[Unit] = mapped_column(
        SQLEnum(Unit),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    product = relationship(
        "Product",
        back_populates="variants",
    )

    attribute_values = relationship(
        "VariantAttributeValue",
        back_populates="variant",
        cascade="all, delete-orphan",
    )

    inventory = relationship(
        "Inventory",
        back_populates="variant",
        uselist=False,
    )
```

---

# 12. Variant attributes

```python
class VariantAttributeValue(Base, UUIDMixin):

    __tablename__ = "variant_attribute_values"

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "product_variants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    attribute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "attributes.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    attribute_value_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "attribute_values.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    variant = relationship(
        "ProductVariant",
        back_populates="attribute_values",
    )
```

Now this:

```text
Gul Ahmed Suit
 ├── Color → Pink
 ├── Pieces → 3 Piece
 ├── Fabric → Lawn
 ├── Dupatta → Chiffon
 └── Bottom → Trouser
```

doesn't require creating another category.

---

# 13. Inventory

Here's where I'd make one important distinction.

`inventory.quantity` is your **current cached stock**.

The historical truth lives in `inventory_movements`.

```python
class Inventory(Base, UUIDMixin, TimestampMixin):

    __tablename__ = "inventory"

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "product_variants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        default=0,
        nullable=False,
    )

    reserved_quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        default=0,
        nullable=False,
    )

    variant = relationship(
        "ProductVariant",
        back_populates="inventory",
    )
```

Available stock:

```text
quantity - reserved_quantity
```

---

# 14. Inventory movement

This is arguably the **most important table in the entire system**.

```python
class InventoryMovementType(str, Enum):
    PURCHASE = "purchase"
    SALE = "sale"
    CUSTOMER_RETURN = "customer_return"
    SUPPLIER_RETURN = "supplier_return"
    DAMAGE = "damage"
    ADJUSTMENT = "adjustment"


class InventoryMovement(Base, UUIDMixin):

    __tablename__ = "inventory_movements"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id"),
        nullable=False,
        index=True,
    )

    movement_type: Mapped[InventoryMovementType] = mapped_column(
        SQLEnum(InventoryMovementType),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
    )

    unit_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2),
    )

    reference_type: Mapped[str | None] = mapped_column(
        String(50)
    )

    reference_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )

    notes: Mapped[str | None] = mapped_column(
        String(500)
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
```

For a sale:

```text
movement_type = SALE
quantity = -3.500
```

For purchase:

```text
movement_type = PURCHASE
quantity = +100.000
```

**I strongly prefer signed quantities.**

Then your ledger becomes incredibly intuitive.

---

# 15. Customer

```python
class Customer(Base, UUIDMixin, TimestampMixin):

    __tablename__ = "customers"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    phone: Mapped[str | None] = mapped_column(
        String(30),
        index=True,
    )

    address: Mapped[str | None] = mapped_column(
        String(300)
    )

    notes: Mapped[str | None] = mapped_column(
        String(500)
    )

    sales = relationship(
        "Sale",
        back_populates="customer",
    )
```

Don't require customers for every sale.

You should support:

```text
Walk-in Customer
```

without creating a customer account.

---

# 16. Supplier

```python
class Supplier(Base, UUIDMixin, TimestampMixin):

    __tablename__ = "suppliers"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    phone: Mapped[str | None] = mapped_column(
        String(30)
    )

    address: Mapped[str | None] = mapped_column(
        String(300)
    )

    notes: Mapped[str | None] = mapped_column(
        String(500)
    )

    purchases = relationship(
        "Purchase",
        back_populates="supplier",
    )
```

---

# 17. Sales

```python
class SaleStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class Sale(Base, UUIDMixin, TimestampMixin):

    __tablename__ = "sales"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
    )

    invoice_number: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    discount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=0,
        nullable=False,
    )

    total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=0,
        nullable=False,
    )

    due_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=0,
        nullable=False,
    )

    status: Mapped[SaleStatus] = mapped_column(
        SQLEnum(SaleStatus),
        nullable=False,
    )

    customer = relationship(
        "Customer",
        back_populates="sales",
    )

    items = relationship(
        "SaleItem",
        back_populates="sale",
        cascade="all, delete-orphan",
    )
```

---

# 18. Sale items

This is where you preserve the exact historical sale.

```python
class SaleItem(Base, UUIDMixin):

    __tablename__ = "sale_items"

    sale_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sales.id", ondelete="CASCADE"),
        nullable=False,
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id"),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
    )

    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    cost_price: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    discount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=0,
        nullable=False,
    )

    total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    sale = relationship(
        "Sale",
        back_populates="items",
    )
```

The `cost_price` is critical.

Imagine:

```text
January cost = Rs 400/m
March cost   = Rs 500/m
```

Your January sale must retain:

```text
cost_price = 400
```

You cannot calculate historical profit using today's product price.

---

# 19. Purchases

```python
class Purchase(Base, UUIDMixin, TimestampMixin):

    __tablename__ = "purchases"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id"),
        nullable=False,
    )

    invoice_number: Mapped[str | None] = mapped_column(
        String(50)
    )

    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    discount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=0,
        nullable=False,
    )

    total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=0,
        nullable=False,
    )

    due_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=0,
        nullable=False,
    )

    supplier = relationship(
        "Supplier",
        back_populates="purchases",
    )

    items = relationship(
        "PurchaseItem",
        back_populates="purchase",
        cascade="all, delete-orphan",
    )
```

---

# 20. Purchase items

```python
class PurchaseItem(Base, UUIDMixin):

    __tablename__ = "purchase_items"

    purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchases.id", ondelete="CASCADE"),
        nullable=False,
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id"),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3),
        nullable=False,
    )

    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    purchase = relationship(
        "Purchase",
        back_populates="items",
    )
```

---

# 21. Payments

I'd keep payments separate from sales/purchases.

```python
class PaymentMethod(str, Enum):
    CASH = "cash"
    CARD = "card"
    BANK = "bank"
    JAZZCASH = "jazzcash"
    EASYPAISA = "easypaisa"
    OTHER = "other"


class Payment(Base, UUIDMixin, TimestampMixin):

    __tablename__ = "payments"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
    )

    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id"),
    )

    sale_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sales.id"),
    )

    purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchases.id"),
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    method: Mapped[PaymentMethod] = mapped_column(
        SQLEnum(PaymentMethod),
        nullable=False,
    )

    reference: Mapped[str | None] = mapped_column(
        String(100)
    )
```

This supports:

```text
Customer → pays shop
Supplier → paid by shop
```

---

# 22. Expenses

```python
class Expense(Base, UUIDMixin, TimestampMixin):

    __tablename__ = "expenses"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(300)
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    payment_method: Mapped[PaymentMethod] = mapped_column(
        SQLEnum(PaymentMethod),
        nullable=False,
    )

    expense_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
```

---

# 23. Accounting

Now we reach the proper financial layer.

Accounts:

```python
class AccountType(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class Account(Base, UUIDMixin):

    __tablename__ = "accounts"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    account_type: Mapped[AccountType] = mapped_column(
        SQLEnum(AccountType),
        nullable=False,
    )
```

Typical accounts automatically created for every shop:

```text
ASSETS
├── Cash
├── Bank
├── Inventory
└── Accounts Receivable

LIABILITIES
└── Accounts Payable

REVENUE
└── Sales Revenue

EXPENSE
├── Rent
├── Salaries
├── Electricity
└── Other Expenses

EXPENSE / COGS
└── Cost of Goods Sold
```

---

# 24. Financial ledger

```python
class LedgerEntry(Base, UUIDMixin):

    __tablename__ = "ledger_entries"

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id"),
        nullable=False,
        index=True,
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    debit: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=0,
        nullable=False,
    )

    credit: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        default=0,
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(300)
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
```

---

# 25. Now the magic: one sale

Let's take the real example.

Shop buys:

```text
100m Linen
Cost = Rs 420/m
```

Inventory:

```text
+100m
```

Then customer buys:

```text
3.5m
Selling = Rs 650/m
```

Revenue:

```text
3.5 × 650
= Rs 2,275
```

COGS:

```text
3.5 × 420
= Rs 1,470
```

Profit:

```text
Rs 805
```

The transaction creates:

### Sale

```text
Sale
Total = 2,275
```

### Sale item

```text
3.5m
Selling price = 650
Cost price = 420
```

### Inventory movement

```text
SALE
-3.5m
```

### Payment

```text
CASH
+2,275
```

### Ledger

```text
Dr Cash                    2,275
    Cr Sales Revenue              2,275

Dr Cost of Goods Sold     1,470
    Cr Inventory                   1,470
```

And therefore:

```text
Revenue      2,275
COGS         1,470
-------------------
Gross Profit   805
```

This is a proper accounting trail.

---

# 26. Customer credit changes the accounting

Suppose:

```text
Sale = Rs 2,275
Paid = Rs 1,000
Due  = Rs 1,275
```

Instead of:

```text
Dr Cash 2,275
```

you get:

```text
Dr Cash                 1,000
Dr Accounts Receivable  1,275
    Cr Sales Revenue          2,275
```

Then when customer later pays:

```text
Dr Cash                 1,275
    Cr Accounts Receivable    1,275
```

That's your digital **khata**.

---

# 27. Purchase on credit

Supplier gives:

```text
100m × Rs 420
= Rs 42,000
```

Shop doesn't pay.

Accounting:

```text
Dr Inventory             42,000
    Cr Accounts Payable       42,000
```

Later pays Rs 20,000:

```text
Dr Accounts Payable      20,000
    Cr Cash                   20,000
```

Remaining:

```text
Rs 22,000
```

---

# 28. One thing I would change from the earlier design

I'd **not store `due_amount` as something users can freely modify**.

You can store it as a cached/derived value if needed, but the source of truth should be:

```text
Invoice total
-
Payments
=
Outstanding
```

Otherwise you eventually get this nightmare:

```text
Sale says:      Due = 10,000

Payments say:   8,000 paid

Customer says:  "No bhai, I already paid 5,000."
```

Your ledger should be authoritative.

---

# 29. Another important issue: stock valuation

For V1, I'd use:

**weighted average cost**.

Example:

Initial:

```text
100m × Rs 400
```

Then buy:

```text
50m × Rs 500
```

Total:

```text
150m
Total cost = Rs 65,000
```

Average cost:

```text
65,000 / 150
= Rs 433.33/m
```

If customer buys 3m:

```text
COGS ≈ 3 × 433.33
     ≈ Rs 1,300
```

This is much easier to implement than full FIFO.

Later, if the business needs exact batch-level FIFO, you can add inventory lots.

---

# 30. If you want batch-level inventory later

You can add:

```text
inventory_lots
-------------------------
id
variant_id
purchase_id
quantity_received
quantity_remaining
unit_cost
created_at
```

Then:

```text
Purchase #100
100m @ 400
        ↓
Lot A

Purchase #130
50m @ 500
        ↓
Lot B
```

Sale:

```text
3m
 ↓
consume Lot A
```

That's useful if the shopkeeper wants exact historical costing.

But **I wouldn't make this V1 unless you confirm the shops need it.**

---

# 31. Constraints I would absolutely add

PostgreSQL should protect you from bad business data.

For example:

```text
quantity >= 0
selling_price >= 0
purchase_price >= 0
debit >= 0
credit >= 0
```

And especially:

```text
NOT (debit > 0 AND credit > 0)
```

A ledger entry should be either debit or credit.

Also:

```text
debit + credit > 0
```

And:

```text
shop_id
```

should exist on essentially every tenant-owned table.

---

# 32. Indexes

You're going to search these constantly:

```text
products.shop_id
product_variants.shop_id
product_variants.sku
product_variants.barcode

customers.shop_id
customers.phone

suppliers.shop_id

sales.shop_id
sales.created_at
sales.customer_id

purchases.shop_id
purchases.created_at
purchases.supplier_id

inventory_movements.shop_id
inventory_movements.variant_id
inventory_movements.created_at

ledger_entries.shop_id
ledger_entries.account_id
ledger_entries.created_at
```

Especially:

```text
(shop_id, sku)
(shop_id, barcode)
(shop_id, created_at)
```

---

# 33. One very important SaaS security rule

Never trust:

```text
shop_id
```

coming from the frontend.

For example, don't let the frontend send:

```json
{
    "shop_id": "shop-A",
    "product_id": "..."
}
```

and blindly use it.

The authenticated user should determine the shop:

```text
JWT
 ↓
user_id
 ↓
user.shop_id
 ↓
database query
```

So:

```python
products = await session.execute(
    select(Product)
    .where(
        Product.id == product_id,
        Product.shop_id == current_user.shop_id,
    )
)
```

Otherwise Shop A could potentially query Shop B's data.

For a SaaS, that's a **critical security boundary**.

---

# 34. The actual sale service

Your API shouldn't directly manipulate five tables from the route handler.

Instead:

```text
POST /sales
       ↓
SaleService.create_sale()
       ↓
┌───────────────────────────┐
│ PostgreSQL Transaction    │
│                           │
│ Create Sale               │
│ Create Sale Items         │
│ Deduct Inventory          │
│ Create Payment            │
│ Create Ledger Entries     │
│ Commit                    │
└───────────────────────────┘
```

Conceptually:

```python
async def create_sale(
    session,
    shop_id,
    customer_id,
    items,
    payments,
):
    async with session.begin():

        # 1. Validate products
        # 2. Lock inventory rows
        # 3. Validate available stock
        # 4. Calculate totals
        # 5. Create sale
        # 6. Create sale items
        # 7. Create inventory movements
        # 8. Update cached inventory
        # 9. Create payments
        # 10. Create accounting entries

        ...
```

That entire operation is atomic.

Either:

```text
EVERYTHING succeeds
```

or:

```text
NOTHING happens
```

---

# 35. Inventory concurrency matters

Imagine two cashiers.

Both see:

```text
Linen stock = 5m
```

Cashier A sells:

```text
4m
```

Cashier B simultaneously sells:

```text
3m
```

Without row locking:

```text
A sees 5
B sees 5

A sells 4
B sells 3

Database thinks:
stock = 2
```

But actually:

```text
5 - 4 - 3 = -2
```

So during a sale you should lock the inventory row:

```sql
SELECT *
FROM inventory
WHERE variant_id = :variant_id
FOR UPDATE;
```

Then check:

```python
if inventory.quantity < requested_quantity:
    raise InsufficientStock()
```

This is the kind of detail that separates a toy POS from a production system.

---

# 36. The final domain architecture

I'd organize your backend around these modules:

```text
                         SHOP
                          │
        ┌─────────────────┼──────────────────┐
        │                 │                  │
        ▼                 ▼                  ▼
     CATALOG          INVENTORY          PARTIES
        │                 │                  │
        │                 │            ┌─────┴─────┐
        │                 │            │           │
        ▼                 ▼            ▼           ▼
 Categories          Movements     Customers    Suppliers
 Products             Stock
 Variants             Adjustments
 Attributes
 Brands
        │
        └───────────────┐
                        ▼
                    TRANSACTIONS
                        │
                  ┌─────┴─────┐
                  ▼           ▼
                 SALES     PURCHASES
                  │           │
                  ▼           ▼
               Payments    Payments
                  │           │
                  └─────┬─────┘
                        ▼
                    ACCOUNTING
                        │
             ┌──────────┼──────────┐
             ▼          ▼          ▼
           Assets     Revenue    Expenses
           Liabilities
```

---

# 37. And then the AI layer sits above all of it

This is the part I'd be particularly excited about for **your version**.

Don't let the AI agent directly mutate inventory.

Instead:

```text
                    AI Assistant
                         │
                 ┌───────┴────────┐
                 │                │
              READ ONLY        ACTIONS
                 │                │
                 ▼                ▼
             Analytics       Approved APIs
                 │                │
                 ▼                ▼
              Database       Sales Service
                              Purchase Service
                              Inventory Service
```

For example:

> "What should I reorder?"

AI can call:

```text
get_low_stock_products()
get_sales_velocity()
get_recent_purchases()
get_supplier_prices()
```

and reason over the results.

But:

> "Delete this sale."

should **not** be an unrestricted LLM tool.

The agent should call a controlled backend operation with authorization, validation, audit logging, etc.

---

# 38. What I'd build first

If this were our project, I'd do it in this order:

**Phase 1 — Foundation**

```text
Shop
Users
Categories
Brands
Attributes
Products
Variants
```

**Phase 2 — Inventory**

```text
Inventory
Inventory Movements
Purchases
Suppliers
```

**Phase 3 — POS**

```text
Customers
Sales
Sale Items
Payments
Returns
```

**Phase 4 — Financials**

```text
Accounts
Ledger Entries
Expenses
Customer Khata
Supplier Payables
Profit Reports
```

**Phase 5 — Intelligence**

```text
Dashboard
Sales Analytics
Inventory Analytics
Demand Forecasting
AI Business Assistant
```

And I'd use **Alembic migrations** from the very first database version.

---

## One final architectural point

There are actually **two ledgers** here:

```text
INVENTORY LEDGER
────────────────────
What physically happened to stock?

+100m purchase
-3.5m sale
-1m damage
+0.5m return


FINANCIAL LEDGER
────────────────────
What financially happened?

Dr Cash
Dr Receivable
Cr Revenue

Dr COGS
Cr Inventory
```

Keeping those concepts separate while connecting them through the same business transaction is the cleanest architecture.

That gives you a system where the owner can ask:

> **"Mere paas kitna maal hai?"**

→ Inventory.

> **"Mera kitna paisa customers ke paas phansa hua hai?"**

→ Receivables.

> **"Mujhe suppliers ko kitna dena hai?"**

→ Payables.

> **"Maine kitna kamaya?"**

→ Revenue − COGS − Expenses.

> **"Ye 100 meter kapra kahan gaya?"**

→ Inventory movement history.

That's the system I'd build.

And importantly, **you don't need to over-engineer it with microservices or LangGraph**. A well-structured FastAPI modular monolith + PostgreSQL + SQLAlchemy + Redis/background jobs is the right starting point. The AI/agent layer can come later and consume these clean domain services.
