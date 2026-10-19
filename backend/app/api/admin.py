"""Super Admin Panel endpoints - platform-wide, read-only.

Every route is gated by `PlatformAdmin` (`require_platform_admin`), which
resolves the caller's application User from the verified Clerk token and
requires `users.is_platform_admin = true` server-side. No client-supplied
header, body field or query parameter can influence this check.

Routes:

    GET /admin/overview           platform KPIs + 30-day trends
    GET /admin/health             dependency health checks
    GET /admin/users              paginated user index (search/role filter)
    GET /admin/users/{user_id}    one user + shop relationship
    GET /admin/shops              paginated shop index
    GET /admin/shops/{shop_id}    one shop + members + aggregate counts

There are deliberately no mutation endpoints in this phase: the only
platform-admin mutation (granting/revoking the flag) is an operator-run
database script, never an HTTP surface. Domain errors are translated here
and nowhere else.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DbSession, PlatformAdmin
from app.models.user import UserRole
from app.schemas.admin import (
    AdminHealthResponse,
    AdminOverviewResponse,
    AdminShopDetailResponse,
    AdminShopListResponse,
    AdminUserDetailResponse,
    AdminUserListResponse,
)
from app.services import admin as admin_service
from app.services.admin import (
    AdminShopNotFoundError,
    AdminUserNotFoundError,
    InvalidAdminPaginationError,
)

router = APIRouter(prefix="/admin", tags=["admin"])

SearchQuery = Annotated[
    str | None,
    Query(max_length=200, description="Case-insensitive substring match."),
]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.get(
    "/overview",
    response_model=AdminOverviewResponse,
    summary="Platform-wide overview",
)
async def read_platform_overview(
    admin: PlatformAdmin,
    db: DbSession,
) -> AdminOverviewResponse:
    overview = await admin_service.get_platform_overview(db)
    return AdminOverviewResponse.model_validate(overview)


@router.get(
    "/health",
    response_model=AdminHealthResponse,
    summary="Platform dependency health",
)
async def read_platform_health(
    admin: PlatformAdmin,
    db: DbSession,
) -> AdminHealthResponse:
    health = await admin_service.get_platform_health(db)
    return AdminHealthResponse.model_validate(health)


@router.get(
    "/users",
    response_model=AdminUserListResponse,
    summary="List platform users",
)
async def list_platform_users(
    admin: PlatformAdmin,
    db: DbSession,
    search: SearchQuery = None,
    role: UserRole | None = None,
    limit: LimitQuery = 25,
    offset: OffsetQuery = 0,
) -> AdminUserListResponse:
    try:
        page = await admin_service.list_users(
            db, search=search, role=role, limit=limit, offset=offset
        )
    except InvalidAdminPaginationError as exc:
        raise _unprocessable(exc) from exc
    return AdminUserListResponse.model_validate(page)


@router.get(
    "/users/{user_id}",
    response_model=AdminUserDetailResponse,
    summary="Platform user detail",
)
async def read_platform_user(
    user_id: uuid.UUID,
    admin: PlatformAdmin,
    db: DbSession,
) -> AdminUserDetailResponse:
    try:
        detail = await admin_service.get_user_detail(db, user_id=user_id)
    except AdminUserNotFoundError as exc:
        raise _not_found(exc) from exc
    return AdminUserDetailResponse.model_validate(detail)


@router.get(
    "/shops",
    response_model=AdminShopListResponse,
    summary="List platform shops",
)
async def list_platform_shops(
    admin: PlatformAdmin,
    db: DbSession,
    search: SearchQuery = None,
    limit: LimitQuery = 25,
    offset: OffsetQuery = 0,
) -> AdminShopListResponse:
    try:
        page = await admin_service.list_shops(
            db, search=search, limit=limit, offset=offset
        )
    except InvalidAdminPaginationError as exc:
        raise _unprocessable(exc) from exc
    return AdminShopListResponse.model_validate(page)


@router.get(
    "/shops/{shop_id}",
    response_model=AdminShopDetailResponse,
    summary="Platform shop detail",
)
async def read_platform_shop(
    shop_id: uuid.UUID,
    admin: PlatformAdmin,
    db: DbSession,
) -> AdminShopDetailResponse:
    try:
        detail = await admin_service.get_shop_detail(db, shop_id=shop_id)
    except AdminShopNotFoundError as exc:
        raise _not_found(exc) from exc
    return AdminShopDetailResponse.model_validate(detail)
