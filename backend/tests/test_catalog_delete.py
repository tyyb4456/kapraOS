"""Catalog force-delete tests.

Plain deletes protect the audit trail: a product/variant with inventory
movements and a category holding products are rejected with 409. The
`?force=true` escape hatch wipes the leftover stock ledger (movements +
inventory rows cascade off the variant row) so a shopkeeper who already
voided every sale/purchase can fully reset the catalog - but it still
refuses when live sale/purchase documents reference the variant, because
those lines would cascade away and corrupt document totals.
"""

import uuid
from decimal import Decimal
from typing import NamedTuple

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Category,
    Inventory,
    InventoryMovement,
    Product,
    ProductType,
    ProductVariant,
    Shop,
    Supplier,
    Unit,
    User,
)
from app.models.user import UserRole
from app.services.purchases import PurchaseItemInput, create_purchase


class ShopFixture(NamedTuple):
    shop: Shop
    category: Category
    product: Product
    variant: ProductVariant
    supplier: Supplier


async def _make_shop(api_session: AsyncSession) -> ShopFixture:
    shop = Shop(name="Ahmed Fabrics")
    category = Category(shop=shop, name="Open Fabric")
    api_session.add_all([shop, category])
    await api_session.flush()

    product = Product(
        shop_id=shop.id,
        category_id=category.id,
        name="Premium Linen",
        product_type=ProductType.OPEN_FABRIC,
    )
    api_session.add(product)
    await api_session.flush()

    variant = ProductVariant(
        shop_id=shop.id,
        product_id=product.id,
        sku="LINEN-WHT-001",
        purchase_price=Decimal("800.00"),
        selling_price=Decimal("1200.00"),
        unit=Unit.METER,
    )
    supplier = Supplier(shop_id=shop.id, name="Al-Madina Textile")
    user = User(
        clerk_user_id="mock_clerk_id",
        shop_id=shop.id,
        name="Mock User",
        email="mock@example.com",
        role=UserRole.OWNER,
    )
    api_session.add_all([variant, supplier, user])
    await api_session.flush()
    return ShopFixture(
        shop=shop,
        category=category,
        product=product,
        variant=variant,
        supplier=supplier,
    )


async def _purchase(api_session: AsyncSession, fixture: ShopFixture):
    return await create_purchase(
        api_session,
        shop_id=fixture.shop.id,
        supplier_id=fixture.supplier.id,
        items=[
            PurchaseItemInput(
                variant_id=fixture.variant.id,
                quantity=Decimal("10"),
                unit_cost=Decimal("800"),
            )
        ],
    )


async def _movement_count(
    api_session: AsyncSession, variant_id: uuid.UUID
) -> int:
    return (await api_session.execute(
        sa.select(sa.func.count(InventoryMovement.id)).where(
            InventoryMovement.variant_id == variant_id
        )
    )).scalar_one()


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


@pytest.mark.asyncio
async def test_product_plain_delete_blocked_then_force_wipes_movements(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    purchase = await _purchase(api_session, fixture)
    assert await _movement_count(api_session, fixture.variant.id) > 0

    # Void the purchase first (stock goes back out, purchase rows vanish)...
    response = await mocked_api_client.delete(
        f"/purchases/{purchase.id}", headers=_headers()
    )
    assert response.status_code == 204

    # ...but its movement ledger is still there, so a plain delete refuses.
    response = await mocked_api_client.delete(
        f"/products/{fixture.product.id}", headers=_headers()
    )
    assert response.status_code == 409

    response = await mocked_api_client.delete(
        f"/products/{fixture.product.id}?force=true", headers=_headers()
    )
    assert response.status_code == 204

    response = await mocked_api_client.get(
        f"/products/{fixture.product.id}", headers=_headers()
    )
    assert response.status_code == 404
    assert await _movement_count(api_session, fixture.variant.id) == 0
    assert (await api_session.execute(
        sa.select(sa.func.count(Inventory.id)).where(
            Inventory.variant_id == fixture.variant.id
        )
    )).scalar_one() == 0


@pytest.mark.asyncio
async def test_product_force_delete_blocked_by_live_purchase(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    await _purchase(api_session, fixture)

    response = await mocked_api_client.delete(
        f"/products/{fixture.product.id}?force=true", headers=_headers()
    )
    assert response.status_code == 409
    assert "purchase" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_category_force_delete_removes_products_and_history(
    mocked_api_client: AsyncClient, api_session: AsyncSession
) -> None:
    fixture = await _make_shop(api_session)
    purchase = await _purchase(api_session, fixture)
    response = await mocked_api_client.delete(
        f"/purchases/{purchase.id}", headers=_headers()
    )
    assert response.status_code == 204

    response = await mocked_api_client.delete(
        f"/products/categories/{fixture.category.id}", headers=_headers()
    )
    assert response.status_code == 409

    response = await mocked_api_client.delete(
        f"/products/categories/{fixture.category.id}?force=true",
        headers=_headers(),
    )
    assert response.status_code == 204

    categories = (
        await mocked_api_client.get("/products/categories", headers=_headers())
    ).json()
    assert all(c["id"] != str(fixture.category.id) for c in categories)
    products = (
        await mocked_api_client.get("/products", headers=_headers())
    ).json()
    assert all(p["id"] != str(fixture.product.id) for p in products)
