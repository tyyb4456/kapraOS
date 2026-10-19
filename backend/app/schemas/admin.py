"""Response schemas for the Super Admin Panel.

These models are the HTTP wire contract for `app.api.admin` only. They
mirror the dataclasses in `app.services.admin` and deliberately expose the
minimum: no password material, no tokens, no billing data (none exists),
and no shop-level business records beyond aggregate counts.
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.user import UserRole


class DailyCountResponse(BaseModel):
    """One day in an activity trend."""

    model_config = ConfigDict(from_attributes=True)

    day: date
    count: int


class UnavailableMetricResponse(BaseModel):
    """A metric that cannot be computed with the current schema."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    reason: str


class AdminOverviewResponse(BaseModel):
    """Platform-wide KPI snapshot."""

    model_config = ConfigDict(from_attributes=True)

    generated_at: datetime
    total_users: int
    total_owners: int
    total_staff: int
    platform_admin_count: int
    total_shops: int
    new_users_last_7_days: int
    new_users_last_30_days: int
    new_shops_last_7_days: int
    new_shops_last_30_days: int
    total_sales: int
    sales_last_30_days: int
    total_purchases: int
    purchases_last_30_days: int
    total_payments: int
    payments_last_30_days: int
    total_customers: int
    total_suppliers: int
    total_products: int
    user_registrations_by_day: list[DailyCountResponse]
    shop_creations_by_day: list[DailyCountResponse]
    unavailable_metrics: list[UnavailableMetricResponse]


class AdminHealthCheckResponse(BaseModel):
    """One dependency health probe."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    status: str
    detail: str | None = None
    latency_ms: float | None = None


class AdminHealthResponse(BaseModel):
    """Overall platform health plus its individual checks."""

    model_config = ConfigDict(from_attributes=True)

    status: str
    checked_at: datetime
    checks: list[AdminHealthCheckResponse]


class AdminUserItem(BaseModel):
    """One user row in the platform user index."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    clerk_user_id: str | None = None
    role: UserRole
    is_platform_admin: bool
    shop_id: uuid.UUID
    shop_name: str
    created_at: datetime
    updated_at: datetime


class AdminUserListResponse(BaseModel):
    """Paginated platform users."""

    model_config = ConfigDict(from_attributes=True)

    users: list[AdminUserItem]
    total: int
    limit: int
    offset: int


class AdminUserShopSummary(BaseModel):
    """Minimal shop identity attached to a user detail."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    currency: str
    created_at: datetime


class AdminUserDetailResponse(BaseModel):
    """One user plus their (single) shop relationship."""

    model_config = ConfigDict(from_attributes=True)

    user: AdminUserItem
    shop: AdminUserShopSummary
    shop_members_count: int


class AdminShopOwnerResponse(BaseModel):
    """The OWNER of a shop."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str


class AdminShopItem(BaseModel):
    """One shop row in the platform shop index."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone: str | None = None
    address: str | None = None
    currency: str
    created_at: datetime
    owner: AdminShopOwnerResponse | None = None
    user_count: int
    sales_count: int
    purchases_count: int
    products_count: int


class AdminShopListResponse(BaseModel):
    """Paginated platform shops."""

    model_config = ConfigDict(from_attributes=True)

    shops: list[AdminShopItem]
    total: int
    limit: int
    offset: int


class AdminShopMemberResponse(BaseModel):
    """One member of a shop (safe fields only)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    role: UserRole
    is_platform_admin: bool
    created_at: datetime


class AdminShopDetailResponse(BaseModel):
    """Shop detail: metadata, members, aggregate counts."""

    model_config = ConfigDict(from_attributes=True)

    shop: AdminShopItem
    members: list[AdminShopMemberResponse]
    customers_count: int
    suppliers_count: int
    expenses_count: int
    last_sale_at: datetime | None = None
