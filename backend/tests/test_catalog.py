"""Catalog domain tests: Category, Brand, Attribute/AttributeValue, Product,
ProductVariant, VariantAttributeValue, and the tenant-isolation guarantees
their composite foreign keys are meant to provide.
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Attribute,
    AttributeValue,
    Brand,
    Category,
    Product,
    ProductType,
    ProductVariant,
    Shop,
    Unit,
    VariantAttributeValue,
)


async def _make_shop(db_session: AsyncSession, name: str = "Ahmed Fabrics") -> Shop:
    shop = Shop(name=name)
    db_session.add(shop)
    await db_session.flush()
    return shop


# --------------------------------------------------------------------------
# Category
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_category_can_be_created(db_session: AsyncSession) -> None:
    shop = await _make_shop(db_session)

    women = Category(shop_id=shop.id, name="Women")
    db_session.add(women)
    await db_session.flush()

    assert women.parent_id is None
    assert women.shop_id == shop.id


@pytest.mark.asyncio
async def test_child_category_links_to_parent(db_session: AsyncSession) -> None:
    shop = await _make_shop(db_session)

    women = Category(shop_id=shop.id, name="Women")
    db_session.add(women)
    await db_session.flush()

    open_fabric = Category(shop_id=shop.id, name="Open Fabric", parent_id=women.id)
    db_session.add(open_fabric)
    await db_session.flush()
    await db_session.refresh(open_fabric, attribute_names=["parent"])
    await db_session.refresh(women, attribute_names=["children"])

    assert open_fabric.parent_id == women.id
    assert open_fabric.parent.name == "Women"
    assert [c.id for c in women.children] == [open_fabric.id]


@pytest.mark.asyncio
async def test_sibling_category_names_must_be_unique(db_session: AsyncSession) -> None:
    shop = await _make_shop(db_session)

    women = Category(shop_id=shop.id, name="Women")
    db_session.add(women)
    await db_session.flush()

    db_session.add(Category(shop_id=shop.id, name="Lawn", parent_id=women.id))
    await db_session.flush()

    db_session.add(Category(shop_id=shop.id, name="Lawn", parent_id=women.id))
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_two_shops_can_each_have_a_root_category_with_the_same_name(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, "Shop A")
    shop_b = await _make_shop(db_session, "Shop B")

    db_session.add(Category(shop_id=shop_a.id, name="Lawn"))
    db_session.add(Category(shop_id=shop_b.id, name="Lawn"))
    # Should not raise: uniqueness is scoped per shop.
    await db_session.flush()


# --------------------------------------------------------------------------
# Brand
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brand_can_be_created_and_attached_to_a_product(
    db_session: AsyncSession,
) -> None:
    shop = await _make_shop(db_session)
    category = Category(shop_id=shop.id, name="Ready Suits")
    brand = Brand(shop_id=shop.id, name="Sapphire")
    db_session.add_all([category, brand])
    await db_session.flush()

    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        brand_id=brand.id,
        name="Embroidered Lawn Suit",
        product_type=ProductType.READY_SUIT,
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product, attribute_names=["brand"])

    assert product.brand_id == brand.id
    assert product.brand.name == "Sapphire"


@pytest.mark.asyncio
async def test_unbranded_product_uses_null_brand_id(db_session: AsyncSession) -> None:
    """Required test: unbranded products must work with brand_id = NULL."""

    shop = await _make_shop(db_session)
    category = Category(shop_id=shop.id, name="Boutique")
    db_session.add(category)
    await db_session.flush()

    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        brand_id=None,
        name="Local Boutique Suit",
        product_type=ProductType.BOUTIQUE,
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)

    assert product.brand_id is None
    assert product.name == "Local Boutique Suit"


# --------------------------------------------------------------------------
# Attribute / AttributeValue
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attribute_with_multiple_values(db_session: AsyncSession) -> None:
    shop = await _make_shop(db_session)
    fabric = Attribute(shop_id=shop.id, name="Fabric")
    db_session.add(fabric)
    await db_session.flush()

    db_session.add_all(
        [
            AttributeValue(attribute_id=fabric.id, value="Lawn"),
            AttributeValue(attribute_id=fabric.id, value="Linen"),
            AttributeValue(attribute_id=fabric.id, value="Cotton"),
        ]
    )
    await db_session.flush()
    await db_session.refresh(fabric, attribute_names=["values"])

    assert {v.value for v in fabric.values} == {"Lawn", "Linen", "Cotton"}


# --------------------------------------------------------------------------
# Product / ProductVariant
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_product_belongs_to_shop_and_category(db_session: AsyncSession) -> None:
    shop = await _make_shop(db_session)
    category = Category(shop_id=shop.id, name="Open Fabric")
    db_session.add(category)
    await db_session.flush()

    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Premium Linen",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product, attribute_names=["shop", "category"])

    assert product.shop.id == shop.id
    assert product.category.name == "Open Fabric"


@pytest.mark.asyncio
async def test_product_variant_belongs_to_product(db_session: AsyncSession) -> None:
    shop = await _make_shop(db_session)
    category = Category(shop_id=shop.id, name="Open Fabric")
    db_session.add(category)
    await db_session.flush()

    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Premium Linen",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku="LINEN-WHT-001",
        purchase_price=Decimal("420.00"),
        selling_price=Decimal("650.00"),
        unit=Unit.METER,
    )
    db_session.add(variant)
    await db_session.flush()
    await db_session.refresh(variant, attribute_names=["product"])

    assert variant.product.id == product.id
    assert variant.is_active is True
    assert variant.selling_price == Decimal("650.00")


@pytest.mark.asyncio
async def test_variant_sku_must_be_unique_per_shop(db_session: AsyncSession) -> None:
    shop = await _make_shop(db_session)
    category = Category(shop_id=shop.id, name="Open Fabric")
    db_session.add(category)
    await db_session.flush()

    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Premium Linen",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(product)
    await db_session.flush()

    db_session.add(
        ProductVariant(
            shop_id=shop.id,
            product_id=product.id,
            sku="LINEN-001",
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            unit=Unit.METER,
        )
    )
    await db_session.flush()

    db_session.add(
        ProductVariant(
            shop_id=shop.id,
            product_id=product.id,
            sku="LINEN-001",
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            unit=Unit.METER,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


# --------------------------------------------------------------------------
# VariantAttributeValue
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_variant_can_be_tagged_with_attribute_values(
    db_session: AsyncSession,
) -> None:
    shop = await _make_shop(db_session)
    category = Category(shop_id=shop.id, name="Ready Suits")
    db_session.add(category)
    await db_session.flush()

    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Embroidered Lawn Suit",
        product_type=ProductType.READY_SUIT,
    )
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku="SAP-LAWN-001",
        purchase_price=Decimal("3000.00"),
        selling_price=Decimal("4500.00"),
        unit=Unit.SET,
    )
    db_session.add(variant)
    await db_session.flush()

    fabric = Attribute(shop_id=shop.id, name="Fabric")
    color = Attribute(shop_id=shop.id, name="Color")
    pieces = Attribute(shop_id=shop.id, name="Pieces")
    db_session.add_all([fabric, color, pieces])
    await db_session.flush()

    lawn = AttributeValue(attribute_id=fabric.id, value="Lawn")
    black = AttributeValue(attribute_id=color.id, value="Black")
    two_piece = AttributeValue(attribute_id=pieces.id, value="2 Piece")
    db_session.add_all([lawn, black, two_piece])
    await db_session.flush()

    db_session.add_all(
        [
            VariantAttributeValue(
                variant_id=variant.id,
                attribute_id=fabric.id,
                attribute_value_id=lawn.id,
            ),
            VariantAttributeValue(
                variant_id=variant.id,
                attribute_id=color.id,
                attribute_value_id=black.id,
            ),
            VariantAttributeValue(
                variant_id=variant.id,
                attribute_id=pieces.id,
                attribute_value_id=two_piece.id,
            ),
        ]
    )
    await db_session.flush()

    result = await db_session.execute(
        sa.select(VariantAttributeValue)
        .where(VariantAttributeValue.variant_id == variant.id)
        .options(
            sa.orm.selectinload(VariantAttributeValue.attribute),
            sa.orm.selectinload(VariantAttributeValue.attribute_value),
        )
    )
    tagged = {vav.attribute.name: vav.attribute_value.value for vav in result.scalars()}

    assert tagged == {"Fabric": "Lawn", "Color": "Black", "Pieces": "2 Piece"}


@pytest.mark.asyncio
async def test_attribute_value_from_wrong_attribute_is_rejected(
    db_session: AsyncSession,
) -> None:
    """The DB itself must reject pairing a value with a mismatched attribute."""

    shop = await _make_shop(db_session)
    category = Category(shop_id=shop.id, name="Ready Suits")
    db_session.add(category)
    await db_session.flush()

    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Embroidered Lawn Suit",
        product_type=ProductType.READY_SUIT,
    )
    db_session.add(product)
    await db_session.flush()

    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku="SAP-LAWN-002",
        purchase_price=Decimal("3000.00"),
        selling_price=Decimal("4500.00"),
        unit=Unit.SET,
    )
    db_session.add(variant)
    await db_session.flush()

    fabric = Attribute(shop_id=shop.id, name="Fabric")
    color = Attribute(shop_id=shop.id, name="Color")
    db_session.add_all([fabric, color])
    await db_session.flush()

    # "Black" belongs to the Color attribute, not Fabric.
    black = AttributeValue(attribute_id=color.id, value="Black")
    db_session.add(black)
    await db_session.flush()

    db_session.add(
        VariantAttributeValue(
            variant_id=variant.id,
            attribute_id=fabric.id,
            attribute_value_id=black.id,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


# --------------------------------------------------------------------------
# Tenant isolation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_entities_are_scoped_to_their_own_shop(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, "Shop A")
    shop_b = await _make_shop(db_session, "Shop B")

    category_a = Category(shop_id=shop_a.id, name="Lawn")
    category_b = Category(shop_id=shop_b.id, name="Lawn")
    brand_a = Brand(shop_id=shop_a.id, name="Sapphire")
    db_session.add_all([category_a, category_b, brand_a])
    await db_session.flush()

    product_a = Product(
        shop_id=shop_a.id,
        category_id=category_a.id,
        brand_id=brand_a.id,
        name="Shop A Suit",
        product_type=ProductType.READY_SUIT,
    )
    db_session.add(product_a)
    await db_session.flush()

    result = await db_session.execute(
        sa.select(Product).where(Product.shop_id == shop_a.id)
    )
    shop_a_products = result.scalars().all()

    result = await db_session.execute(
        sa.select(Product).where(Product.shop_id == shop_b.id)
    )
    shop_b_products = result.scalars().all()

    assert [p.id for p in shop_a_products] == [product_a.id]
    assert shop_b_products == []


@pytest.mark.asyncio
async def test_product_cannot_reference_a_category_from_another_shop(
    db_session: AsyncSession,
) -> None:
    """A product from Shop A must not reference Shop B's category."""

    shop_a = await _make_shop(db_session, "Shop A")
    shop_b = await _make_shop(db_session, "Shop B")

    category_b = Category(shop_id=shop_b.id, name="Boutique")
    db_session.add(category_b)
    await db_session.flush()

    db_session.add(
        Product(
            shop_id=shop_a.id,
            category_id=category_b.id,  # belongs to shop_b, not shop_a
            name="Cross-tenant Product",
            product_type=ProductType.BOUTIQUE,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_product_cannot_reference_a_brand_from_another_shop(
    db_session: AsyncSession,
) -> None:
    """A product from Shop A must not reference Shop B's brand."""

    shop_a = await _make_shop(db_session, "Shop A")
    shop_b = await _make_shop(db_session, "Shop B")

    category_a = Category(shop_id=shop_a.id, name="Ready Suits")
    brand_b = Brand(shop_id=shop_b.id, name="Gul Ahmed")
    db_session.add_all([category_a, brand_b])
    await db_session.flush()

    db_session.add(
        Product(
            shop_id=shop_a.id,
            category_id=category_a.id,
            brand_id=brand_b.id,  # belongs to shop_b, not shop_a
            name="Cross-tenant Branded Product",
            product_type=ProductType.READY_SUIT,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_product_variant_cannot_reference_a_product_from_another_shop(
    db_session: AsyncSession,
) -> None:
    shop_a = await _make_shop(db_session, "Shop A")
    shop_b = await _make_shop(db_session, "Shop B")

    category_a = Category(shop_id=shop_a.id, name="Open Fabric")
    db_session.add(category_a)
    await db_session.flush()

    product_a = Product(
        shop_id=shop_a.id,
        category_id=category_a.id,
        name="Premium Linen",
        product_type=ProductType.OPEN_FABRIC,
    )
    db_session.add(product_a)
    await db_session.flush()

    db_session.add(
        ProductVariant(
            shop_id=shop_b.id,  # mismatched shop
            product_id=product_a.id,
            sku="CROSS-TENANT-001",
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            unit=Unit.METER,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()