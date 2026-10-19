"""Platform-admin service - read-only, platform-wide operations.

This module powers the Super Admin Panel. It is deliberately split from
every shop-scoped service: queries here intentionally aggregate across
*all* tenants, so the only caller is `app.api.admin`, whose routes require
the `require_platform_admin` dependency. Nothing in this module takes a
`shop_id` from a client, and nothing writes tenant data.

Design rules:
    * Aggregation happens in PostgreSQL (`COUNT`, `GROUP BY`,
      `date_trunc`) - never by loading rows into Python.
    * All date windows are computed from timezone-aware UTC boundaries
      (`datetime.now(timezone.utc)`), so "last 7/30 days" means the same
      thing regardless of server locale.
    * Missing optional capabilities (account status, billing) are reported
      explicitly via `unavailable_metrics` instead of being fabricated as
      zeroes.
    * User/shop listings are paginated since only a page of rows is ever
      materialised.

The only mutating operations are the platform-admin flag grant/revoke
helpers used by the operator-run `app.scripts.grant_platform_admin`
bootstrap; every call appends an `AdminAuditLog` row in the same
transaction.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from time import perf_counter

from sqlalchemy import Select, func, or_, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import is_redis_configured, ping_redis
from app.models.admin_audit import AdminAuditLog
from app.models.customer import Customer
from app.models.expense import Expense
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase import Purchase
from app.models.sale import Sale
from app.models.shop import Shop
from app.models.supplier import Supplier
from app.models.user import User, UserRole

# Calendar-day window (including today) used for the activity trend and the
# "last 30 days" counters, so both always describe the same period.
TREND_DAYS = 30

# The account model has no status column, so these are explicitly reported
# as unavailable rather than rendered as zero.
_UNAVAILABLE_METRICS = (
    (
        "active_inactive_users",
        (
            "The account model has no active/inactive status; every "
            "registered user is active."
        ),
    ),
    (
        "subscriptions_billing",
        (
            "KapraOS has no subscription or billing system, so no platform "
            "revenue/subscription metrics exist."
        ),
    ),
)


class AdminServiceError(Exception):
    """Base class for platform-admin domain failures."""


class AdminUserNotFoundError(AdminServiceError):
    """The requested user id does not exist."""


class AdminShopNotFoundError(AdminServiceError):
    """The requested shop id does not exist."""


class PlatformAdminTargetNotFoundError(AdminServiceError):
    """No user matches the given email / Clerk user id."""


class LastPlatformAdminError(AdminServiceError):
    """Refusing to revoke the only remaining platform administrator."""


class InvalidAdminPaginationError(AdminServiceError):
    """`limit` / `offset` are outside their allowed ranges."""


# ---------------------------------------------------------------------------
# Data carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyCount:
    """One calendar day and the number of rows created in it."""

    day: date
    count: int


@dataclass(frozen=True)
class UnavailableMetric:
    """A metric this phase cannot compute, with the reason why."""

    key: str
    reason: str


@dataclass(frozen=True)
class PlatformOverview:
    """Platform-wide KPI snapshot assembled from efficient COUNT queries."""

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
    user_registrations_by_day: tuple[DailyCount, ...]
    shop_creations_by_day: tuple[DailyCount, ...]
    unavailable_metrics: tuple[UnavailableMetric, ...]


@dataclass(frozen=True)
class HealthCheck:
    """One dependency health probe."""

    name: str
    status: str  # ok | unavailable | not_configured | error
    detail: str | None
    latency_ms: float | None


@dataclass(frozen=True)
class PlatformHealth:
    """Overall platform health derived from the individual checks."""

    status: str  # ok | degraded | error
    checked_at: datetime
    checks: tuple[HealthCheck, ...]


@dataclass(frozen=True)
class AdminUserRow:
    """One user row for the platform user index/detail.

    ``shop_id``/``shop_name`` are ``None`` for a platform administrator
    who is not bound to a tenant.
    """

    id: uuid.UUID
    name: str
    email: str
    clerk_user_id: str | None
    role: UserRole
    is_platform_admin: bool
    shop_id: uuid.UUID | None
    shop_name: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AdminUserPage:
    """A page of platform users."""

    users: tuple[AdminUserRow, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class AdminShopSummary:
    """Minimal shop identity used inside user detail responses."""

    id: uuid.UUID
    name: str
    currency: str
    created_at: datetime


@dataclass(frozen=True)
class AdminUserDetail:
    """One user plus the real relationship model (a user -> one shop).

    ``shop`` is ``None`` for a shop-less platform administrator.
    """

    user: AdminUserRow
    shop: AdminShopSummary | None
    shop_members_count: int


@dataclass(frozen=True)
class AdminShopOwner:
    """The OWNER of a shop, if one exists."""

    id: uuid.UUID
    name: str
    email: str


@dataclass(frozen=True)
class AdminShopRow:
    """One shop row with its owner and aggregate activity counts."""

    id: uuid.UUID
    name: str
    phone: str | None
    address: str | None
    currency: str
    created_at: datetime
    owner: AdminShopOwner | None
    user_count: int
    sales_count: int
    purchases_count: int
    products_count: int


@dataclass(frozen=True)
class AdminShopPage:
    """A page of platform shops."""

    shops: tuple[AdminShopRow, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class AdminShopMember:
    """One member of a shop (safe fields only)."""

    id: uuid.UUID
    name: str
    email: str
    role: UserRole
    is_platform_admin: bool
    created_at: datetime


@dataclass(frozen=True)
class AdminShopDetail:
    """A shop with its owner, members and aggregate counts."""

    shop: AdminShopRow
    members: tuple[AdminShopMember, ...]
    customers_count: int
    suppliers_count: int
    expenses_count: int
    last_sale_at: datetime | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _day_start_utc(days_ago: int = 0, *, now: datetime | None = None) -> datetime:
    """UTC midnight `days_ago` days before today (inclusive boundary)."""
    moment = now or _utc_now()
    day = (moment - timedelta(days=days_ago)).date()
    return datetime.combine(day, dt_time.min, tzinfo=UTC)


def _validate_pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > 100:
        raise InvalidAdminPaginationError("limit must be between 1 and 100")
    if offset < 0:
        raise InvalidAdminPaginationError("offset must be >= 0")


def _like_pattern(term: str) -> str:
    """Escape LIKE wildcards so user input matches literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _scalar_count(session: AsyncSession, stmt: Select) -> int:
    return int((await session.execute(stmt)).scalar() or 0)


