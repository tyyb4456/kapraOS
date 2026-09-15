"""Model registry.

Every model module must be imported here so that:

1. `Base.metadata` is fully populated for Alembic autogeneration
   (`alembic/env.py` imports `Base` from this package).
2. Application code can do `from app.models import Shop, User` instead of
   reaching into individual modules.

As new modules are added in later steps (products, inventory, sales, ...),
import them here too.
"""

from app.models.attribute import Attribute, AttributeValue
from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.brand import Brand
from app.models.category import Category
from app.models.inventory import (
    Inventory,
    InventoryMovement,
    InventoryMovementType,
)
from app.models.product import (
    Product,
    ProductType,
    ProductVariant,
    Unit,
    VariantAttributeValue,
)
from app.models.purchase import Purchase, PurchaseItem
from app.models.shop import Shop
from app.models.supplier import Supplier
from app.models.user import User, UserRole

__all__ = [
    "Attribute",
    "AttributeValue",
    "Base",
    "Brand",
    "Category",
    "Inventory",
    "InventoryMovement",
    "InventoryMovementType",
    "Product",
    "ProductType",
    "ProductVariant",
    "Purchase",
    "PurchaseItem",
    "Shop",
    "Supplier",
    "TimestampMixin",
    "UUIDMixin",
    "Unit",
    "User",
    "UserRole",
    "VariantAttributeValue",
]