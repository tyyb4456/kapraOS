"""Shop model - the tenant root.

Every shop-owned resource added in later steps (products, sales, inventory,
ledger, ...) will carry a `shop_id` foreign key back to this table. Nothing
here should assume knowledge of those future tables; this step only wires up
`Shop <-> User`.
"""

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.attribute import Attribute
    from app.models.brand import Brand
    from app.models.category import Category
    from app.models.customer import Customer
    from app.models.ledger_entry import LedgerEntry
    from app.models.payment import Payment
    from app.models.product import Product
    from app.models.purchase import Purchase
    from app.models.sale import Sale
    from app.models.supplier import Supplier
    from app.models.user import User


class Shop(Base, UUIDMixin, TimestampMixin):
    """A single tenant (one physical/online shop) in the SaaS."""

    __tablename__ = "shops"

    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_shops_name_not_blank"),
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    phone: Mapped[str | None] = mapped_column(String(30))

    address: Mapped[str | None] = mapped_column(String(300))

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        server_default="PKR",
    )

    users: Mapped[list["User"]] = relationship(
        "User",
        back_populates="shop",
        cascade="all, delete-orphan",
    )

    categories: Mapped[list["Category"]] = relationship(
        "Category",
        back_populates="shop",
        cascade="all, delete-orphan",
    )

    brands: Mapped[list["Brand"]] = relationship(
        "Brand",
        back_populates="shop",
        cascade="all, delete-orphan",
    )

    attributes: Mapped[list["Attribute"]] = relationship(
        "Attribute",
        back_populates="shop",
        cascade="all, delete-orphan",
    )

    # Overlaps with `Category.products` / `Brand.products` (and, from the
    # other side, `Product.category` / `Product.brand`) - all of them
    # participate in populating `products.shop_id`, which is intentional:
    # see the composite foreign keys on `Product` in product.py.
    products: Mapped[list["Product"]] = relationship(
        "Product",
        back_populates="shop",
        cascade="all, delete-orphan",
        overlaps="brand,category,products,shop",
    )

    suppliers: Mapped[list["Supplier"]] = relationship(
        "Supplier",
        back_populates="shop",
        cascade="all, delete-orphan",
    )

    customers: Mapped[list["Customer"]] = relationship(
        "Customer",
        back_populates="shop",
        cascade="all, delete-orphan",
    )

    # Overlaps with `Customer.sales` because both populate
    # `sales.shop_id` / `sales.customer_id` alongside the composite foreign
    # key - see the comment on `Sale.shop`.
    sales: Mapped[list["Sale"]] = relationship(
        "Sale",
        back_populates="shop",
        cascade="all, delete-orphan",
        overlaps="customer,sales,shop",
    )

    # Overlaps with every Payment relationship because all of them populate
    # `payments.shop_id` alongside a composite foreign key.
    payments: Mapped[list["Payment"]] = relationship(
        "Payment",
        back_populates="shop",
        cascade="all, delete-orphan",
        overlaps="customer,payments,purchase,sale,shop,supplier",
    )

    # Overlaps with `Supplier.purchases` because both populate
    # `purchases.shop_id` / `purchases.supplier_id` alongside the composite
    # foreign key - see the comment on `Purchase.shop`.
    purchases: Mapped[list["Purchase"]] = relationship(
        "Purchase",
        back_populates="shop",
        cascade="all, delete-orphan",
        overlaps="purchases,shop,supplier",
    )

    # Accounting (Step 8). Both collections participate in populating their
    # rows' `shop_id`, which is also part of a composite foreign key, hence the
    # explicit `overlaps`.
    accounts: Mapped[list["Account"]] = relationship(
        "Account",
        back_populates="shop",
        cascade="all, delete-orphan",
        overlaps="account,accounts,ledger_entries,shop",
    )

    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(
        "LedgerEntry",
        back_populates="shop",
        cascade="all, delete-orphan",
        overlaps="account,accounts,ledger_entries,shop",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Shop(id={self.id!r}, name={self.name!r})"