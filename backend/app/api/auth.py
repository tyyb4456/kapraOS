"""Authentication endpoints.

Only `GET /auth/me` is exposed in V1. All other Clerk integration
(sign-in, sign-up, sign-out) happens on the React frontend via the
Clerk SDK. The backend only verifies tokens and returns application
identity.
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import CurrentUserDep, ShopId
from app.auth.auth import require_auth

router = APIRouter(prefix="/auth", tags=["authentication"])


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