"""Super Admin Panel tests.

Covers:
- Authorization: 401 unauthenticated, 403 for shop owners/staff/other
  tenants, 200 for platform administrators, and the impossibility of
  elevating via headers/query params.
- Platform-wide aggregate correctness against hand-built fixture data.
- Empty-platform behavior (only the admin's own identity exists).
- User/shop pagination, search, role filters, unknown ids.
- Sensitive fields never appearing in responses.
- Bootstrap safety: flag default, grant/revoke, audit entries, and the
  last-admin revoke guard.
- Existing shop-level authorization remaining unchanged.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AdminAuditLog,
    Category,
    Customer,
    Expense,
    ExpenseCategory,
    Payment,
    PaymentMethod,
    Product,
    ProductType,
    Purchase,
    Sale,
    SaleStatus,
    Shop,
    Supplier,
    User,
    UserRole,
)
from app.scripts.grant_platform_admin import main as grant_main
from app.services.admin import (
    LastPlatformAdminError,
    PlatformAdminTargetNotFoundError,
    get_platform_overview,
    set_platform_admin,
)


def _mock_verify(user_clerk_id: str):
    """Return a patcher that makes _verify_token return the given user."""
    return patch(
        "app.auth.auth._verify_token",
        new_callable=AsyncMock,
        return_value={"sub": user_clerk_id},
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


async def _create_shop(
    session: AsyncSession,
    *,
    name: str = "Fixture Shop",
    created_at: datetime | None = None,
) -> Shop:
    shop = Shop(name=name)
    if created_at is not None:
        shop.created_at = created_at
        shop.updated_at = created_at
    session.add(shop)
    await session.flush()
    return shop


async def _create_user(
    session: AsyncSession,
    clerk_id: str,
    *,
    shop: Shop | None = None,
    role: UserRole = UserRole.OWNER,
    is_platform_admin: bool = False,
    created_at: datetime | None = None,
    name: str | None = None,
    email: str | None = None,
) -> tuple[Shop, User]:
    if shop is None:
        shop = await _create_shop(
            session, name=f"Shop {clerk_id}", created_at=created_at
        )
    user = User(
        shop_id=shop.id,
        clerk_user_id=clerk_id,
        name=name or f"User {clerk_id}",
        email=email or f"{clerk_id}@example.com",
        role=role,
        is_platform_admin=is_platform_admin,
    )
    if created_at is not None:
        user.created_at = created_at
        user.updated_at = created_at
    session.add(user)
    await session.flush()
    return shop, user


async def _create_admin(
    session: AsyncSession, clerk_id: str = "clerk_platform_admin"
) -> tuple[Shop, User]:
    return await _create_user(session, clerk_id, is_platform_admin=True)


async def _create_shopless_admin(
    session: AsyncSession, clerk_id: str = "clerk_shopless_admin"
) -> User:
    """Provision a platform administrator with no shop (`shop_id = NULL`)."""
    user = User(
        shop_id=None,
        clerk_user_id=clerk_id,
        name=f"User {clerk_id}",
        email=f"{clerk_id}@example.com",
        role=UserRole.OWNER,
        is_platform_admin=True,
    )
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestAdminAuthorization:
    """Only the server-side is_platform_admin flag opens /admin."""

    ADMIN_PATHS = ("/admin/overview", "/admin/health", "/admin/users", "/admin/shops")

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        for path in self.ADMIN_PATHS:
            response = await client.get(path)
            assert response.status_code == 401, path

    @pytest.mark.asyncio
    async def test_unprovisioned_clerk_identity_returns_401(
        self, client: AsyncClient
    ) -> None:
        with _mock_verify("clerk_never_provisioned_0001"):
            response = await client.get("/admin/overview")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_shop_owner_is_denied(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, owner = await _create_user(
            api_session, "clerk_owner_denied", role=UserRole.OWNER
        )
        with _mock_verify(owner.clerk_user_id or ""):
            for path in self.ADMIN_PATHS:
                response = await api_client.get(path)
                assert response.status_code == 403, path

    @pytest.mark.asyncio
    async def test_shop_staff_is_denied(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, staff = await _create_user(
            api_session, "clerk_staff_denied", role=UserRole.STAFF
        )
        with _mock_verify(staff.clerk_user_id or ""):
            for path in self.ADMIN_PATHS:
                response = await api_client.get(path)
                assert response.status_code == 403, path

    @pytest.mark.asyncio
    async def test_cross_shop_user_cannot_read_other_tenant_via_admin(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        """An owner of shop B cannot use /admin to inspect shop A."""
        shop_a = await _create_shop(api_session, name="Tenant A")
        _, _victim = await _create_user(api_session, "clerk_a_victim", shop=shop_a)
        _, owner_b = await _create_user(api_session, "clerk_b_attacker")

        with _mock_verify(owner_b.clerk_user_id or ""):
            for path in (
                "/admin/overview",
                f"/admin/users/{shop_a.id}",
                f"/admin/shops/{shop_a.id}",
                "/admin/users",
                "/admin/shops",
            ):
                response = await api_client.get(path)
                assert response.status_code == 403, path

    @pytest.mark.asyncio
    async def test_headers_and_query_params_cannot_elevate(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, owner = await _create_user(api_session, "clerk_header_attack")
        with _mock_verify(owner.clerk_user_id or ""):
            response = await api_client.get(
                "/admin/overview",
                headers={
                    "X-Platform-Admin": "true",
                    "X-User-Role": "admin",
                    "X-Shop-Id": str(uuid.uuid4()),
                },
                params={"is_platform_admin": "true", "role": "admin"},
            )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_platform_admin_is_allowed(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, admin = await _create_admin(api_session, "clerk_admin_ok")
        with _mock_verify(admin.clerk_user_id or ""):
            for path in self.ADMIN_PATHS:
                response = await api_client.get(path)
                assert response.status_code == 200, path

    @pytest.mark.asyncio
    async def test_auth_me_reports_flag(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, admin = await _create_admin(api_session, "clerk_me_admin")
        _, owner = await _create_user(api_session, "clerk_me_owner")

        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.get("/auth/me")
        assert response.status_code == 200
        assert response.json()["is_platform_admin"] is True

        with _mock_verify(owner.clerk_user_id or ""):
            response = await api_client.get("/auth/me")
        assert response.status_code == 200
        assert response.json()["is_platform_admin"] is False


# ---------------------------------------------------------------------------
# Overview aggregates
# ---------------------------------------------------------------------------


class TestAdminOverview:
    @pytest.mark.asyncio
    async def test_empty_platform_reports_zeroes_not_errors(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        """With only the admin's own identity, all totals are honest zeroes."""
        _, admin = await _create_admin(api_session, "clerk_admin_empty")

        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.get("/admin/overview")

        assert response.status_code == 200
        data = response.json()
        assert data["total_users"] == 1
        assert data["total_shops"] == 1
        assert data["total_sales"] == 0
        assert data["sales_last_30_days"] == 0
        assert data["total_purchases"] == 0
        assert data["purchases_last_30_days"] == 0
        assert data["total_payments"] == 0
        assert data["total_customers"] == 0
        assert data["total_suppliers"] == 0
        assert data["total_products"] == 0
        assert data["platform_admin_count"] == 1
        # The admin identity itself was created moments ago, so it is the
        # only row inside the 7/30-day windows - tenant data is otherwise
        # empty and every operational total is a real zero.
        assert data["new_users_last_7_days"] == 1
        assert data["new_users_last_30_days"] == 1

        assert len(data["user_registrations_by_day"]) == 30
        assert len(data["shop_creations_by_day"]) == 30
        assert sum(d["count"] for d in data["user_registrations_by_day"]) == 1
        assert sum(d["count"] for d in data["shop_creations_by_day"]) == 1

        keys = {m["key"] for m in data["unavailable_metrics"]}
        assert "active_inactive_users" in keys
        assert "subscriptions_billing" in keys

        with _mock_verify(admin.clerk_user_id or ""):
            users = await api_client.get("/admin/users")
            shops = await api_client.get("/admin/shops")
        assert users.status_code == 200 and users.json()["total"] == 1
        assert shops.status_code == 200 and shops.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_platform_wide_counts_and_time_windows(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        now = datetime.now(UTC)

        # Admin identity: 40 days old (outside every window).
        _admin_shop, admin = await _create_user(
            api_session,
            "clerk_admin_counts",
            is_platform_admin=True,
            created_at=now - timedelta(days=40),
        )
        # Old tenant: shop + owner 40 days old, staff joined 3 days ago.
        old_shop, _ = await _create_user(
            api_session,
            "clerk_old_owner",
            created_at=now - timedelta(days=40),
            name="Old Owner",
        )
        await _create_user(
            api_session,
            "clerk_new_staff",
            shop=old_shop,
            role=UserRole.STAFF,
            created_at=now - timedelta(days=3),
            name="New Staff",
        )
        # Ten-day-old tenant, inside 30 days but outside 7.
        _ten_shop, _ = await _create_user(
            api_session, "clerk_ten_owner", created_at=now - timedelta(days=10)
        )

        # Business activity, all inside the old tenant.
        supplier = Supplier(shop_id=old_shop.id, name="Fixture Supplier")
        customer = Customer(shop_id=old_shop.id, name="Fixture Customer")
        category = Category(shop_id=old_shop.id, name="Fixture Category")
        api_session.add_all([supplier, customer, category])
        await api_session.flush()

        api_session.add(
            Product(
                shop_id=old_shop.id,
                category_id=category.id,
                name="Fixture Product",
                product_type=ProductType.OPEN_FABRIC,
            )
        )
        api_session.add(
            Sale(
                shop_id=old_shop.id,
                subtotal=Decimal("100.00"),
                total=Decimal("100.00"),
                status=SaleStatus.COMPLETED,
                created_at=now - timedelta(days=40),
                updated_at=now - timedelta(days=40),
            )
        )
        api_session.add(
            Sale(
                shop_id=old_shop.id,
                subtotal=Decimal("50.00"),
                total=Decimal("50.00"),
                status=SaleStatus.COMPLETED,
                created_at=now - timedelta(days=20),
                updated_at=now - timedelta(days=20),
            )
        )
        api_session.add(
            Purchase(
                shop_id=old_shop.id,
                supplier_id=supplier.id,
                subtotal=Decimal("25.00"),
                total=Decimal("25.00"),
                created_at=now - timedelta(days=5),
                updated_at=now - timedelta(days=5),
            )
        )
        api_session.add(
            Payment(
                shop_id=old_shop.id,
                amount=Decimal("10.00"),
                method=PaymentMethod.CASH,
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=2),
            )
        )
        await api_session.flush()

        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.get("/admin/overview")

        assert response.status_code == 200
        data = response.json()

        assert data["total_users"] == 4
        assert data["total_owners"] == 3
        assert data["total_staff"] == 1
        assert data["platform_admin_count"] == 1
        assert data["total_shops"] == 3

        assert data["new_users_last_7_days"] == 1
        assert data["new_users_last_30_days"] == 2
        assert data["new_shops_last_7_days"] == 0
        assert data["new_shops_last_30_days"] == 1

        assert data["total_sales"] == 2
        assert data["sales_last_30_days"] == 1
        assert data["total_purchases"] == 1
        assert data["purchases_last_30_days"] == 1
        assert data["total_payments"] == 1
        assert data["payments_last_30_days"] == 1
        assert data["total_customers"] == 1
        assert data["total_suppliers"] == 1
        assert data["total_products"] == 1

        assert sum(d["count"] for d in data["user_registrations_by_day"]) == 2
        assert sum(d["count"] for d in data["shop_creations_by_day"]) == 1

    @pytest.mark.asyncio
    async def test_overview_does_not_leak_shop_ids_or_money(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, admin = await _create_admin(api_session, "clerk_admin_privacy")
        shop, _ = await _create_user(api_session, "clerk_privacy_user")
        api_session.add(
            Sale(
                shop_id=shop.id,
                subtotal=Decimal("123.45"),
                total=Decimal("123.45"),
                status=SaleStatus.COMPLETED,
            )
        )
        await api_session.flush()

        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.get("/admin/overview")

        data = response.json()
        # Counts only - no shop/user identities, no monetary totals.
        body = json.dumps(data)
        assert str(shop.id) not in body
        assert "123.45" not in body


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class TestAdminUsers:
    @pytest.mark.asyncio
    async def test_pagination_search_and_role_filter(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        now = datetime.now(UTC)
        _, admin = await _create_user(
            api_session,
            "clerk_admin_users",
            is_platform_admin=True,
            created_at=now - timedelta(days=1),
        )
        shop_a = await _create_shop(api_session, name="Users Shop A")
        _, ali = await _create_user(
            api_session,
            "clerk_ali",
            shop=shop_a,
            name="Ali Khan",
            email="ali@example.com",
            created_at=now - timedelta(days=4),
        )
        _, sara = await _create_user(
            api_session,
            "clerk_sara",
            shop=shop_a,
            role=UserRole.STAFF,
            name="Sara Ahmed",
            email="sara@example.com",
            created_at=now - timedelta(days=3),
        )
        _, bilal = await _create_user(
            api_session,
            "clerk_bilal",
            name="Bilal Tariq",
            email="bilal@example.com",
            created_at=now - timedelta(days=2),
        )

        with _mock_verify(admin.clerk_user_id or ""):
            page1 = await api_client.get(
                "/admin/users", params={"limit": 2, "offset": 0}
            )
            page2 = await api_client.get(
                "/admin/users", params={"limit": 2, "offset": 2}
            )
            over = await api_client.get(
                "/admin/users", params={"limit": 2, "offset": 10}
            )

        assert page1.status_code == 200
        data = page1.json()
        assert data["total"] == 4
        assert data["limit"] == 2
        assert [u["id"] for u in data["users"]] == [str(admin.id), str(bilal.id)]

        assert page2.status_code == 200
        assert [u["id"] for u in page2.json()["users"]] == [str(sara.id), str(ali.id)]
        assert over.json()["users"] == []

        with _mock_verify(admin.clerk_user_id or ""):
            by_name = await api_client.get("/admin/users", params={"search": "khan"})
            by_email = await api_client.get("/admin/users", params={"search": "SARA@"})
            staff_only = await api_client.get("/admin/users", params={"role": "staff"})
            owners = await api_client.get("/admin/users", params={"role": "owner"})
            wildcard = await api_client.get("/admin/users", params={"search": "%"})

        assert [u["name"] for u in by_name.json()["users"]] == ["Ali Khan"]
        assert [u["name"] for u in by_email.json()["users"]] == ["Sara Ahmed"]
        assert [u["name"] for u in staff_only.json()["users"]] == ["Sara Ahmed"]
        assert owners.json()["total"] == 3
        assert wildcard.json()["total"] == 0  # LIKE wildcard is escaped

    @pytest.mark.asyncio
    async def test_user_detail_includes_real_shop_relationship(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        now = datetime.now(UTC)
        _, admin = await _create_user(
            api_session,
            "clerk_admin_detail",
            is_platform_admin=True,
            created_at=now - timedelta(days=2),
        )
        shop = await _create_shop(api_session, name="Detail Shop")
        _, owner = await _create_user(
            api_session, "clerk_detail_owner", shop=shop, name="Shop Owner"
        )
        _, staff = await _create_user(
            api_session,
            "clerk_detail_staff",
            shop=shop,
            role=UserRole.STAFF,
            name="Shop Staff",
        )

        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.get(f"/admin/users/{staff.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["user"]["id"] == str(staff.id)
        assert data["user"]["shop_name"] == "Detail Shop"
        assert data["shop"]["id"] == str(shop.id)
        assert data["shop_members_count"] == 2  # owner + staff
        assert data["user"]["role"] == "staff"
        assert data["user"]["is_platform_admin"] is False
        assert data["user"]["created_at"]
        assert data["user"]["updated_at"]
        assert data["user"]["clerk_user_id"] == "clerk_detail_staff"

        with _mock_verify(admin.clerk_user_id or ""):
            owner_detail = await api_client.get(f"/admin/users/{owner.id}")
        assert owner_detail.json()["user"]["role"] == "owner"

    @pytest.mark.asyncio
    async def test_unknown_user_returns_404(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, admin = await _create_admin(api_session, "clerk_admin_404")
        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.get(f"/admin/users/{uuid.uuid4()}")
        assert response.status_code == 404
        assert "was not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_no_sensitive_fields_in_responses(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, admin = await _create_admin(api_session, "clerk_admin_privacy_check")
        _, user = await _create_user(api_session, "clerk_pii_target")

        with _mock_verify(admin.clerk_user_id or ""):
            listing = await api_client.get("/admin/users")
            detail = await api_client.get(f"/admin/users/{user.id}")

        allowed_user_keys = {
            "id",
            "name",
            "email",
            "clerk_user_id",
            "role",
            "is_platform_admin",
            "shop_id",
            "shop_name",
            "created_at",
            "updated_at",
        }
        for item in listing.json()["users"]:
            assert set(item.keys()) == allowed_user_keys
        assert set(detail.json()["user"].keys()) == allowed_user_keys

        lowered = (listing.text + detail.text).lower()
        for forbidden in (
            "password",
            "hash",
            "token",
            "secret",
            "api_key",
            "apikey",
            "session",
            "reset",
        ):
            assert forbidden not in lowered

    @pytest.mark.asyncio
    async def test_invalid_pagination_and_ids_are_rejected(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, admin = await _create_admin(api_session, "clerk_admin_validation")
        with _mock_verify(admin.clerk_user_id or ""):
            assert (
                await api_client.get("/admin/users", params={"limit": 0})
            ).status_code == 422
            assert (
                await api_client.get("/admin/users", params={"limit": 101})
            ).status_code == 422
            assert (
                await api_client.get("/admin/users", params={"offset": -1})
            ).status_code == 422
            assert (await api_client.get("/admin/users/not-a-uuid")).status_code == 422


# ---------------------------------------------------------------------------
# Shops
# ---------------------------------------------------------------------------


class TestAdminShops:
    @pytest.mark.asyncio
    async def test_list_pagination_search_and_counts(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        now = datetime.now(UTC)
        _, admin = await _create_user(
            api_session,
            "clerk_admin_shops",
            is_platform_admin=True,
            created_at=now - timedelta(days=10),
        )
        fabric_world = await _create_shop(
            api_session, name="Fabric World", created_at=now - timedelta(days=2)
        )
        _, _ali = await _create_user(
            api_session,
            "clerk_fw_owner",
            shop=fabric_world,
            name="Ali Owner",
            created_at=now - timedelta(days=2),
        )
        await _create_user(
            api_session,
            "clerk_fw_staff",
            shop=fabric_world,
            role=UserRole.STAFF,
            created_at=now - timedelta(days=1),
        )
        cloth_house = await _create_shop(
            api_session, name="Cloth House", created_at=now - timedelta(days=1)
        )
        await _create_user(
            api_session,
            "clerk_ch_owner",
            shop=cloth_house,
            name="Bilal Owner",
            created_at=now - timedelta(days=1),
        )

        supplier = Supplier(shop_id=fabric_world.id, name="World Supplier")
        category = Category(shop_id=fabric_world.id, name="World Category")
        api_session.add_all([supplier, category])
        await api_session.flush()
        api_session.add(
            Product(
                shop_id=fabric_world.id,
                category_id=category.id,
                name="World Product",
                product_type=ProductType.OPEN_FABRIC,
            )
        )
        for amount in ("10.00", "20.00"):
            api_session.add(
                Sale(
                    shop_id=fabric_world.id,
                    subtotal=Decimal(amount),
                    total=Decimal(amount),
                    status=SaleStatus.COMPLETED,
                )
            )
        api_session.add(
            Purchase(
                shop_id=fabric_world.id,
                supplier_id=supplier.id,
                subtotal=Decimal("30.00"),
                total=Decimal("30.00"),
            )
        )
        await api_session.flush()

        with _mock_verify(admin.clerk_user_id or ""):
            listing = await api_client.get("/admin/shops")
            search = await api_client.get("/admin/shops", params={"search": "fabric"})
            page = await api_client.get(
                "/admin/shops", params={"limit": 2, "offset": 1}
            )

        assert listing.status_code == 200
        data = listing.json()
        assert data["total"] == 3
        # Newest shop first (created_at desc, id desc).
        assert [s["name"] for s in data["shops"]] == [
            "Cloth House",
            "Fabric World",
            "Shop clerk_admin_shops",
        ]

        assert search.json()["total"] == 1
        fw = search.json()["shops"][0]
        assert fw["name"] == "Fabric World"
        assert fw["owner"]["name"] == "Ali Owner"
        assert fw["user_count"] == 2
        assert fw["sales_count"] == 2
        assert fw["purchases_count"] == 1
        assert fw["products_count"] == 1

        assert page.status_code == 200
        assert len(page.json()["shops"]) == 2

    @pytest.mark.asyncio
    async def test_shop_detail_members_and_counts(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, admin = await _create_admin(api_session, "clerk_admin_shop_detail")
        shop = await _create_shop(api_session, name="Detail Shop")
        now = datetime.now(UTC)
        _, owner = await _create_user(
            api_session,
            "clerk_shop_owner",
            shop=shop,
            name="Owner Person",
            created_at=now - timedelta(hours=2),
        )
        _, staff = await _create_user(
            api_session,
            "clerk_shop_staff",
            shop=shop,
            role=UserRole.STAFF,
            name="Staff Person",
            created_at=now - timedelta(hours=1),
        )
        supplier = Supplier(shop_id=shop.id, name="Supplier")
        customer = Customer(shop_id=shop.id, name="Customer")
        api_session.add_all([supplier, customer])
        await api_session.flush()
        api_session.add(
            Expense(
                shop_id=shop.id,
                category=ExpenseCategory.OTHER,
                amount=Decimal("5.00"),
                payment_method=PaymentMethod.CASH,
            )
        )
        sale_time = datetime.now(UTC)
        api_session.add(
            Sale(
                shop_id=shop.id,
                subtotal=Decimal("9.00"),
                total=Decimal("9.00"),
                status=SaleStatus.COMPLETED,
                created_at=sale_time,
                updated_at=sale_time,
            )
        )
        await api_session.flush()

        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.get(f"/admin/shops/{shop.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["shop"]["id"] == str(shop.id)
        assert data["shop"]["owner"]["id"] == str(owner.id)
        assert data["shop"]["user_count"] == 2
        assert [m["name"] for m in data["members"]] == ["Owner Person", "Staff Person"]
        assert [m["role"] for m in data["members"]] == ["owner", "staff"]
        assert data["customers_count"] == 1
        assert data["suppliers_count"] == 1
        assert data["expenses_count"] == 1
        assert data["shop"]["sales_count"] == 1
        assert data["last_sale_at"] is not None
        assert str(staff.id) in {m["id"] for m in data["members"]}

    @pytest.mark.asyncio
    async def test_unknown_shop_returns_404(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, admin = await _create_admin(api_session, "clerk_admin_shop_404")
        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.get(f"/admin/shops/{uuid.uuid4()}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_shop_detail_exposes_no_secrets(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        _, admin = await _create_admin(api_session, "clerk_admin_shop_secrets")
        shop = await _create_shop(api_session, name="Secret Shop")
        await _create_user(api_session, "clerk_secret_shop_owner", shop=shop)

        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.get(f"/admin/shops/{shop.id}")

        lowered = response.text.lower()
        for forbidden in ("password", "hash", "token", "secret_key", "api_key"):
            assert forbidden not in lowered
        for member in response.json()["members"]:
            assert set(member.keys()) == {
                "id",
                "name",
                "email",
                "role",
                "is_platform_admin",
                "created_at",
            }


# ---------------------------------------------------------------------------
# Platform status + bootstrap safety
# ---------------------------------------------------------------------------


class TestPlatformAdminBootstrap:
    @pytest.mark.asyncio
    async def test_flag_defaults_to_false_and_is_not_nullable(self) -> None:
        column = User.__table__.c.is_platform_admin
        assert column.nullable is False
        assert column.server_default is not None

    @pytest.mark.asyncio
    async def test_grant_by_email_is_case_insensitive_and_audited(
        self, api_session: AsyncSession
    ) -> None:
        _, user = await _create_user(
            api_session, "clerk_bootstrap_user", email="Bootstrap@Example.com"
        )
        assert user.is_platform_admin is False

        updated = await set_platform_admin(
            api_session,
            identifier="bootstrap@example.com",
            grant=True,
            actor_label="pytest",
        )
        assert updated.id == user.id
        assert updated.is_platform_admin is True

        entries = (
            (
                await api_session.execute(
                    select(AdminAuditLog).where(AdminAuditLog.target_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(entries) == 1
        assert entries[0].action == "platform_admin.granted"
        assert entries[0].target_type == "user"
        assert entries[0].details is not None
        assert entries[0].details["actor"] == "pytest"

        # Re-granting is idempotent: no duplicate audit entries.
        await set_platform_admin(
            api_session, identifier="clerk_bootstrap_user", grant=True
        )
        count = (
            await api_session.execute(
                select(func.count(AdminAuditLog.id)).where(
                    AdminAuditLog.target_id == user.id
                )
            )
        ).scalar()
        assert count == 1

    @pytest.mark.asyncio
    async def test_grant_unknown_user_is_refused(
        self, api_session: AsyncSession
    ) -> None:
        with pytest.raises(PlatformAdminTargetNotFoundError):
            await set_platform_admin(
                api_session, identifier="nobody@example.com", grant=True
            )

    @pytest.mark.asyncio
    async def test_revoke_last_admin_is_refused(
        self, api_session: AsyncSession
    ) -> None:
        _, admin = await _create_admin(api_session, "clerk_only_admin")

        with pytest.raises(LastPlatformAdminError):
            await set_platform_admin(
                api_session, identifier="clerk_only_admin", grant=False
            )

        await api_session.refresh(admin)
        assert admin.is_platform_admin is True

    @pytest.mark.asyncio
    async def test_revoke_with_multiple_admins_succeeds_and_is_audited(
        self, api_session: AsyncSession
    ) -> None:
        _, first = await _create_admin(api_session, "clerk_admin_first")
        _, second = await _create_admin(api_session, "clerk_admin_second")

        await set_platform_admin(
            api_session,
            identifier="clerk_admin_first",
            grant=False,
            actor_label="pytest",
        )
        await api_session.refresh(first)
        await api_session.refresh(second)
        assert first.is_platform_admin is False
        assert second.is_platform_admin is True

        actions = (
            (
                await api_session.execute(
                    select(AdminAuditLog.action).where(
                        AdminAuditLog.target_id == first.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert actions == ["platform_admin.revoked"]

    def test_cli_argument_handling(self) -> None:
        # Missing identifier is a usage error (SystemExit code 2).
        with pytest.raises(SystemExit) as excinfo:
            grant_main([])
        assert excinfo.value.code == 2

        # --list only reads; it must succeed against the empty test DB.
        assert grant_main(["--list"]) == 0

    @pytest.mark.asyncio
    async def test_service_overview_matches_direct_sql(
        self, api_session: AsyncSession
    ) -> None:
        """The service never fabricates numbers: totals equal a direct count."""
        shop = await _create_shop(api_session, name="SQL Shop")
        await _create_user(api_session, "clerk_sql_owner", shop=shop)
        await _create_user(
            api_session, "clerk_sql_staff", shop=shop, role=UserRole.STAFF
        )

        overview = await get_platform_overview(api_session)
        direct_users = (await api_session.execute(select(func.count(User.id)))).scalar()
        direct_shops = (await api_session.execute(select(func.count(Shop.id)))).scalar()

        assert overview.total_users == direct_users == 2
        assert overview.total_shops == direct_shops == 1


# ---------------------------------------------------------------------------
# Existing shop-level authorization is untouched
# ---------------------------------------------------------------------------


class TestShopLevelAuthorizationUnchanged:
    @pytest.mark.asyncio
    async def test_admin_flag_does_not_change_shop_scoping(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        """A platform admin's shop routes still resolve to their own shop."""
        admin_shop, admin = await _create_admin(api_session, "clerk_admin_scoped")
        other_shop = await _create_shop(api_session, name="Other Tenant")
        other_customer = Customer(shop_id=other_shop.id, name="Other Customer")
        api_session.add(other_customer)
        await api_session.flush()

        with _mock_verify(admin.clerk_user_id or ""):
            me = await api_client.get("/shops/me")
            foreign = await api_client.get(f"/customers/{other_customer.id}/balance")

        assert me.status_code == 200
        assert me.json()["id"] == str(admin_shop.id)
        # Platform admin does not gain access to shop-scoped data of others.
        assert foreign.status_code == 404

    @pytest.mark.asyncio
    async def test_regular_owner_and_staff_keep_shop_access(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        shop = await _create_shop(api_session, name="Normal Shop")
        _, owner = await _create_user(api_session, "clerk_normal_owner", shop=shop)
        _, staff = await _create_user(
            api_session, "clerk_normal_staff", shop=shop, role=UserRole.STAFF
        )

        for user in (owner, staff):
            with _mock_verify(user.clerk_user_id or ""):
                me = await api_client.get("/shops/me")
                denied = await api_client.get("/admin/users")
            assert me.status_code == 200
            assert me.json()["id"] == str(shop.id)
            assert denied.status_code == 403


# ---------------------------------------------------------------------------
# Platform administrators without a shop
# ---------------------------------------------------------------------------


class TestShoplessPlatformAdmin:
    """A platform admin needs no shop to sign in or to use /admin.

    The backend always allowed `shop_id = NULL` for platform admins via
    `is_platform_admin`; these tests pin that contract end to end.
    """

    @pytest.mark.asyncio
    async def test_me_returns_null_shop_and_admin_flag(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        admin = await _create_shopless_admin(api_session, "clerk_admin_me")

        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.get("/auth/me")

        assert response.status_code == 200
        body = response.json()
        assert body["shop_id"] is None
        assert body["is_platform_admin"] is True
        assert body["clerk_user_id"] == admin.clerk_user_id

    @pytest.mark.asyncio
    async def test_sync_returns_shopless_admin_without_provisioning_shop(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        admin = await _create_shopless_admin(api_session, "clerk_admin_sync")
        shops_before = (
            await api_session.execute(select(func.count(Shop.id)))
        ).scalar()

        with _mock_verify(admin.clerk_user_id or ""):
            response = await api_client.post("/auth/sync")

        assert response.status_code == 200
        assert response.json()["shop_id"] is None
        shops_after = (await api_session.execute(select(func.count(Shop.id)))).scalar()
        # Syncing a pre-provisioned identity never silently creates a tenant.
        assert shops_after == shops_before

    @pytest.mark.asyncio
    async def test_tenant_routes_forbidden_without_shop(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        admin = await _create_shopless_admin(api_session, "clerk_admin_tenant")

        with _mock_verify(admin.clerk_user_id or ""):
            me = await api_client.get("/auth/me")
            shops_me = await api_client.get("/shops/me")
            customers = await api_client.get("/customers")
            voice = await api_client.post("/ai/voice/token", json={})

        # Identity stays readable; every shop-scoped route is refused.
        assert me.status_code == 200
        assert shops_me.status_code == 403
        assert customers.status_code == 403
        assert voice.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_endpoints_available_without_shop(
        self, api_client: AsyncClient, api_session: AsyncSession
    ) -> None:
        admin = await _create_shopless_admin(api_session, "clerk_admin_api")
        shop = await _create_shop(api_session, name="Tenant A")
        await _create_user(api_session, "clerk_admin_api_owner", shop=shop)

        paths = (
            "/admin/overview",
            "/admin/health",
            "/admin/users",
            "/admin/shops",
            f"/admin/shops/{shop.id}",
            f"/admin/users/{admin.id}",
        )
        with _mock_verify(admin.clerk_user_id or ""):
            responses = {}
            for path in paths:
                responses[path] = await api_client.get(path)

        for path in paths:
            assert responses[path].status_code == 200, path

        detail = responses[f"/admin/users/{admin.id}"].json()
        assert detail["user"]["shop_id"] is None
        assert detail["user"]["is_platform_admin"] is True
        assert detail["shop"] is None
        assert detail["shop_members_count"] == 0

        users = responses["/admin/users"].json()["users"]
        admin_row = next(
            row for row in users if row["clerk_user_id"] == admin.clerk_user_id
        )
        assert admin_row["shop_id"] is None
        assert admin_row["shop_name"] is None
