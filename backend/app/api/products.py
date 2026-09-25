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
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import DbSession, ShopId
from app.models.category import Category
from app.models.brand import Brand
from app.models.attribute import Attribute, AttributeValue
from app.models.inventory import Inventory, InventoryMovement
from app.models.product import Product, ProductVariant, ProductType
from app.models.purchase import PurchaseItem
from app.models.sale import SaleItem
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
    UpdateProductRequest,
    UpdateCategoryRequest,
    UpdateBrandRequest,
    UpdateAttributeRequest,
    UpdateAttributeValueRequest,
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
    counts = dict((await db.execute(
        select(Product.category_id, func.count(Product.id))
        .where(Product.shop_id == shop_id)
        .group_by(Product.category_id)
    )).all())
    return [
        CategoryResponse(
            id=c.id,
            name=c.name,
            parent_id=c.parent_id,
            shop_id=c.shop_id,
            created_at=c.created_at,
            products_count=int(counts.get(c.id, 0)),
        )
        for c in categories
    ]


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
    if not body.name or not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Category name must not be blank",
        )
    if body.parent_id is not None:
        parent = (await db.execute(
            select(Category).where(
                Category.id == body.parent_id, Category.shop_id == shop_id
            )
        )).scalar_one_or_none()
        if parent is None:
            raise _not_found(f"Parent category {body.parent_id} not found")
    category = Category(
        shop_id=shop_id,
        name=body.name.strip(),
        parent_id=body.parent_id,
    )
    db.add(category)
    await db.flush()
    await db.commit()
    return CategoryResponse.model_validate(category)


@router.patch("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    body: UpdateCategoryRequest,
) -> CategoryResponse:
    category = (await db.execute(
        select(Category).where(
            Category.id == category_id, Category.shop_id == shop_id
        )
    )).scalar_one_or_none()
    if category is None:
        raise _not_found(f"Category {category_id} not found")
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        name = fields["name"].strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Category name must not be blank",
            )
        category.name = name
    if "parent_id" in fields:
        new_parent = fields["parent_id"]
        if new_parent is not None:
            if new_parent == category.id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="A category cannot be its own parent",
                )
            parent = (await db.execute(
                select(Category).where(
                    Category.id == new_parent, Category.shop_id == shop_id
                )
            )).scalar_one_or_none()
            if parent is None:
                raise _not_found(f"Parent category {new_parent} not found")
        category.parent_id = new_parent
    await db.flush()
    await db.commit()
    return CategoryResponse.model_validate(category)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    force: bool = Query(default=False, description="Also permanently delete the category's products and their stock history."),
) -> None:
    category = (await db.execute(
        select(Category).where(
            Category.id == category_id, Category.shop_id == shop_id
        )
    )).scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    children = (await db.execute(
        select(func.count(Category.id)).where(Category.parent_id == category_id)
    )).scalar_one()
    if (children or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete category with {children} sub-categorie(s). "
                "Delete or move the sub-categories first."
            ),
        )
    product_ids = (await db.execute(
        select(Product.id).where(
            Product.category_id == category_id, Product.shop_id == shop_id
        )
    )).scalars().all()
    if product_ids and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete category with {len(product_ids)} product(s). "
                "Move or delete them first, or retry with ?force=true to permanently "
                "delete the products and their stock history."
            ),
        )
    for product_id in product_ids:
        product = (await db.execute(
            select(Product).where(Product.id == product_id)
        )).scalar_one()
        await _delete_product_row(db, shop_id, product, force=True)
    await db.delete(category)
    await db.commit()


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
    if not body.name or not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Brand name must not be blank",
        )
    brand = Brand(shop_id=shop_id, name=body.name.strip())
    db.add(brand)
    await db.flush()
    await db.commit()
    return BrandResponse.model_validate(brand)


@router.patch("/brands/{brand_id}", response_model=BrandResponse)
async def update_brand(
    brand_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    body: UpdateBrandRequest,
) -> BrandResponse:
    brand = (await db.execute(
        select(Brand).where(Brand.id == brand_id, Brand.shop_id == shop_id)
    )).scalar_one_or_none()
    if brand is None:
        raise _not_found(f"Brand {brand_id} not found")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Brand name must not be blank",
            )
        brand.name = name
    await db.flush()
    await db.commit()
    return BrandResponse.model_validate(brand)


