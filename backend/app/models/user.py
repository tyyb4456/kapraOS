"""User model.

Clerk owns authentication; this table stores the application-level
identity that links a Clerk user to their tenant Shop - or to no shop at
all for a platform administrator operating the Super Admin Panel.

Fields:
    id          - UUID primary key
    shop_id     - FK to the tenant Shop (server-derived, never client-supplied);
                  NULL for platform administrators and for employees whose
                  shop access has been revoked
    clerk_user_id - stable Clerk user identifier (unique)
    role        - OWNER, CASHIER or STAFF (legacy non-owner role)
    name        - display name (optional, populated at provisioning)
    email       - optional, kept for compatibility
    is_platform_admin - KapraOS platform-operator flag (default: false)

Role semantics (enforced server-side in `app.auth.auth` / the API routers):
    OWNER   - full access to their own shop.
    CASHIER - employee invited by an owner; POS sales plus the read-only
              catalog/inventory/customer data needed to make them.
    STAFF   - legacy non-owner role; treated exactly like CASHIER. It is
              never granted by signup - only pre-existing rows carry it.
"""

import uuid
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint, false
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.shop import Shop


class UserRole(str, Enum):
    """Shop-level role set.

    OWNER is the shop administrator. CASHIER is an invited employee
    ("salesman") limited to POS workflows. STAFF is the legacy non-owner
    role kept for backward compatibility and is treated as CASHIER.
    """

    OWNER = "owner"
    CASHIER = "cashier"
    STAFF = "staff"


class User(Base, UUIDMixin, TimestampMixin):
    """An application user bound to a Shop, or a shop-less platform admin."""

    __tablename__ = "users"

    __table_args__ = (
        UniqueConstraint("clerk_user_id", name="uq_users_clerk_user_id"),
        UniqueConstraint("email", name="uq_users_email"),
    )

    shop_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    clerk_user_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    email: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        SQLEnum(
            UserRole,
            name="user_role",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=UserRole.STAFF.value,
    )

    # Platform-operator flag. False for every normal shop user; only a
    # direct database operation (the `grant_platform_admin` script) may
    # set it. It is intentionally NOT derived from `role` or from any
    # client-supplied value.
    is_platform_admin: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default=false(),
    )

    shop: Mapped["Shop | None"] = relationship(
        "Shop",
        back_populates="users",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"User(id={self.id!r}, clerk_user_id={self.clerk_user_id!r}, role={self.role!r})"