def _count_stmt(model: type) -> Select:
    return select(func.count(model.id))  # type: ignore[attr-defined]


async def _daily_counts(
    session: AsyncSession,
    model: type,
    *,
    since: datetime,
    until: datetime,
) -> tuple[DailyCount, ...]:
    """Per-day row counts in `[since, until)`, with zero-days filled in.

    The database does the grouping (`date_trunc` + `COUNT`); Python only
    fills calendar gaps so the response shape is stable.
    """
    day_col = func.date_trunc("day", model.created_at)  # type: ignore[attr-defined]
    rows = (
        await session.execute(
            select(day_col, func.count(model.id))  # type: ignore[attr-defined]
            .where(
                model.created_at >= since,  # type: ignore[attr-defined]
                model.created_at < until,  # type: ignore[attr-defined]
            )
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()

    counts: dict[date, int] = {}
    for day_value, count in rows:
        counts[day_value.date() if isinstance(day_value, datetime) else day_value] = (
            int(count)
        )

    filled: list[DailyCount] = []
    cursor = since.date()
    end = until.date()
    while cursor < end:
        filled.append(DailyCount(day=cursor, count=counts.get(cursor, 0)))
        cursor += timedelta(days=1)
    return tuple(filled)


async def _grouped_shop_counts(
    session: AsyncSession,
    model: type,
    shop_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """`{shop_id: count}` for one model, restricted to `shop_ids`."""
    if not shop_ids:
        return {}
    rows = (
        await session.execute(
            select(model.shop_id, func.count(model.id))  # type: ignore[attr-defined]
            .where(model.shop_id.in_(shop_ids))  # type: ignore[attr-defined]
            .group_by(model.shop_id)  # type: ignore[attr-defined]
        )
    ).all()
    return {shop_id: int(count) for shop_id, count in rows}


async def _owners_for_shops(
    session: AsyncSession, shop_ids: list[uuid.UUID]
) -> dict[uuid.UUID, AdminShopOwner]:
    """First OWNER (by creation time) per shop."""
    if not shop_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(User)
                .where(User.shop_id.in_(shop_ids), User.role == UserRole.OWNER)
                .order_by(User.created_at.asc(), User.id.asc())
            )
        )
        .scalars()
        .all()
    )
    owners: dict[uuid.UUID, AdminShopOwner] = {}
    for user in rows:
        if user.shop_id is None:  # pragma: no cover - query is shop-scoped
            continue
        owners.setdefault(
            user.shop_id,
            AdminShopOwner(id=user.id, name=user.name, email=user.email),
        )
    return owners


