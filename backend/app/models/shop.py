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

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Shop(id={self.id!r}, name={self.name!r})"