"""Shop Settings service - tenant profile and operational defaults.

Small on purpose: the shop is always resolved server-side from the auth
token, so these helpers take an explicit `shop_id` and can never cross
tenant boundaries. All validation of *values* lives here so the API layer
stays a thin translation shim.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shop import Shop


class ShopNotFoundError(Exception):
    """No shop row for the authenticated tenant."""


class InvalidShopSettingsError(Exception):
    """A settings value fails domain validation (blank name, bad unit...)."""


_ALLOWED_UNITS = frozenset({"meters", "yards", "pieces"})
_ALLOWED_CURRENCIES = frozenset({"PKR"})


async def get_shop(session: AsyncSession, *, shop_id: uuid.UUID) -> Shop:
    """Return the tenant's shop row or raise `ShopNotFoundError`."""
    shop = (
        await session.execute(select(Shop).where(Shop.id == shop_id))
    ).scalar_one_or_none()
    if shop is None:
        raise ShopNotFoundError(f"Shop {shop_id} not found")
    return shop


async def update_shop(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    name: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    currency: str | None = None,
    tax_id: str | None = None,
    default_unit: str | None = None,
) -> Shop:
    """Apply a partial settings update and return the refreshed shop.

    `None` means "leave unchanged", except for the explicitly nullable
    fields (`phone`, `address`, `tax_id`) where an empty/blank string clears
    the value to `NULL`. Commits the transaction.
    """
    shop = await get_shop(session, shop_id=shop_id)

    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise InvalidShopSettingsError("Shop name must not be blank")
        shop.name = cleaned

    if phone is not None:
        shop.phone = phone.strip() or None

    if address is not None:
        shop.address = address.strip() or None

    if currency is not None:
        code = currency.strip().upper()
        if code not in _ALLOWED_CURRENCIES:
            raise InvalidShopSettingsError(
                f"Unsupported currency {currency!r}: only PKR is supported in V1"
            )
        shop.currency = code

    if tax_id is not None:
        shop.tax_id = tax_id.strip() or None

    if default_unit is not None:
        unit = default_unit.strip().lower()
        if unit not in _ALLOWED_UNITS:
            raise InvalidShopSettingsError(
                f"Unsupported fabric unit {default_unit!r}: "
                "expected meters, yards or pieces"
            )
        shop.default_unit = unit

    await session.commit()
    await session.refresh(shop)
    return shop
