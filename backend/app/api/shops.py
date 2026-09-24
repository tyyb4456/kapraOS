"""Shop Settings endpoints - tenant profile and operational defaults.

Two focused routes over `app.services.shops`:

    GET   /shops/me    read the current tenant's shop settings
    PATCH /shops/me    partially update them

The shop is always resolved server-side (see `app.api.dependencies`), so
settings can never leak or write across tenants. Domain errors are
translated here and nowhere else.
"""

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import DbSession, ShopId
from app.schemas.shops import ShopResponse, UpdateShopRequest
from app.services import shops as shops_service
from app.services.shops import InvalidShopSettingsError, ShopNotFoundError

router = APIRouter(prefix="/shops", tags=["shops"])


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.get(
    "/me",
    response_model=ShopResponse,
    summary="Read shop settings",
)
async def read_shop(
    shop_id: ShopId,
    db: DbSession,
) -> ShopResponse:
    try:
        shop = await shops_service.get_shop(db, shop_id=shop_id)
    except ShopNotFoundError as exc:
        raise _not_found(exc) from exc
    return ShopResponse.model_validate(shop)


@router.patch(
    "/me",
    response_model=ShopResponse,
    summary="Update shop settings",
)
async def update_shop(
    shop_id: ShopId,
    db: DbSession,
    body: UpdateShopRequest,
) -> ShopResponse:
    try:
        shop = await shops_service.update_shop(
            db,
            shop_id=shop_id,
            name=body.name,
            phone=body.phone,
            address=body.address,
            currency=body.currency,
            tax_id=body.tax_id,
            default_unit=body.default_unit,
        )
    except ShopNotFoundError as exc:
        raise _not_found(exc) from exc
    except InvalidShopSettingsError as exc:
        raise _unprocessable(exc) from exc
    return ShopResponse.model_validate(shop)
