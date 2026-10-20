"""Employee invitation model.

An owner-authorized, single-use invitation for one email address to join
one shop with one of the invitable roles (currently `cashier`, which is
the salesman role).

Security properties:
    * The raw token is generated with `secrets.token_urlsafe(32)`, returned
      to the owner exactly once, and never stored: only its SHA-256 hex
      digest is persisted (`token_hash`, unique). A database leak does not
      reveal usable invite links.
    * `expires_at` is mandatory; acceptance refuses expired rows.
    * `accepted_at` / `revoked_at` make the row single-use and revocable.
      Acceptance claims the row with a conditional UPDATE, so two
      concurrent accepts can never both succeed.
    * `email` is stored normalized (trim + lowercase) and acceptance is
      bound to the verified email of the authenticated Clerk identity.
    * `shop_id` scopes every lookup to the inviting owner's tenant; there
      is deliberately no cross-shop invitation path.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.user import UserRole

if TYPE_CHECKING:
    from app.models.shop import Shop
    from app.models.user import User


class InvitationStatus(str, Enum):
    """Derived lifecycle state of an invitation (never stored directly)."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class EmployeeInvitation(Base, UUIDMixin, TimestampMixin):
    """One pending/used invitation from a shop owner to an employee email."""

    __tablename__ = "employee_invitations"

    __table_args__ = (
        # One row per token hash; this is also the lookup index.
        Index(
            "uq_employee_invitations_token_hash",
            "token_hash",
            unique=True,
        ),
        # The owner-facing read pattern: a shop's invitations, newest first.
        Index(
            "ix_employee_invitations_shop_created_at",
            "shop_id",
            "created_at",
        ),
        # The email-lookup pattern used when creating an invitation
        # (duplicate pending guard) and for auditing.
        Index("ix_employee_invitations_email", "email"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Who sent it. SET NULL (not CASCADE): removing an owner/staff row must
    # never silently erase the invitation audit trail.
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        SQLEnum(
            UserRole,
            name="user_role",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    # SHA-256 hex digest of the raw token. The raw token exists only in the
    # creation response and the invite link the owner copies.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    shop: Mapped["Shop"] = relationship(
        "Shop",
        back_populates="employee_invitations",
    )

    invited_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[invited_by_user_id],
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"EmployeeInvitation(id={self.id!r}, shop_id={self.shop_id!r}, "
            f"email={self.email!r}, role={self.role!r})"
        )
