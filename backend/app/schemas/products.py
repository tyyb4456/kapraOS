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
    products_count: int = 0


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
    code: str | None = None
    product_type: str
    description: str | None = None
    category_id: UUID | None = None
    brand_id: UUID | None = None
    unit: str
    shop_id: UUID
    created_at: datetime
    category: CategoryResponse | None = None
    variants: list[ProductVariantResponse] = []


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
    code: str | None = None
    product_type: str
    description: str | None = None
    category: CategoryResponse | None = None
    brand: BrandResponse | None = None
    unit: str
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


class CreateProductVariantRequest(BaseModel):
    product_id: UUID | None = None
    sku: str
    barcode: str | None = None
    purchase_price: float
    selling_price: float
    unit: str
    attributes: dict[str, str] | None = None


class CreateProductRequest(BaseModel):
    name: str
    code: str | None = None
    product_type: str = "other"
    description: str | None = None
    category_id: UUID | str
    brand_id: UUID | None = None
    unit: str = "meter"
    variants: list[CreateProductVariantRequest] = []


class UpdateProductVariantRequest(BaseModel):
    selling_price: float | None = None
    purchase_price: float | None = None
    is_active: bool | None = None
    sku: str | None = None
    barcode: str | None = None
    unit: str | None = None


class UpdateProductRequest(BaseModel):
    """Partial edit for a product - omitted keys are left alone."""

    name: str | None = None
    code: str | None = None
    product_type: str | None = None
    description: str | None = None
    category_id: UUID | None = None
    brand_id: UUID | None = None
    unit: str | None = None


class UpdateCategoryRequest(BaseModel):
    name: str | None = None
    parent_id: UUID | None = None


class UpdateBrandRequest(BaseModel):
    name: str | None = None


class UpdateAttributeRequest(BaseModel):
    name: str | None = None


class UpdateAttributeValueRequest(BaseModel):
    value: str | None = None
