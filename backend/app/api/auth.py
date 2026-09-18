"""Authentication endpoints.

Only `GET /auth/me` is exposed in V1. All other Clerk integration
(sign-in, sign-up, sign-out) happens on the React frontend via the
Clerk SDK. The backend only verifies tokens and returns application
identity.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import CurrentUserDep, DbSession, ShopId
from app.auth.auth import require_auth
from app.models.shop import Shop
from app.models.user import User, UserRole
from app.services.accounting import ensure_system_accounts

router = APIRouter(prefix="/auth", tags=["authentication"])


class SyncUserRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    shop_name: str | None = None


@router.get("/me")
async def me(
    current_user: CurrentUserDep,
    shop_id: ShopId,
) -> dict:
    """Return the authenticated application user and their shop context.

    The `shop_id` here is server-derived from the Clerk token, never
    client-supplied.
    """
    return {
        "id": str(current_user.id),
        "clerk_user_id": current_user.clerk_user_id,
        "shop_id": str(shop_id),
        "role": current_user.role.value if current_user.role else None,
    }


@router.post("/sync")
async def sync(
    request: Request,
    db: DbSession,
    body: SyncUserRequest | None = None,
) -> dict:
    """Sync or provision the authenticated Clerk user into the application database.

    Extracts `sub` from the verified Clerk token. If the user already exists,
    returns their current identity. If not, provisions a new User (and Shop if none exists)
    and initializes system accounts.
    """
    payload = await require_auth(request)
    clerk_user_id = payload.get("sub")
    if not clerk_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing subject",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    user_obj = result.scalar_one_or_none()

    if user_obj is None:
        shop_result = await db.execute(select(Shop).limit(1))
        shop = shop_result.scalar_one_or_none()
        if not shop:
            shop_name = (body.shop_name if body and body.shop_name else None) or "KapraOS Fabrics & Suiting"
            shop = Shop(
                name=shop_name,
                currency="PKR",
                address="Shop #14, Cloth Market, Faisalabad",
                phone="0300-8765432",
            )
            db.add(shop)
            await db.flush()

        await ensure_system_accounts(db, shop_id=shop.id)

        name = (body.name if body and body.name else None) or "Store Owner"
        email = (body.email if body and body.email else None) or "owner@kapraos.local"

        user_obj = User(
            shop_id=shop.id,
            clerk_user_id=clerk_user_id,
            name=name,
            email=email,
            role=UserRole.OWNER,
        )
        db.add(user_obj)
        await db.commit()
        await db.refresh(user_obj)

    return {
        "id": str(user_obj.id),
        "clerk_user_id": user_obj.clerk_user_id,
        "shop_id": str(user_obj.shop_id),
        "role": user_obj.role.value if user_obj.role else None,
    }