@router.delete("/brands/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brand(
    brand_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    brand = (await db.execute(
        select(Brand).where(Brand.id == brand_id, Brand.shop_id == shop_id)
    )).scalar_one_or_none()
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")
    # Unassign rather than block: unbranded products are first-class (NULL).
    await db.execute(
        Product.__table__.update()
        .where(Product.brand_id == brand_id, Product.shop_id == shop_id)
        .values(brand_id=None)
    )
    await db.delete(brand)
    await db.commit()


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
    if not body.name or not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Attribute name must not be blank",
        )
    attr = Attribute(shop_id=shop_id, name=body.name.strip())
    db.add(attr)
    await db.flush()
    await db.commit()
    return AttributeResponse.model_validate(attr)


@router.patch("/attributes/{attribute_id}", response_model=AttributeResponse)
async def update_attribute(
    attribute_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    body: UpdateAttributeRequest,
) -> AttributeResponse:
    attr = (await db.execute(
        select(Attribute).where(
            Attribute.id == attribute_id, Attribute.shop_id == shop_id
        )
    )).scalar_one_or_none()
    if attr is None:
        raise _not_found(f"Attribute {attribute_id} not found")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Attribute name must not be blank",
            )
        attr.name = name
    await db.flush()
    await db.commit()
    return AttributeResponse.model_validate(attr)


@router.delete("/attributes/{attribute_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attribute(
    attribute_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    attr = (await db.execute(
        select(Attribute).where(
            Attribute.id == attribute_id, Attribute.shop_id == shop_id
        )
    )).scalar_one_or_none()
    if attr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attribute not found")
    await db.delete(attr)
    await db.commit()


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
    attr = (await db.execute(
        select(Attribute).where(
            Attribute.id == body.attribute_id, Attribute.shop_id == shop_id
        )
    )).scalar_one_or_none()
    if attr is None:
        raise _not_found(f"Attribute {body.attribute_id} not found")
    if not body.value or not body.value.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Attribute value must not be blank",
        )
    value = AttributeValue(
        attribute_id=body.attribute_id,
        value=body.value.strip(),
    )
    db.add(value)
    await db.flush()
    await db.commit()
    return AttributeValueResponse.model_validate(value)


@router.patch("/attribute-values/{value_id}", response_model=AttributeValueResponse)
async def update_attribute_value(
    value_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    body: UpdateAttributeValueRequest,
) -> AttributeValueResponse:
    row = (await db.execute(
        select(AttributeValue)
        .join(Attribute, AttributeValue.attribute_id == Attribute.id)
        .where(AttributeValue.id == value_id, Attribute.shop_id == shop_id)
    )).scalar_one_or_none()
    if row is None:
        raise _not_found(f"Attribute value {value_id} not found")
    if body.value is not None:
        value = body.value.strip()
        if not value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Attribute value must not be blank",
            )
        row.value = value
    await db.flush()
    await db.commit()
    return AttributeValueResponse.model_validate(row)


@router.delete("/attribute-values/{value_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attribute_value(
    value_id: UUID,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    row = (await db.execute(
        select(AttributeValue)
        .join(Attribute, AttributeValue.attribute_id == Attribute.id)
        .where(AttributeValue.id == value_id, Attribute.shop_id == shop_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attribute value not found")
    await db.delete(row)
    await db.commit()


# ---- Product Variants (registered before /{product_id} so /variants is reachable) ----

@router.get("/variants", response_model=list[ProductVariantResponse])
async def list_variants(
    shop_id: ShopId,
    db: DbSession,
    product_id: UUID | None = None,
    sku: str | None = None,
) -> list[ProductVariantResponse]:
    stmt = select(ProductVariant).where(ProductVariant.shop_id == shop_id)
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
    if body.product_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="product_id is required",
        )
    product = (await db.execute(
        select(Product).where(Product.id == body.product_id, Product.shop_id == shop_id)
    )).scalar_one_or_none()
    if product is None:
        raise _not_found(f"Product {body.product_id} not found")
    if not body.sku or not body.sku.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Variant SKU must not be blank",
        )
    variant = ProductVariant(
        product_id=body.product_id,
        sku=body.sku.strip(),
        barcode=body.barcode,
        purchase_price=body.purchase_price,
        selling_price=body.selling_price,
        unit=_resolve_unit(body.unit),
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
    fields = body.model_dump(exclude_unset=True)
    if "selling_price" in fields and fields["selling_price"] is not None:
        if fields["selling_price"] < 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="selling_price must be >= 0",
            )
        variant.selling_price = fields["selling_price"]
    if "purchase_price" in fields and fields["purchase_price"] is not None:
        if fields["purchase_price"] < 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="purchase_price must be >= 0",
            )
        variant.purchase_price = fields["purchase_price"]
    if "is_active" in fields and fields["is_active"] is not None:
        variant.is_active = fields["is_active"]
    if "sku" in fields and fields["sku"] is not None:
        sku = fields["sku"].strip()
        if not sku:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Variant SKU must not be blank",
            )
        clash = (await db.execute(
            select(ProductVariant.id).where(
                ProductVariant.shop_id == shop_id,
                ProductVariant.sku == sku,
                ProductVariant.id != variant_id,
            ).limit(1)
        )).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"SKU {sku!r} is already used in this shop",
            )
        variant.sku = sku
    if "barcode" in fields:
        barcode = fields["barcode"]
        if barcode is not None and isinstance(barcode, str) and barcode.strip() == "":
            barcode = None
        if barcode is not None:
            clash = (await db.execute(
                select(ProductVariant.id).where(
                    ProductVariant.shop_id == shop_id,
                    ProductVariant.barcode == barcode,
                    ProductVariant.id != variant_id,
                ).limit(1)
            )).scalar_one_or_none()
            if clash is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Barcode {barcode!r} is already used in this shop",
                )
        variant.barcode = barcode
    if "unit" in fields and fields["unit"] is not None:
        variant.unit = _resolve_unit(fields["unit"])
    await db.flush()
    await db.commit()
    return ProductVariantResponse.model_validate(variant)


