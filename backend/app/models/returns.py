"""Customer and supplier return records (Step 8 — core return mechanism).

A return is a *business transaction*, not CRUD. It references the original
sale/purchase and its items, stores authoritative pricing from those items,
and drives inventory, receivable/payable and ledger effects via the existing
services. Returns are immutable once created and are never physically
deleted (sale/purchase deletion is blocked while returns exist).

* `SaleReturn` + `SaleReturnItem` — customer returning goods to the shop.
  `total_amount` is the full revenue reversal (net of line + header
  discounts, proportional). `ar_amount` is the portion that reduces the
  customer's receivable (AR-first, up to the sale's remaining due);
  `cash_refund = total - ar` is the cash the shop hands back (ledger only,
  never a `Payment` row — `payments.amount > 0` forbids negative/refund
  rows). `cogs_amount` is the inventory-value reversal
  (`SUM(return_qty * cost_price)`).

* `PurchaseReturn` + `PurchaseReturnItem` — shop returning goods to a
  supplier. The full `total_amount` (net of header-discount share) reduces
  the supplier payable (`Dr AP / Cr Inventory`). No cash movement is
  modelled in V1: an over-returned purchase surfaces as a negative payable
  (supplier credit), consistent with how negative Khatas are already
  surfaced elsewhere.

Quantities are `NUMERIC(14,3)`, money `NUMERIC(14,2)`, never Float.
"""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product import ProductVariant
    from app.models.purchase import Purchase, PurchaseItem
    from app.models.sale import Sale, SaleItem
    from app.models.shop import Shop


