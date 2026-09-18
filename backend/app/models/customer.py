"""Customer model - who a shop sells to.

Deliberately minimal (`db_arch.md` section 15): many fabric-shop customers are
walk-ins, so only `name` is required and phone/address/notes are all optional.
A sale does *not* require a customer at all - `customer_id IS NULL` is the
walk-in case and is a first-class value, never a synthetic "Walk-in" row
(the same design principle as unbranded products, see `brand_conf.md`).

`Customer` is tenant-owned and carries a supporting UNIQUE(id, shop_id) so
that `Sale` can target it with a composite foreign key - a sale can never
reference a customer belonging to a different shop.
"""

import uuid
from typing import TYPE_CHECKING

from decimal import Decimal
from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.sale import Sale
    from app.models.shop import Shop


class Customer(Base, UUIDMixin, TimestampMixin):
    """A person (or walk-in account) a shop sells to, scoped to one shop."""

    __tablename__ = "customers"

    __table_args__ = (
        # Supports `Sale`'s composite foreign key (customer_id, shop_id) and
        # `Payment`'s (customer_id, shop_id) - see sale.py / payment.py - so a
        # sale or payment can never attach to a customer from another shop.
        # Postgres needs an explicit unique constraint on exactly this column
        # pair even though `id` alone is already the primary key.
        UniqueConstraint("id", "shop_id", name="uq_customers_id_shop_id"),
        # Customer names/phones are not unique per shop (two Ahmeds, shared
        # family phone numbers), so these are plain lookup indexes.
        Index("ix_customers_shop_name", "shop_id", "name"),
        Index("ix_customers_shop_phone", "shop_id", "phone"),
        CheckConstraint("length(trim(name)) > 0", name="ck_customers_name_not_blank"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    # Optional: a walk-in may leave just a name, and many regulars have no
    # phone recorded at all.
    phone: Mapped[str | None] = mapped_column(String(30))

    email: Mapped[str | None] = mapped_column(String(255))

    credit_limit: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )

    address: Mapped[str | None] = mapped_column(String(300))

    notes: Mapped[str | None] = mapped_column(String(500))

    shop: Mapped["Shop"] = relationship("Shop", back_populates="customers")

    sales: Mapped[list["Sale"]] = relationship(
        "Sale",
        back_populates="customer",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Customer(id={self.id!r}, name={self.name!r})"