async def _variant_document_refs(
    db: DbSession, variant_id: UUID
) -> str:
    """Sale/purchase line references that must never be cascade-destroyed.

    `SaleItem.variant_id` / `PurchaseItem.variant_id` are ON DELETE CASCADE,
    so deleting a variant still referenced by a document would silently strip
    lines out of that sale/purchase and corrupt its totals. Returns a human
    readable detail string, or "" when the variant is document-free.
    """

    sale_lines = (await db.execute(
        select(func.count(SaleItem.id)).where(SaleItem.variant_id == variant_id)
    )).scalar_one() or 0
    purchase_lines = (await db.execute(
        select(func.count(PurchaseItem.id)).where(PurchaseItem.variant_id == variant_id)
    )).scalar_one() or 0
    if sale_lines > 0 or purchase_lines > 0:
        return f"{sale_lines} sale line(s), {purchase_lines} purchase line(s)"
    return ""


async def _variant_has_history(
    db: DbSession, shop_id: UUID, variant_id: UUID
) -> tuple[bool, str]:
    """Check whether a variant is referenced by stock history or documents."""

    movements = (await db.execute(
        select(func.count(InventoryMovement.id)).where(
            InventoryMovement.variant_id == variant_id,
            InventoryMovement.shop_id == shop_id,
        )
    )).scalar_one() or 0
    if movements > 0:
        return True, f"{movements} inventory movement(s)"
    doc_refs = await _variant_document_refs(db, variant_id)
    if doc_refs:
        return True, doc_refs
    inv = (await db.execute(
        select(Inventory).where(Inventory.variant_id == variant_id)
    )).scalar_one_or_none()
    if inv is not None and inv.quantity > 0:
        return True, f"stock on hand {inv.quantity}"
    return False, ""


async def _delete_variant_row(
    db: DbSession, shop_id: UUID, variant: ProductVariant, *, force: bool
) -> None:
    """Delete one variant row, optionally wiping its leftover stock ledger.

    With `force=False` any history blocks the delete. With `force=True` the
    variant's inventory row + movements are removed with it (they cascade off
    the variant row at the database level) - but sale/purchase line references
    still block, because those would corrupt live documents. Callers deleting
    a whole product/category reuse this so every variant gets the same guard.
    """

    if not force:
        has_history, detail = await _variant_has_history(db, shop_id, variant.id)
        if has_history:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot delete variant with history ({detail}). "
                    "Deactivate it instead (is_active=false) so past sales keep their audit trail, "
                    "or retry with ?force=true to permanently wipe its stock history."
                ),
            )
    else:
        doc_refs = await _variant_document_refs(db, variant.id)
        if doc_refs:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot delete variant referenced by documents ({doc_refs}). "
                    "Void the sales/purchases first, then delete."
                ),
            )
    await db.delete(variant)
    await db.flush()


