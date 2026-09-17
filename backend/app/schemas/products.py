"""Response schemas for the catalog domain (Step 2).

These mirror the SQLAlchemy models in app.models for the catalog
domain and are built with model_validate() so the domain never
imports Pydantic.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from app.models.product import ProductType, Unit


class CategoryResponse(BaseModel):
    """A product category, possibly nested."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    parent_id: UUID | None = None
    shop_id: UUID
    created_at: datetime


class CategoryTreeResponse(BaseModel):
    """Category with its children, for hierarchical display."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    parent_id: UUID | None = None
    children: list["CategoryTreeResponse"] = []


class BrandResponse(BaseModel):
    """A product brand."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    shop_id: UUID
    created_at: datetime


class AttributeResponse(BaseModel):
    """A named characteristic (e.g. Fabric, Color)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    shop_id: UUID
    created_at: datetime


class AttributeValueResponse(BaseModel):
    """One concrete value of an attribute."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    attribute_id: UUID
    value: str
    created_at: datetime


class ProductVariantResponse(BaseModel):
    """A concrete sellable item (SKU)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    sku: str
    barcode: str | None = None
    purchase_price: float
    selling_price: float
    unit: str
    is_active: bool
    shop_id: UUID
    created_at: datetime


class ProductResponse(BaseModel):
    """A product identity with its variants."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    product_type: str
    description: str | None = None
    category_id: UUID
    brand_id: UUID | None = None
    shop_id: UUID
    created_at: datetime


class VariantAttributeResponse(BaseModel):
    """The attribute-value tags on a variant."""

    model_config = ConfigDict(from_attributes=True)

    variant_id: UUID
    attribute_id: UUID
    attribute_value_id: UUID
    attribute_name: str
    attribute_value: str


class ProductDetailResponse(BaseModel):
    """Product with all its variants and attribute combinations."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    product_type: str
    description: str | None = None
    category: CategoryResponse | None = None
    brand: BrandResponse | None = None
    variants: list[ProductVariantResponse] = []


class CreateCategoryRequest(BaseModel):
    name: str
    parent_id: UUID | None = None


class CreateBrandRequest(BaseModel):
    name: str


class CreateAttributeRequest(BaseModel):
    name: str


class CreateAttributeValueRequest(BaseModel):
    attribute_id: UUID
    value: str


class CreateProductRequest(BaseModel):
    name: str
    product_type: str
    description: str | None = None
    category_id: UUID
    brand_id: UUID | None = None


class CreateProductVariantRequest(BaseModel):
    product_id: UUID
    sku: str
    barcode: str | None = None
    purchase_price: float
    selling_price: float
    unit: str
    attribute_value_ids: list[UUID] | None = None


class UpdateProductVariantRequest(BaseModel):
    selling_price: float | None = None
    purchase_price: float | None = None
    is_active: bool | None = None
