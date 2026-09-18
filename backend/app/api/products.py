"""Catalog endpoints (Step 2).

CRUD routes for products, categories, brands, attributes, and variants.
All routes are read-only for queries and support creation for
catalog management. The shop is always resolved server-side.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import DbSession, ShopId
from app.models.category import Category
from app.models.brand import Brand
from app.models.attribute import Attribute, AttributeValue
from app.models.product import Product, ProductVariant, ProductType
from app.schemas.products import (
    CategoryResponse,
    CategoryTreeResponse,
    BrandResponse,
    AttributeResponse,
    AttributeValueResponse,
    ProductVariantResponse,
    ProductResponse,
    ProductDetailResponse,
    CreateCategoryRequest,
    CreateBrandRequest,
    CreateAttributeRequest,
    CreateAttributeValueRequest,
    CreateProductRequest,
    CreateProductVariantRequest,
    UpdateProductVariantRequest,
)

router = APIRouter(prefix="/products", tags=["catalog"])


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


# ---- Categories ----

@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(
    shop_id: ShopId,
    db: DbSession,
    parent_id: UUID | None = None,
) -> list[CategoryResponse]:
    stmt = select(Category).where(Category.shop_id == shop_id)
    if parent_id is not None:
        stmt = stmt.where(Category.parent_id == parent_id)
    stmt = stmt.order_by(Category.name.asc())
    categories = (await db.execute(stmt)).scalars().all()
    return [CategoryResponse.model_validate(c) for c in categories]


@router.get("/categories/tree", response_model=list[CategoryTreeResponse])
async def get_category_tree(
    shop_id: ShopId,
    db: DbSession,
) -> list[CategoryTreeResponse]:
    stmt = select(Category).where(
        Category.shop_id == shop_id,
        Category.parent_id.is_(None),
    ).order_by(Category.name.asc())
    roots = (await db.execute(stmt)).scalars().all()

    async def build_tree(category: Category) -> CategoryTreeResponse:
        children_stmt = select(Category).where(
            Category.parent_id == category.id
        ).order_by(Category.name.asc())
        children = (await db.execute(children_stmt)).scalars().all()
        return CategoryTreeResponse(
            id=category.id,
            name=category.name,
            parent_id=category.parent_id,
            children=[build_tree(c) for c in children],
        )

    return [await build_tree(c) for c in roots]


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    shop_id: ShopId,
    db: DbSession,
    body: CreateCategoryRequest,
) -> CategoryResponse:
    category = Category(
        shop_id=shop_id,
        name=body.name,
        parent_id=body.parent_id,
    )
    db.add(category)
    await db.flush()
    await db.commit()
    return CategoryResponse.model_validate(category)


# ---- Brands ----

@router.get("/brands", response_model=list[BrandResponse])
async def list_brands(
    shop_id: ShopId,
    db: DbSession,
) -> list[BrandResponse]:
    brands = (await db.execute(
        select(Brand).where(Brand.shop_id == shop_id).order_by(Brand.name.asc())
    )).scalars().all()
    return [BrandResponse.model_validate(b) for b in brands]


@router.post("/brands", response_model=BrandResponse, status_code=status.HTTP_201_CREATED)
async def create_brand(
    shop_id: ShopId,
    db: DbSession,
    body: CreateBrandRequest,
) -> BrandResponse:
    brand = Brand(shop_id=shop_id, name=body.name)
    db.add(brand)
    await db.flush()
    await db.commit()
    return BrandResponse.model_validate(brand)


# ---- Attributes ----

@router.get("/attributes", response_model=list[AttributeResponse])
async def list_attributes(
    shop_id: ShopId,
    db: DbSession,
) -> list[AttributeResponse]:
    attrs = (await db.execute(
        select(Attribute).where(Attribute.shop_id == shop_id).order_by(Attribute.name.asc())
    )).scalars().all()
    return [AttributeResponse.model_validate(a) for a in attrs]


@router.post("/attributes", response_model=AttributeResponse, status_code=status.HTTP_201_CREATED)
async def create_attribute(
    shop_id: ShopId,
    db: DbSession,
    body: CreateAttributeRequest,
) -> AttributeResponse:
    attr = Attribute(shop_id=shop_id, name=body.name)
    db.add(attr)
    await db.flush()
    await db.commit()
    return AttributeResponse.model_validate(attr)


@router.get("/attributes/{attribute_id}/values", response_model=list[AttributeValueResponse])
async def list_attribute_values(
    attribute_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> list[AttributeValueResponse]:
    values = (await db.execute(
        select(AttributeValue).where(
            AttributeValue.attribute_id == attribute_id,
        ).order_by(AttributeValue.value.asc())
    )).scalars().all()
    return [AttributeValueResponse.model_validate(v) for v in values]


@router.post("/attribute-values", response_model=AttributeValueResponse, status_code=status.HTTP_201_CREATED)
async def create_attribute_value(
    shop_id: ShopId,
    db: DbSession,
    body: CreateAttributeValueRequest,
) -> AttributeValueResponse:
    value = AttributeValue(
        attribute_id=body.attribute_id,
        value=body.value,
    )
    db.add(value)
    await db.flush()
    await db.commit()
    return AttributeValueResponse.model_validate(value)


# ---- Products ----

@router.get("", response_model=list[ProductResponse])
async def list_products(
    shop_id: ShopId,
    db: DbSession,
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    product_type: str | None = None,
    search: str | None = None,
) -> list[ProductResponse]:
    stmt = select(Product).where(Product.shop_id == shop_id)
    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)
    if brand_id is not None:
        stmt = stmt.where(Product.brand_id == brand_id)
    if product_type is not None:
        stmt = stmt.where(Product.product_type == product_type)
    if search is not None:
        stmt = stmt.where(Product.name.ilike(f"%{search}%"))
    stmt = stmt.order_by(Product.name.asc())
    products = (await db.execute(stmt)).scalars().all()
    return [ProductResponse.model_validate(p) for p in products]


@router.get("/{product_id}", response_model=ProductDetailResponse)
async def get_product(
    product_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> ProductDetailResponse:
    product = await db.get(Product, product_id)
    if product is None or product.shop_id != shop_id:
        raise _not_found(f"Product {product_id} not found")
    variants = (await db.execute(
        select(ProductVariant).where(ProductVariant.product_id == product_id)
    )).scalars().all()
    return ProductDetailResponse.model_validate(product)


def _resolve_unit(unit_str: str) -> str:
    mapping = {
        "meters": "meter", "meter": "meter",
        "yards": "yard", "yard": "yard",
        "pieces": "piece", "piece": "piece",
        "sets": "set", "set": "set",
        "rolls": "roll", "roll": "roll",
    }
    return mapping.get(unit_str.lower(), unit_str)


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    shop_id: ShopId,
    db: DbSession,
    body: CreateProductRequest,
) -> ProductResponse:
    category_id = body.category_id
    if isinstance(category_id, str):
        category_id = UUID(category_id)

    brand_id = body.brand_id
    if isinstance(brand_id, str):
        brand_id = UUID(brand_id)

    try:
        product_type = ProductType(body.product_type)
    except ValueError:
        product_type = ProductType.OTHER

    product = Product(
        shop_id=shop_id,
        name=body.name,
        product_type=product_type,
        description=body.description,
        category_id=category_id,
        brand_id=brand_id,
        code=body.code,
        unit=body.unit,
    )
    db.add(product)
    await db.flush()
    await db.refresh(product)

    for v in body.variants:
        variant = ProductVariant(
            product_id=product.id,
            shop_id=shop_id,
            sku=v.sku,
            barcode=v.barcode,
            purchase_price=Decimal(str(v.purchase_price)),
            selling_price=Decimal(str(v.selling_price)),
            unit=_resolve_unit(v.unit),
        )
        db.add(variant)

    await db.flush()
    await db.commit()
    return ProductResponse.model_validate(product)


# ---- Product Variants ----

@router.get("/variants", response_model=list[ProductVariantResponse])
async def list_variants(
    shop_id: ShopId,
    db: DbSession,
    product_id: UUID | None = None,
    sku: str | None = None,
) -> list[ProductVariantResponse]:
    stmt = select(ProductVariant)
    if product_id is not None:
        stmt = stmt.where(ProductVariant.product_id == product_id)
    if sku is not None:
        stmt = stmt.where(ProductVariant.sku == sku)
    stmt = stmt.order_by(ProductVariant.sku.asc())
    variants = (await db.execute(stmt)).scalars().all()
    return [ProductVariantResponse.model_validate(v) for v in variants]


@router.get("/variants/{variant_id}", response_model=ProductVariantResponse)
async def get_variant(
    variant_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> ProductVariantResponse:
    variant = await db.get(ProductVariant, variant_id)
    if variant is None or variant.shop_id != shop_id:
        raise _not_found(f"Variant {variant_id} not found")
    return ProductVariantResponse.model_validate(variant)


@router.post("/variants", response_model=ProductVariantResponse, status_code=status.HTTP_201_CREATED)
async def create_variant(
    shop_id: ShopId,
    db: DbSession,
    body: CreateProductVariantRequest,
) -> ProductVariantResponse:
    variant = ProductVariant(
        product_id=body.product_id,
        sku=body.sku,
        barcode=body.barcode,
        purchase_price=body.purchase_price,
        selling_price=body.selling_price,
        unit=body.unit,
        shop_id=shop_id,
    )
    db.add(variant)
    await db.flush()
    await db.commit()
    return ProductVariantResponse.model_validate(variant)


@router.patch("/variants/{variant_id}", response_model=ProductVariantResponse)
async def update_variant(
    variant_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    body: UpdateProductVariantRequest,
) -> ProductVariantResponse:
    variant = await db.get(ProductVariant, variant_id)
    if variant is None or variant.shop_id != shop_id:
        raise _not_found(f"Variant {variant_id} not found")
    if body.selling_price is not None:
        variant.selling_price = body.selling_price
    if body.purchase_price is not None:
        variant.purchase_price = body.purchase_price
    if body.is_active is not None:
        variant.is_active = body.is_active
    await db.flush()
    await db.commit()
    return ProductVariantResponse.model_validate(variant)