# ---------------------------------------------------------------------------
# Platform overview
# ---------------------------------------------------------------------------


async def get_platform_overview(session: AsyncSession) -> PlatformOverview:
    """Aggregate platform-wide KPIs and 30-day creation trends."""
    now = _utc_now()
    since_7 = _day_start_utc(6, now=now)
    since_30 = _day_start_utc(TREND_DAYS - 1, now=now)
    tomorrow = _day_start_utc(-1, now=now)

    total_users = await _scalar_count(session, _count_stmt(User))
    total_shops = await _scalar_count(session, _count_stmt(Shop))

    role_rows = (
        await session.execute(
            select(User.role, func.count(User.id)).group_by(User.role)
        )
    ).all()
    role_counts = {role: int(count) for role, count in role_rows}

    platform_admin_count = await _scalar_count(
        session,
        select(func.count(User.id)).where(User.is_platform_admin.is_(True)),
    )

    new_users_7 = await _scalar_count(
        session, select(func.count(User.id)).where(User.created_at >= since_7)
    )
    new_users_30 = await _scalar_count(
        session, select(func.count(User.id)).where(User.created_at >= since_30)
    )
    new_shops_7 = await _scalar_count(
        session, select(func.count(Shop.id)).where(Shop.created_at >= since_7)
    )
    new_shops_30 = await _scalar_count(
        session, select(func.count(Shop.id)).where(Shop.created_at >= since_30)
    )

    total_sales = await _scalar_count(session, _count_stmt(Sale))
    sales_30 = await _scalar_count(
        session, select(func.count(Sale.id)).where(Sale.created_at >= since_30)
    )
    total_purchases = await _scalar_count(session, _count_stmt(Purchase))
    purchases_30 = await _scalar_count(
        session,
        select(func.count(Purchase.id)).where(Purchase.created_at >= since_30),
    )
    total_payments = await _scalar_count(session, _count_stmt(Payment))
    payments_30 = await _scalar_count(
        session,
        select(func.count(Payment.id)).where(Payment.created_at >= since_30),
    )

    total_customers = await _scalar_count(session, _count_stmt(Customer))
    total_suppliers = await _scalar_count(session, _count_stmt(Supplier))
    total_products = await _scalar_count(session, _count_stmt(Product))

    user_trend = await _daily_counts(session, User, since=since_30, until=tomorrow)
    shop_trend = await _daily_counts(session, Shop, since=since_30, until=tomorrow)

    return PlatformOverview(
        generated_at=now,
        total_users=total_users,
        total_owners=role_counts.get(UserRole.OWNER, 0),
        total_staff=role_counts.get(UserRole.STAFF, 0),
        platform_admin_count=platform_admin_count,
        total_shops=total_shops,
        new_users_last_7_days=new_users_7,
        new_users_last_30_days=new_users_30,
        new_shops_last_7_days=new_shops_7,
        new_shops_last_30_days=new_shops_30,
        total_sales=total_sales,
        sales_last_30_days=sales_30,
        total_purchases=total_purchases,
        purchases_last_30_days=purchases_30,
        total_payments=total_payments,
        payments_last_30_days=payments_30,
        total_customers=total_customers,
        total_suppliers=total_suppliers,
        total_products=total_products,
        user_registrations_by_day=user_trend,
        shop_creations_by_day=shop_trend,
        unavailable_metrics=tuple(
            UnavailableMetric(key=key, reason=reason)
            for key, reason in _UNAVAILABLE_METRICS
        ),
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def get_platform_health(session: AsyncSession) -> PlatformHealth:
    """Probe dependencies that expose a meaningful check.

    * `database` - a `SELECT 1` against the same session the request uses.
    * `redis_cache` - PING when `REDIS_URL` is configured; otherwise
      reported as `not_configured` (the in-process fallback cache is a
      deliberate design, not a fault).
    """
    checks: list[HealthCheck] = []

    started = perf_counter()
    try:
        await session.execute(sa_text("SELECT 1"))
        checks.append(
            HealthCheck(
                name="database",
                status="ok",
                detail="PostgreSQL responded to SELECT 1",
                latency_ms=round((perf_counter() - started) * 1000, 2),
            )
        )
    except Exception as exc:  # noqa: BLE001 - health checks must not raise
        checks.append(
            HealthCheck(
                name="database",
                status="error",
                detail=type(exc).__name__,
                latency_ms=None,
            )
        )

    if not is_redis_configured():
        checks.append(
            HealthCheck(
                name="redis_cache",
                status="not_configured",
                detail=(
                    "REDIS_URL is not set; the process-local cache fallback is in use."
                ),
                latency_ms=None,
            )
        )
    else:
        started = perf_counter()
        redis_ok = await ping_redis()
        checks.append(
            HealthCheck(
                name="redis_cache",
                status="ok" if redis_ok else "unavailable",
                detail=(
                    "Redis answered PING"
                    if redis_ok
                    else "Redis did not answer; reads fall back to PostgreSQL."
                ),
                latency_ms=round((perf_counter() - started) * 1000, 2),
            )
        )

    if any(check.status == "error" for check in checks):
        overall = "error"
    elif any(check.status == "unavailable" for check in checks):
        overall = "degraded"
    else:
        overall = "ok"

    return PlatformHealth(status=overall, checked_at=_utc_now(), checks=tuple(checks))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def list_users(
    session: AsyncSession,
    *,
    search: str | None = None,
    role: UserRole | None = None,
    limit: int = 25,
    offset: int = 0,
) -> AdminUserPage:
    """A page of users, optionally searched by name/email or filtered by role."""
    _validate_pagination(limit, offset)

    filters = []
    if search and search.strip():
        pattern = _like_pattern(search.strip())
        filters.append(
            or_(
                User.name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
            )
        )
    if role is not None:
        filters.append(User.role == role)

    total = await _scalar_count(session, select(func.count(User.id)).where(*filters))

    rows = (
        await session.execute(
            select(User, Shop.name)
            # LEFT JOIN: a shop-less platform administrator must still
            # appear in the platform user index (with no shop name).
            .outerjoin(Shop, Shop.id == User.shop_id)
            .where(*filters)
            .order_by(User.created_at.desc(), User.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    users = tuple(
        AdminUserRow(
            id=user.id,
            name=user.name,
            email=user.email,
            clerk_user_id=user.clerk_user_id,
            role=user.role,
            is_platform_admin=bool(user.is_platform_admin),
            shop_id=user.shop_id,
            shop_name=shop_name,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
        for user, shop_name in rows
    )
    return AdminUserPage(users=users, total=total, limit=limit, offset=offset)


async def get_user_detail(
    session: AsyncSession, *, user_id: uuid.UUID
) -> AdminUserDetail:
    """One user plus the shop relationship that actually exists in the model.

    A platform administrator without a tenant has no shop: the detail is
    returned with ``shop=None`` and zero members.
    """
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise AdminUserNotFoundError(f"User {user_id} was not found")

    if user.shop_id is None:
        return AdminUserDetail(
            user=AdminUserRow(
                id=user.id,
                name=user.name,
                email=user.email,
                clerk_user_id=user.clerk_user_id,
                role=user.role,
                is_platform_admin=bool(user.is_platform_admin),
                shop_id=None,
                shop_name=None,
                created_at=user.created_at,
                updated_at=user.updated_at,
            ),
            shop=None,
            shop_members_count=0,
        )

    shop = (
        await session.execute(select(Shop).where(Shop.id == user.shop_id))
    ).scalar_one_or_none()
    if shop is None:
        # A set shop_id is protected by the FK, so this can only mean a
        # concurrently deleted tenant; treat it the same as a missing user.
        raise AdminUserNotFoundError(f"User {user_id} was not found")

    member_count = await _scalar_count(
        session, select(func.count(User.id)).where(User.shop_id == user.shop_id)
    )

    return AdminUserDetail(
        user=AdminUserRow(
            id=user.id,
            name=user.name,
            email=user.email,
            clerk_user_id=user.clerk_user_id,
            role=user.role,
            is_platform_admin=bool(user.is_platform_admin),
            shop_id=user.shop_id,
            shop_name=shop.name,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ),
        shop=AdminShopSummary(
            id=shop.id,
            name=shop.name,
            currency=shop.currency,
            created_at=shop.created_at,
        ),
        shop_members_count=member_count,
    )


# ---------------------------------------------------------------------------
# Shops
# ---------------------------------------------------------------------------


async def list_shops(
    session: AsyncSession,
    *,
    search: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> AdminShopPage:
    """A page of shops with owner and aggregate activity counts."""
    _validate_pagination(limit, offset)

    filters = []
    if search and search.strip():
        pattern = _like_pattern(search.strip())
        filters.append(
            or_(
                Shop.name.ilike(pattern, escape="\\"),
                Shop.phone.ilike(pattern, escape="\\"),
            )
        )

    total = await _scalar_count(session, select(func.count(Shop.id)).where(*filters))

    shops = (
        (
            await session.execute(
                select(Shop)
                .where(*filters)
                .order_by(Shop.created_at.desc(), Shop.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    rows = await _build_shop_rows(session, list(shops))
    return AdminShopPage(shops=rows, total=total, limit=limit, offset=offset)


async def _build_shop_rows(
    session: AsyncSession, shops: list[Shop]
) -> tuple[AdminShopRow, ...]:
    if not shops:
        return ()
    shop_ids = [shop.id for shop in shops]

    user_counts = await _grouped_shop_counts(session, User, shop_ids)
    sales_counts = await _grouped_shop_counts(session, Sale, shop_ids)
    purchase_counts = await _grouped_shop_counts(session, Purchase, shop_ids)
    product_counts = await _grouped_shop_counts(session, Product, shop_ids)
    owners = await _owners_for_shops(session, shop_ids)

    return tuple(
        AdminShopRow(
            id=shop.id,
            name=shop.name,
            phone=shop.phone,
            address=shop.address,
            currency=shop.currency,
            created_at=shop.created_at,
            owner=owners.get(shop.id),
            user_count=user_counts.get(shop.id, 0),
            sales_count=sales_counts.get(shop.id, 0),
            purchases_count=purchase_counts.get(shop.id, 0),
            products_count=product_counts.get(shop.id, 0),
        )
        for shop in shops
    )


async def get_shop_detail(
    session: AsyncSession, *, shop_id: uuid.UUID
) -> AdminShopDetail:
    """One shop with its (safe) member list and aggregate activity."""
    shop = (
        await session.execute(select(Shop).where(Shop.id == shop_id))
    ).scalar_one_or_none()
    if shop is None:
        raise AdminShopNotFoundError(f"Shop {shop_id} was not found")

    (row,) = await _build_shop_rows(session, [shop])

    members = (
        (
            await session.execute(
                select(User)
                .where(User.shop_id == shop.id)
                .order_by(User.created_at.asc(), User.id.asc())
            )
        )
        .scalars()
        .all()
    )

    customers_count = await _scalar_count(
        session, select(func.count(Customer.id)).where(Customer.shop_id == shop.id)
    )
    suppliers_count = await _scalar_count(
        session, select(func.count(Supplier.id)).where(Supplier.shop_id == shop.id)
    )
    expenses_count = await _scalar_count(
        session, select(func.count(Expense.id)).where(Expense.shop_id == shop.id)
    )
    last_sale_at = (
        await session.execute(
            select(func.max(Sale.created_at)).where(Sale.shop_id == shop.id)
        )
    ).scalar()

    return AdminShopDetail(
        shop=row,
        members=tuple(
            AdminShopMember(
                id=member.id,
                name=member.name,
                email=member.email,
                role=member.role,
                is_platform_admin=bool(member.is_platform_admin),
                created_at=member.created_at,
            )
            for member in members
        ),
        customers_count=customers_count,
        suppliers_count=suppliers_count,
        expenses_count=expenses_count,
        last_sale_at=last_sale_at,
    )


# ---------------------------------------------------------------------------
# Bootstrap: platform-admin flag management (operator-run only)
# ---------------------------------------------------------------------------


async def find_user_by_identifier(
    session: AsyncSession, identifier: str
) -> User | None:
    """Resolve a user by Clerk user id or case-insensitive email."""
    token = identifier.strip()
    if not token:
        return None
    return (
        await session.execute(
            select(User).where(
                or_(
                    User.clerk_user_id == token,
                    func.lower(User.email) == token.lower(),
                )
            )
        )
    ).scalar_one_or_none()


async def record_admin_audit(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> AdminAuditLog:
    """Append one audit entry (flushed, committed with the caller's txn)."""
    entry = AdminAuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )
    session.add(entry)
    await session.flush()
    return entry


async def set_platform_admin(
    session: AsyncSession,
    *,
    identifier: str,
    grant: bool,
    actor_user_id: uuid.UUID | None = None,
    actor_label: str | None = None,
) -> User:
    """Grant or revoke the platform-admin flag for an existing user.

    This is the *only* code path that sets `is_platform_admin` to true.
    It intentionally requires an already-provisioned user (identified by
    Clerk user id or email) and refuses to revoke the last remaining
    platform administrator. The change and its audit entry are written in
    the caller's transaction.
    """
    user = await find_user_by_identifier(session, identifier)
    if user is None:
        raise PlatformAdminTargetNotFoundError(
            f"No user matches '{identifier}' (expected a Clerk user id or email)"
        )

    if user.is_platform_admin == grant:
        return user

    if not grant:
        others = await _scalar_count(
            session,
            select(func.count(User.id)).where(
                User.is_platform_admin.is_(True), User.id != user.id
            ),
        )
        if others == 0:
            raise LastPlatformAdminError(
                "Refusing to revoke the last remaining platform administrator"
            )

    user.is_platform_admin = grant
    await record_admin_audit(
        session,
        actor_user_id=actor_user_id,
        action="platform_admin.granted" if grant else "platform_admin.revoked",
        target_type="user",
        target_id=user.id,
        details={
            "identifier": identifier,
            "actor": actor_label or "cli",
        },
    )
    return user


async def list_platform_admins(session: AsyncSession) -> tuple[User, ...]:
    """Every current platform administrator, oldest first."""
    rows = (
        (
            await session.execute(
                select(User)
                .where(User.is_platform_admin.is_(True))
                .order_by(User.created_at.asc(), User.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return tuple(rows)
