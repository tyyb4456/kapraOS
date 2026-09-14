"""User model.

A User always belongs to exactly one Shop (`shop_id` is non-nullable with a
CASCADE delete), which is the multi-tenancy foundation described in the
architecture. Authentication (JWT, password hashing) is intentionally out of
scope for this step - only the column that will hold a pre-hashed password
is defined.
"""

import uuid
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.shop import Shop


class UserRole(str, Enum):
    """Roles referenced by the architecture doc's authorization model."""

    OWNER = "owner"
    MANAGER = "manager"
    CASHIER = "cashier"
    INVENTORY_MANAGER = "inventory_manager"


class User(Base, UUIDMixin, TimestampMixin):
    """A staff member (owner/manager/cashier/...) scoped to one shop."""

    __tablename__ = "users"

    __table_args__ = (
        # Login is by email; scoping uniqueness globally (rather than per
        # shop) is the simplest production-safe default for a single
        # sign-in system and avoids ambiguous "which shop did I mean to log
        # into" flows. Revisit if a future requirement needs one person to
        # hold accounts in multiple shops under the same email.
        UniqueConstraint("email", name="uq_users_email"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    email: Mapped[str] = mapped_column(String(255), nullable=False)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        SQLEnum(
            UserRole,
            name="user_role",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=UserRole.CASHIER.value,
    )

    shop: Mapped["Shop"] = relationship(
        "Shop",
        back_populates="users",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"User(id={self.id!r}, email={self.email!r}, role={self.role!r})"