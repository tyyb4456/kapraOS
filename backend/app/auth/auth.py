"""Clerk authentication for FastAPI.

The `require_auth` dependency verifies the Clerk session token on every
request using `clerk-backend-api` (networkless verification via
`CLERK_JWT_KEY`, falling back to `CLERK_SECRET_KEY` + JWKS fetch).

From the verified token payload we extract `sub` (the Clerk user ID),
resolve the application `User`, and derive `shop_id` server-side.

No custom authentication is implemented here. Clerk owns identity;
the backend only verifies and maps.
"""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import User
from app.models.user import UserRole


async def _verify_token(request: Request) -> dict | None:
    """Verify the Clerk session token and return the payload."""
    settings = get_settings()

    if not settings.clerk_secret_key:
        return None

    from clerk_backend_api import AuthenticateRequestOptions, authenticate_request

    try:
        state = authenticate_request(
            request,
            AuthenticateRequestOptions(
                secret_key=settings.clerk_secret_key,
                jwt_key=settings.clerk_jwt_key or None,
                authorized_parties=settings.clerk_authorized_parties_list,
                accepts_token=["session_token"],
            ),
        )
    except Exception:
        return None

    if not state.is_signed_in:
        return None

    return state.payload


async def require_auth(request: Request) -> dict:
    """Verify the Clerk token and return the full payload.

    Raises 401 for missing, invalid, or expired tokens.
    """
    payload = await _verify_token(request)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Resolve the authenticated application User from the Clerk token.

    Extracts `sub` from the verified Clerk payload, looks up the
    application User by `clerk_user_id`, and returns it.

    Raises 401 if the token is invalid or the user is not provisioned.
    Raises 403 if the user has no shop association.
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not provisioned",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user_obj.shop_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no shop association",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_obj


async def get_current_user_id(
    current_user: Annotated[User, Depends(get_current_user)],
) -> uuid.UUID:
    """Return the authenticated user's application ID."""
    return current_user.id


async def get_current_shop_id(
    current_user: Annotated[User, Depends(get_current_user)],
) -> uuid.UUID:
    """Return the authenticated user's shop ID.

    This is the **only** legitimate source of `shop_id` for tenant-scoped
    routes. Client-provided `X-Shop-Id`, query params, or body fields are
    ignored entirely.
    """
    return current_user.shop_id


async def require_owner(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require the authenticated user to be an OWNER.

    Raises 403 if the user is a STAFF member.
    """
    if current_user.role != UserRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required",
        )
    return current_user


ShopId = Annotated[uuid.UUID, Depends(get_current_shop_id)]
CurrentUser = Annotated[User, Depends(get_current_user)]