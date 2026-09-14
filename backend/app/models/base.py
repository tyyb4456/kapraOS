"""Shared declarative base and reusable column mixins.

Every model in the system mixes in `UUIDMixin` (surrogate primary key) and
`TimestampMixin` (audit columns), so those behaviors live here exactly once
instead of being redefined table by table.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Project-wide declarative base.

    All models must inherit from this (directly or via a mixin combo) so
    that a single `Base.metadata` object is what Alembic's `env.py` points
    `target_metadata` at for autogeneration.
    """


class UUIDMixin:
    """Adds a UUID primary key generated client-side.

    UUIDs (rather than integers) are used everywhere per the architecture,
    which avoids leaking sequential row counts across tenants and makes IDs
    safe to generate before an INSERT (useful for the sale/inventory/ledger
    write path where several related rows share identifiers).
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """Adds timezone-aware `created_at` / `updated_at` audit columns.

    Both are set by the database (`server_default=func.now()`) rather than
    the application clock, so the value is consistent regardless of which
    app server or timezone issued the write.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )