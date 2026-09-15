"""Supplier model - who a shop buys stock from.

Deliberately minimal (`db_arch.md` section 16): many fabric-shop suppliers are
informal local wholesalers, so only `name` is required. Phone, address and
notes are all optional and no formal business fields are assumed.

`Supplier` is tenant-owned and carries a supporting UNIQUE(id, shop_id) so that
`Purchase` can target it with a composite foreign key - a purchase can never
reference a supplier belonging to a different shop.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.purchase import Purchase
    from app.models.shop import Shop


class Supplier(Base, UUIDMixin, TimestampMixin):
    """A wholesaler / supplier scoped to exactly one shop."""

    __tablename__ = "suppliers"

    __table_args__ = (
        # Supports `Purchase`'s composite foreign key (supplier_id, shop_id) -
        # see purchase.py - so a purchase can never attach to a supplier from
        # another shop. Postgres needs an explicit unique constraint on exactly
        # this column pair even though `id` alone is already the primary key.
        UniqueConstraint("id", "shop_id", name="uq_suppliers_id_shop_id"),
        # Supplier names are not unique per shop: two suppliers can legitimately
        # share a name, so these are plain lookup indexes, not constraints.
        Index("ix_suppliers_shop_name", "shop_id", "name"),
        Index("ix_suppliers_shop_phone", "shop_id", "phone"),
        CheckConstraint("length(trim(name)) > 0", name="ck_suppliers_name_not_blank"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    phone: Mapped[str | None] = mapped_column(String(30))

    address: Mapped[str | None] = mapped_column(String(300))

    notes: Mapped[str | None] = mapped_column(String(500))

    shop: Mapped["Shop"] = relationship("Shop", back_populates="suppliers")

    purchases: Mapped[list["Purchase"]] = relationship(
        "Purchase",
        back_populates="supplier",
        # No delete cascade on purpose: the FK is ON DELETE RESTRICT, so a
        # supplier with purchase history cannot be removed and silently take
        # that history with it.
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Supplier(id={self.id!r}, name={self.name!r})"