class SaleReturn(Base, UUIDMixin, TimestampMixin):
    """One customer-return transaction against a single original sale."""

    __tablename__ = "sale_returns"

    __table_args__ = (
        ForeignKeyConstraint(
            ["sale_id", "shop_id"],
            ["sales.id", "sales.shop_id"],
            name="fk_sale_returns_sale_same_shop",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["customer_id", "shop_id"],
            ["customers.id", "customers.shop_id"],
            name="fk_sale_returns_customer_same_shop",
            ondelete="RESTRICT",
        ),
        Index("ix_sale_returns_shop_created_at", "shop_id", "created_at"),
        Index("ix_sale_returns_shop_sale_created_at", "shop_id", "sale_id", "created_at"),
        CheckConstraint("total_amount >= 0", name="ck_sale_returns_total_non_negative"),
        CheckConstraint("ar_amount >= 0", name="ck_sale_returns_ar_non_negative"),
        CheckConstraint("cash_refund >= 0", name="ck_sale_returns_cash_non_negative"),
        CheckConstraint("cogs_amount >= 0", name="ck_sale_returns_cogs_non_negative"),
        CheckConstraint(
            "total_amount = ar_amount + cash_refund",
            name="ck_sale_returns_total_matches_split",
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sale_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Denormalised from the sale at creation time (NULL == walk-in return).
    # Kept so Khata aggregates can filter returns without joining sales.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    ar_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    cash_refund: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    cogs_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0"), server_default=text("0")
    )

    notes: Mapped[str | None] = mapped_column(String(500))

    shop: Mapped["Shop"] = relationship("Shop", overlaps="sale,shop")
    sale: Mapped["Sale"] = relationship("Sale", overlaps="sale,shop")

    items: Mapped[list["SaleReturnItem"]] = relationship(
        "SaleReturnItem",
        back_populates="return_",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"SaleReturn(id={self.id!r}, sale_id={self.sale_id!r}, total={self.total_amount!r})"


class SaleReturnItem(Base, UUIDMixin):
    """One returned line on a `SaleReturn`.

    No timestamps: lifetime bound to the parent return, like `SaleItem`.
    `discount` is the total discount share for this returned quantity
    (proportional line discount + proportional header-discount share), so
    `total = round(quantity * unit_price, 2) - discount` mirrors `SaleItem`.
    """

    __tablename__ = "sale_return_items"

    __table_args__ = (
        UniqueConstraint(
            "return_id", "sale_item_id", name="uq_sale_return_items_return_sale_item"
        ),
        ForeignKeyConstraint(
            ["variant_id", "shop_id"],
            ["product_variants.id", "product_variants.shop_id"],
            name="fk_sale_return_items_variant_same_shop",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "total = round(quantity * unit_price, 2) - discount",
            name="ck_sale_return_items_total_matches_line",
        ),
        CheckConstraint("quantity > 0", name="ck_sale_return_items_quantity_positive"),
        CheckConstraint(
            "unit_price >= 0", name="ck_sale_return_items_unit_price_non_negative"
        ),
        CheckConstraint(
            "cost_price >= 0", name="ck_sale_return_items_cost_price_non_negative"
        ),
        CheckConstraint("discount >= 0", name="ck_sale_return_items_discount_non_negative"),
        CheckConstraint("total >= 0", name="ck_sale_return_items_total_non_negative"),
    )

    return_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sale_returns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sale_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sale_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    discount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    return_: Mapped["SaleReturn"] = relationship("SaleReturn", back_populates="items")
    sale_item: Mapped["SaleItem"] = relationship("SaleItem")
    variant: Mapped["ProductVariant"] = relationship("ProductVariant")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"SaleReturnItem(return_id={self.return_id!r}, "
            f"sale_item_id={self.sale_item_id!r}, quantity={self.quantity!r})"
        )


class PurchaseReturn(Base, UUIDMixin, TimestampMixin):
    """One supplier-return transaction against a single original purchase."""

    __tablename__ = "purchase_returns"

    __table_args__ = (
        ForeignKeyConstraint(
            ["purchase_id", "shop_id"],
            ["purchases.id", "purchases.shop_id"],
            name="fk_purchase_returns_purchase_same_shop",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "shop_id"],
            ["suppliers.id", "suppliers.shop_id"],
            name="fk_purchase_returns_supplier_same_shop",
            ondelete="RESTRICT",
        ),
        Index("ix_purchase_returns_shop_created_at", "shop_id", "created_at"),
        Index(
            "ix_purchase_returns_shop_purchase_created_at",
            "shop_id",
            "purchase_id",
            "created_at",
        ),
        CheckConstraint(
            "total_amount >= 0", name="ck_purchase_returns_total_non_negative"
        ),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    notes: Mapped[str | None] = mapped_column(String(500))

    shop: Mapped["Shop"] = relationship("Shop", overlaps="purchase,shop")
    purchase: Mapped["Purchase"] = relationship("Purchase", overlaps="purchase,shop")

    items: Mapped[list["PurchaseReturnItem"]] = relationship(
        "PurchaseReturnItem",
        back_populates="return_",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"PurchaseReturn(id={self.id!r}, purchase_id={self.purchase_id!r}, "
            f"total={self.total_amount!r})"
        )


class PurchaseReturnItem(Base, UUIDMixin):
    """One returned line on a `PurchaseReturn`.

    `discount` is the proportional header-discount share for this returned
    quantity, so `total = round(quantity * unit_cost, 2) - discount`.
    """

    __tablename__ = "purchase_return_items"

    __table_args__ = (
        UniqueConstraint(
            "return_id",
            "purchase_item_id",
            name="uq_purchase_return_items_return_purchase_item",
        ),
        ForeignKeyConstraint(
            ["variant_id", "shop_id"],
            ["product_variants.id", "product_variants.shop_id"],
            name="fk_purchase_return_items_variant_same_shop",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "total = round(quantity * unit_cost, 2) - discount",
            name="ck_purchase_return_items_total_matches_line",
        ),
        CheckConstraint(
            "quantity > 0", name="ck_purchase_return_items_quantity_positive"
        ),
        CheckConstraint(
            "unit_cost >= 0", name="ck_purchase_return_items_unit_cost_non_negative"
        ),
        CheckConstraint(
            "discount >= 0", name="ck_purchase_return_items_discount_non_negative"
        ),
        CheckConstraint("total >= 0", name="ck_purchase_return_items_total_non_negative"),
    )

    return_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_returns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    purchase_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    discount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    return_: Mapped["PurchaseReturn"] = relationship(
        "PurchaseReturn", back_populates="items"
    )
    purchase_item: Mapped["PurchaseItem"] = relationship("PurchaseItem")
    variant: Mapped["ProductVariant"] = relationship("ProductVariant")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"PurchaseReturnItem(return_id={self.return_id!r}, "
            f"purchase_item_id={self.purchase_item_id!r}, quantity={self.quantity!r})"
        )
