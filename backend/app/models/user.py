"""User model.

Clerk owns authentication; this table stores the application-level
identity that links a Clerk user to exactly one Shop (tenant).

Fields:
    id          - UUID primary key
    shop_id     - FK to the tenant Shop (server-derived, never client-supplied)
    clerk_user_id - stable Clerk user identifier (unique)
    role        - OWNER or STAFF
    name        - display name (optional, populated at provisioning)
    email       - optional, kept for compatibility
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
    """Minimal role set for V1: OWNER or STAFF."""

    OWNER = "owner"
    STAFF = "staff"


class User(Base, UUIDMixin, TimestampMixin):
    """An application user bound to exactly one Shop."""

    __tablename__ = "users"

    __table_args__ = (
        UniqueConstraint("clerk_user_id", name="uq_users_clerk_user_id"),
        UniqueConstraint("email", name="uq_users_email"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
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

    shop: Mapped["Shop"] = relationship(
        "Shop",
        back_populates="users",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"User(id={self.id!r}, clerk_user_id={self.clerk_user_id!r}, role={self.role!r})"