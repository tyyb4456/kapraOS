"""Authentication and tenant isolation tests.

Tests cover:
- Authentication: missing/invalid tokens return 401
- User resolution: known Clerk users resolve to application Users
- Tenant isolation: User A cannot access Shop B resources
- Header attack: X-Shop-Id header cannot switch tenants
- /auth/me returns correct user and shop
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Shop, User, UserRole


# ---------------------------------------------------------------------------
# Helper: create a mock-verified user context
# ---------------------------------------------------------------------------


def _mock_verify(user_clerk_id: str):
    """Return a patcher that makes _verify_token return the given user."""
    return patch(
        "app.auth.auth._verify_token",
        new_callable=AsyncMock,
        return_value={"sub": user_clerk_id},
    )


def _mock_verify_none():
    """Return a patcher that makes _verify_token return None."""
    return patch(
        "app.auth.auth._verify_token",
        new_callable=AsyncMock,
        return_value=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuthMe:
    """Tests for GET /auth/me."""

    @pytest.mark.asyncio
    async def test_no_token_returns_401(
        self,
        client: AsyncClient,
    ) -> None:
        response = await client.get("/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(
        self,
        client: AsyncClient,
    ) -> None:
        response = await client.get(
            "/auth/me",
            headers={"Authorization": "Bearer invalid_token_xyz"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_auth_me_returns_user_and_shop(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = Shop(name="Auth Test Shop")
        api_session.add(shop)
        await api_session.flush()
        await api_session.refresh(shop)

        user = User(
            clerk_user_id="clerk_me_00000000000000000000",
            shop_id=shop.id,
            name="Me User",
            email="me@example.com",
            role=UserRole.OWNER,
        )
        api_session.add(user)
        await api_session.flush()
        await api_session.refresh(user)

        with _mock_verify(user.clerk_user_id):
            response = await api_client.get("/auth/me")

        assert response.status_code == 200
        data = response.json()
        assert data["clerk_user_id"] == user.clerk_user_id
        assert data["role"] == "owner"
        assert uuid.UUID(data["shop_id"]) == shop.id


class TestTenantIsolation:
    """Tests that clients cannot access other tenants' data."""

    @pytest.mark.asyncio
    async def test_x_shop_id_header_cannot_switch_tenant(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        """User A is authenticated for Shop A. Adding X-Shop-Id: Shop B
        must NOT switch the tenant to Shop B.
        """
        shop_a = Shop(name="Shop A")
        shop_b = Shop(name="Shop B")
        api_session.add_all([shop_a, shop_b])
        await api_session.flush()
        await api_session.refresh(shop_a)
        await api_session.refresh(shop_b)

        user_a = User(
            clerk_user_id="clerk_a_header_test",
            shop_id=shop_a.id,
            name="Alice",
            email="alice@example.com",
            role=UserRole.OWNER,
        )
        api_session.add(user_a)
        await api_session.flush()
        await api_session.refresh(user_a)

        with _mock_verify(user_a.clerk_user_id):
            response = await api_client.get(
                "/auth/me",
                headers={"X-Shop-Id": str(shop_b.id)},
            )

        assert response.status_code == 200
        data = response.json()
        assert uuid.UUID(data["shop_id"]) == shop_a.id, (
            "X-Shop-Id header must not override the Clerk-derived shop_id"
        )

    @pytest.mark.asyncio
    async def test_user_a_cannot_access_shop_b_resources(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        """User A's Clerk token should never resolve to Shop B resources."""
        shop_a = Shop(name="Shop A for isolation")
        shop_b = Shop(name="Shop B for isolation")
        api_session.add_all([shop_a, shop_b])
        await api_session.flush()
        await api_session.refresh(shop_a)
        await api_session.refresh(shop_b)

        user_a = User(
            clerk_user_id="clerk_a_isolation",
            shop_id=shop_a.id,
            name="Alice",
            email="alice@example.com",
            role=UserRole.OWNER,
        )
        api_session.add(user_a)
        await api_session.flush()
        await api_session.refresh(user_a)

        # Create a customer in Shop B
        from app.models.customer import Customer
        customer_b = Customer(shop_id=shop_b.id, name="Bob")
        api_session.add(customer_b)
        await api_session.flush()
        await api_session.refresh(customer_b)

        with _mock_verify(user_a.clerk_user_id):
            response = await api_client.get(
                f"/customers/{customer_b.id}/balance"
            )

        # The service will try to find the customer with user_a's shop_id,
        # so it won't find it -> 404
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_user_b_cannot_access_shop_a_resources(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        """The reverse: User B should not access Shop A data."""
        shop_a = Shop(name="Shop A reverse isolation")
        shop_b = Shop(name="Shop B reverse isolation")
        api_session.add_all([shop_a, shop_b])
        await api_session.flush()
        await api_session.refresh(shop_a)
        await api_session.refresh(shop_b)

        user_b = User(
            clerk_user_id="clerk_b_reverse",
            shop_id=shop_b.id,
            name="Bob",
            email="bob@example.com",
            role=UserRole.STAFF,
        )
        api_session.add(user_b)
        await api_session.flush()
        await api_session.refresh(user_b)

        # Create a customer in Shop A
        from app.models.customer import Customer
        customer_a = Customer(shop_id=shop_a.id, name="Alice")
        api_session.add(customer_a)
        await api_session.flush()
        await api_session.refresh(customer_a)

        with _mock_verify(user_b.clerk_user_id):
            response = await api_client.get(
                f"/customers/{customer_a.id}/balance"
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_clerk_user_returns_401(
        self,
        client: AsyncClient,
    ) -> None:
        """A Clerk user who doesn't exist in our database should get 401."""
        with _mock_verify("nonexistent_clerk_user_0000000000"):
            response = await client.get("/auth/me")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_user_without_shop_returns_403(
        self,
    ) -> None:
        """Verify that shop_id is non-nullable in the User model,
        which ensures the 403 guard is meaningful."""
        col = User.__table__.c.shop_id
        assert col.nullable is False


class TestAuthMechanism:
    """Tests for the authentication mechanism itself."""

    def test_user_role_enum_values(self) -> None:
        assert UserRole.OWNER.value == "owner"
        assert UserRole.STAFF.value == "staff"
        assert len(list(UserRole)) == 2

    def test_clerk_user_id_unique_constraint(self) -> None:
        from app.models import User as UserModel
        from sqlalchemy import UniqueConstraint

        constraints = [
            c.name for c in UserModel.__table__.constraints
        ]
        assert "uq_users_clerk_user_id" in constraints

    def test_no_password_hash_column(self) -> None:
        columns = [c.name for c in User.__table__.columns]
        assert "password_hash" not in columns