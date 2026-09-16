"""Chart of accounts - Step 8's accounting layer (`db_arch.md` section 23).

An `Account` is one line of a shop's chart of accounts. Every account belongs
to exactly one shop, and its `(id, shop_id)` pair carries a supporting
`UNIQUE` constraint so `LedgerEntry` can target it with a composite foreign
key - a ledger entry belonging to Shop A can never point at Shop B's account,
and that is enforced by the database, not just the service layer.

`is_system` marks accounts the application manages (Cash, Bank, AR, Inventory,
AP, Equity, Sales Revenue). `ensure_system_accounts()` in
`app.services.accounting` creates them idempotently per shop; users are not
expected to create them by hand. This step deliberately ships no
chart-of-accounts management UI (`step_8_description.md` section 5).
"""

import uuid
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.ledger_entry import LedgerEntry
    from app.models.shop import Shop


class AccountType(str, Enum):
    """The five classic account types (`db_arch.md` section 23).

    The type decides which side increases the account's balance: ASSET and
    EXPENSE are debit-normal, LIABILITY / EQUITY / REVENUE are credit-normal.
    """

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class Account(Base, UUIDMixin, TimestampMixin):
    """One account in one shop's chart of accounts."""

    __tablename__ = "accounts"

    __table_args__ = (
        # Supports `LedgerEntry`'s composite foreign key (account_id, shop_id),
        # so a ledger entry can never post against another shop's account.
        UniqueConstraint("id", "shop_id", name="uq_accounts_id_shop_id"),
        # Account codes are unique per shop only - two shops both have a
        # "1000 Cash" account, which is expected, not a conflict.
        UniqueConstraint("shop_id", "code", name="uq_accounts_shop_code"),
        Index("ix_accounts_shop_type", "shop_id", "account_type"),
        CheckConstraint("length(trim(code)) > 0", name="ck_accounts_code_not_blank"),
        CheckConstraint("length(trim(name)) > 0", name="ck_accounts_name_not_blank"),
    )

    shop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    code: Mapped[str] = mapped_column(String(20), nullable=False)

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    account_type: Mapped[AccountType] = mapped_column(
        SQLEnum(
            AccountType,
            name="account_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    # True for accounts the application creates and manages. Users should not
    # be able to delete or deactivate these by accident.
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    # `shop` participates in populating `shop_id`, which is also part of the
    # composite foreign key on `LedgerEntry.account` - hence the explicit
    # `overlaps` rather than a wall of SQLAlchemy warnings on import.
    shop: Mapped["Shop"] = relationship(
        "Shop",
        back_populates="accounts",
        overlaps="account,accounts,ledger_entries,shop",
    )

    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(
        "LedgerEntry",
        back_populates="account",
        cascade="all, delete-orphan",
        overlaps="account,accounts,ledger_entries,shop",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Account(id={self.id!r}, code={self.code!r}, name={self.name!r})"