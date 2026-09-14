"""Model registry.

Every model module must be imported here so that:

1. `Base.metadata` is fully populated for Alembic autogeneration
   (`alembic/env.py` imports `Base` from this package).
2. Application code can do `from app.models import Shop, User` instead of
   reaching into individual modules.

As new modules are added in later steps (products, inventory, sales, ...),
import them here too.
"""

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.shop import Shop
from app.models.user import User, UserRole

__all__ = [
    "Base",
    "TimestampMixin",
    "UUIDMixin",
    "Shop",
    "User",
    "UserRole",
]