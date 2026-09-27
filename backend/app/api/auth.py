"""Authentication endpoints.

Only `GET /auth/me` is exposed in V1. All other Clerk integration
(sign-in, sign-up, sign-out) happens on the React frontend via the
Clerk SDK. The backend only verifies tokens and returns application
identity.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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

    Extracts `sub` from the verified Clerk token. If the user already exists
    (matched by `clerk_user_id`), returns their current identity (idempotent).

    Otherwise provisions a brand-new isolated tenant: a fresh `Shop` + an
    `OWNER` `User` bound to it, then initializes system accounts.

    A `clerk_user_id` must NEVER be attached to another user's `Shop` --
    reusing `SELECT Shop LIMIT 1` caused every account to see the same data.
    Each new Clerk identity gets its own Shop.

    If the requested `email` is already registered to a *different*
    `clerk_user_id`, returns 409 so the frontend can tell the user the
    account already exists and to sign in instead.
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

    if user_obj is not None:
        return {
            "id": str(user_obj.id),
            "clerk_user_id": user_obj.clerk_user_id,
            "shop_id": str(user_obj.shop_id),
            "role": user_obj.role.value if user_obj.role else None,
        }

    raw_name = (body.name if body and body.name else None) or ""
    raw_name = raw_name.strip()
    raw_email = (body.email if body and body.email else None) or ""
    raw_email = raw_email.strip().lower()
    raw_shop_name = (body.shop_name if body and body.shop_name else None) or ""
    raw_shop_name = raw_shop_name.strip()

    # Resolve a usable email. Never fall back to a shared static address
    # (that would instantly collide on the uq_users_email constraint for
    # the second user). A per-Clerk-ID placeholder keeps the NOT NULL /
    # UNIQUE invariants without ever matching a real account.
    email = raw_email or f"{clerk_user_id}@kapraos.local"
    name = raw_name or email.split("@")[0] or "Store Owner"

    # Duplicate-account guard: same email, different Clerk identity.
    # Clerk itself normally blocks re-registration, but a second Clerk
    # identity can still arrive here (re-created Clerk user, OAuth vs
    # password, etc.). Fail loudly instead of silently provisioning a
    # second tenant for the same human.
    existing_email = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing_email is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists for this email. Kindly sign in instead.",
        )

    # Every genuinely new identity gets its own isolated Shop.
    shop_name = raw_shop_name or (f"{name}'s Shop" if name != "Store Owner" else "My Shop")
    shop = Shop(
        name=shop_name[:150],
        currency="PKR",
    )
    db.add(shop)
    await db.flush()

    await ensure_system_accounts(db, shop_id=shop.id)

    user_obj = User(
        shop_id=shop.id,
        clerk_user_id=clerk_user_id,
        name=name[:150],
        email=email,
        role=UserRole.OWNER,
    )
    db.add(user_obj)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Race: two concurrent syncs for the same clerk_user_id/email.
        # Re-read; if the row now exists, return it (idempotent).
        retry = (
            await db.execute(
                select(User).where(User.clerk_user_id == clerk_user_id)
            )
        ).scalar_one_or_none()
        if retry is not None:
            return {
                "id": str(retry.id),
                "clerk_user_id": retry.clerk_user_id,
                "shop_id": str(retry.shop_id),
                "role": retry.role.value if retry.role else None,
            }
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists for this email. Kindly sign in instead.",
        )
    await db.refresh(user_obj)

    return {
        "id": str(user_obj.id),
        "clerk_user_id": user_obj.clerk_user_id,
        "shop_id": str(user_obj.shop_id),
        "role": user_obj.role.value if user_obj.role else None,
    }