async def _delete_product_row(
    db: DbSession, shop_id: UUID, product: Product, *, force: bool
) -> None:
    """Delete a product and its variants, with the same force semantics."""

    variant_ids = (await db.execute(
        select(ProductVariant.id).where(ProductVariant.product_id == product.id)
    )).scalars().all()
    if not force:
        for variant_id in variant_ids:
            has_history, detail = await _variant_has_history(db, shop_id, variant_id)
            if has_history:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot delete product with variant history ({detail}). "
                        "Delete or deactivate the variants' history first, or retry "
                        "with ?force=true to permanently wipe its stock history."
                    ),
                )
    else:
        for variant_id in variant_ids:
            doc_refs = await _variant_document_refs(db, variant_id)
            if doc_refs:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot delete product referenced by documents ({doc_refs}). "
                        "Void the sales/purchases first, then delete."
                    ),
                )
    await db.delete(product)
    await db.flush()


@router.delete("/variants/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variant(
    variant_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    force: bool = Query(default=False, description="Permanently wipe leftover stock history too."),
) -> None:
    variant = await db.get(ProductVariant, variant_id)
    if variant is None or variant.shop_id != shop_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")
    await _delete_variant_row(db, shop_id, variant, force=force)
    await db.commit()


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
    stmt = (
        select(Product)
        .options(
            selectinload(Product.variants),
            selectinload(Product.category),
        )
        .where(Product.shop_id == shop_id)
    )
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
    stmt = (
        select(Product)
        .options(
            selectinload(Product.variants),
            selectinload(Product.category),
            selectinload(Product.brand),
        )
        .where(Product.id == product_id, Product.shop_id == shop_id)
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if product is None:
        raise _not_found(f"Product {product_id} not found")
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

    stmt = (
        select(Product)
        .options(
            selectinload(Product.variants),
            selectinload(Product.category),
        )
        .where(Product.id == product.id)
    )
    product_with_relations = (await db.execute(stmt)).scalar_one()
    return ProductResponse.model_validate(product_with_relations)


@router.patch("/{product_id}", response_model=ProductDetailResponse)
async def update_product(
    product_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    body: UpdateProductRequest,
) -> ProductDetailResponse:
    stmt = (
        select(Product)
        .options(
            selectinload(Product.variants),
            selectinload(Product.category),
            selectinload(Product.brand),
        )
        .where(Product.id == product_id, Product.shop_id == shop_id)
    )
    product = (await db.execute(stmt)).scalar_one_or_none()
    if product is None:
        raise _not_found(f"Product {product_id} not found")
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        name = fields["name"].strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Product name must not be blank",
            )
        product.name = name
    if "code" in fields:
        code = fields["code"]
        if code is not None and code.strip() == "":
            code = None
        if code is not None:
            clash = (await db.execute(
                select(Product.id).where(
                    Product.shop_id == shop_id,
                    Product.code == code,
                    Product.id != product_id,
                ).limit(1)
            )).scalar_one_or_none()
            if clash is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Product code {code!r} is already used in this shop",
                )
        product.code = code
    if "product_type" in fields and fields["product_type"] is not None:
        try:
            product.product_type = ProductType(fields["product_type"])
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown product_type {fields['product_type']!r}",
            )
    if "description" in fields:
        product.description = fields["description"]
    if "category_id" in fields and fields["category_id"] is not None:
        category = (await db.execute(
            select(Category).where(
                Category.id == fields["category_id"], Category.shop_id == shop_id
            )
        )).scalar_one_or_none()
        if category is None:
            raise _not_found(f"Category {fields['category_id']} not found")
        product.category_id = fields["category_id"]
    if "brand_id" in fields:
        new_brand = fields["brand_id"]
        if new_brand is not None:
            brand = (await db.execute(
                select(Brand).where(Brand.id == new_brand, Brand.shop_id == shop_id)
            )).scalar_one_or_none()
            if brand is None:
                raise _not_found(f"Brand {new_brand} not found")
        product.brand_id = new_brand
    if "unit" in fields and fields["unit"] is not None:
        product.unit = fields["unit"]
    await db.flush()
    await db.commit()
    stmt = (
        select(Product)
        .options(
            selectinload(Product.variants),
            selectinload(Product.category),
            selectinload(Product.brand),
        )
        .where(Product.id == product_id)
    )
    updated = (await db.execute(stmt)).scalar_one()
    return ProductDetailResponse.model_validate(updated)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID,
    shop_id: ShopId,
    db: DbSession,
    force: bool = Query(default=False, description="Permanently wipe leftover stock history too."),
) -> None:
    product = (await db.execute(
        select(Product).where(Product.id == product_id, Product.shop_id == shop_id)
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    await _delete_product_row(db, shop_id, product, force=force)
    await db.commit()
