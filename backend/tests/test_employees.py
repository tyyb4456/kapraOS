"""Employee invitations and role-based access control.

Covers the full owner-authorized onboarding path and the least-privilege
employee role:

* invitation creation/listing/revocation is owner-only and shop-scoped;
* raw tokens are returned once and persisted only as SHA-256 digests;
* acceptance is single-use, expiring, email-bound and cannot move a user
  from another shop (nor create a second tenant);
* ``/auth/sync`` redeems an invitation instead of provisioning a shop;
* cashiers can read catalog/inventory/customer data and ring up sales, but
  every admin/mutation endpoint rejects them server-side;
* a cashier only sees sales they created;
* revoking an employee clears the tenant binding immediately.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmployeeInvitation, Sale, SaleStatus, Shop, User, UserRole
from app.services.employees import hash_token

AUTH_HEADERS = {"Authorization": "Bearer mock_token"}


def _mock_identity(clerk_id: str, email: str | None = None):
    """Patch Clerk verification with a stable subject and optional email."""
    payload: dict[str, str] = {"sub": clerk_id}
    if email is not None:
        payload["email"] = email
    return patch(
        "app.auth.auth._verify_token",
        new_callable=AsyncMock,
        return_value=payload,
    )


async def _make_shop(session: AsyncSession, name: str = "Inviting Shop") -> Shop:
    shop = Shop(name=name)
    session.add(shop)
    await session.flush()
    await session.refresh(shop)
    return shop


async def _make_user(
    session: AsyncSession,
    shop: Shop | None,
    *,
    clerk_id: str,
    email: str,
    role: UserRole = UserRole.OWNER,
    name: str = "Test User",
) -> User:
    user = User(
        clerk_user_id=clerk_id,
        shop_id=shop.id if shop is not None else None,
        name=name,
        email=email,
        role=role,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def _invite(
    client: AsyncClient,
    owner: User,
    *,
    email: str,
    role: str = "cashier",
) -> tuple[int, dict]:
    with _mock_identity(owner.clerk_user_id or "", owner.email):
        response = await client.post(
            "/employees/invitations",
            json={"email": email, "role": role},
        )
    return response.status_code, response.json()


class TestInvitationCreation:
    @pytest.mark.asyncio
    async def test_owner_invite_normalizes_email_and_hashes_token(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_invite", email="owner@shop.test"
        )

        status, body = await _invite(
            api_client, owner, email="  Salesman@Example.COM  "
        )

        assert status == 201
        assert body["email"] == "salesman@example.com"
        assert body["role"] == "cashier"
        assert body["status"] == "pending"
        assert body["invite_path"] == f"/invite/{body['token']}"
        assert len(body["token"]) >= 32

        invitation = (
            await api_session.execute(select(EmployeeInvitation))
        ).scalar_one()
        assert invitation.token_hash == hash_token(body["token"])
        assert invitation.token_hash != body["token"]
        assert invitation.invited_by_user_id == owner.id
        assert invitation.shop_id == shop.id
        assert invitation.email == "salesman@example.com"
        expiry = invitation.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        assert expiry > datetime.now(UTC)

    @pytest.mark.asyncio
    async def test_salesman_role_is_an_alias_for_cashier(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_alias", email="alias@shop.test"
        )

        status, body = await _invite(
            api_client, owner, email="salesman@example.com", role="salesman"
        )

        assert status == 201
        assert body["role"] == "cashier"

    @pytest.mark.asyncio
    async def test_invalid_email_or_privileged_role_is_rejected(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_invalid", email="invalid@shop.test"
        )

        invalid_email, _ = await _invite(api_client, owner, email="not-an-email")
        owner_role, _ = await _invite(
            api_client, owner, email="boss@example.com", role="owner"
        )
        admin_role, _ = await _invite(
            api_client, owner, email="admin@example.com", role="admin"
        )

        assert invalid_email == 422
        assert owner_role == 422
        assert admin_role == 422

    @pytest.mark.asyncio
    async def test_duplicate_pending_invite_and_existing_members_conflict(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        other_shop = await _make_shop(api_session, name="Other Shop")
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_conflict", email="conflict@shop.test"
        )
        await _make_user(
            api_session,
            shop,
            clerk_id="clerk_existing_member",
            email="member@example.com",
            role=UserRole.CASHIER,
        )
        await _make_user(
            api_session,
            other_shop,
            clerk_id="clerk_foreign_member",
            email="foreign@example.com",
            role=UserRole.CASHIER,
        )

        first, _ = await _invite(api_client, owner, email="new@example.com")
        duplicate, _ = await _invite(api_client, owner, email="new@example.com")
        member, _ = await _invite(api_client, owner, email="member@example.com")
        foreign, _ = await _invite(api_client, owner, email="foreign@example.com")

        assert first == 201
        assert duplicate == 409
        assert member == 409
        assert foreign == 409


class TestInvitationAuthorization:
    @pytest.mark.asyncio
    async def test_cashier_and_legacy_staff_cannot_manage_employees(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_authz", email="owner-authz@shop.test"
        )
        cashier = await _make_user(
            api_session,
            shop,
            clerk_id="clerk_cashier_authz",
            email="cashier-authz@shop.test",
            role=UserRole.CASHIER,
        )
        staff = await _make_user(
            api_session,
            shop,
            clerk_id="clerk_staff_authz",
            email="staff-authz@shop.test",
            role=UserRole.STAFF,
        )

        for employee in (cashier, staff):
            with _mock_identity(employee.clerk_user_id or "", employee.email):
                invite = await api_client.post(
                    "/employees/invitations",
                    json={"email": "someone@example.com", "role": "cashier"},
                )
                listing = await api_client.get("/employees/invitations")
                employees = await api_client.get("/employees")
                revoke = await api_client.delete(f"/employees/{owner.id}")

            assert invite.status_code == 403
            assert listing.status_code == 403
            assert employees.status_code == 403
            assert revoke.status_code == 403

    @pytest.mark.asyncio
    async def test_invitations_are_shop_scoped(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop_a = await _make_shop(api_session, name="Shop A")
        shop_b = await _make_shop(api_session, name="Shop B")
        owner_a = await _make_user(
            api_session, shop_a, clerk_id="clerk_owner_scope_a", email="owner-a@shop.test"
        )
        owner_b = await _make_user(
            api_session, shop_b, clerk_id="clerk_owner_scope_b", email="owner-b@shop.test"
        )

        status, invitation = await _invite(
            api_client, owner_a, email="scoped@example.com"
        )
        assert status == 201

        with _mock_identity(owner_b.clerk_user_id or "", owner_b.email):
            listing_b = await api_client.get("/employees/invitations")
            revoke_b = await api_client.delete(
                f"/employees/invitations/{invitation['id']}"
            )

        with _mock_identity(owner_a.clerk_user_id or "", owner_a.email):
            listing_a = await api_client.get("/employees/invitations")
            revoke_a = await api_client.delete(
                f"/employees/invitations/{invitation['id']}"
            )

        assert listing_b.status_code == 200
        assert listing_b.json() == []
        assert revoke_b.status_code == 404
        assert [row["id"] for row in listing_a.json()] == [invitation["id"]]
        assert revoke_a.status_code == 204


class TestInvitationAcceptance:
    @pytest.mark.asyncio
    async def test_accepting_creates_cashier_in_inviting_shop_without_new_tenant(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_accept", email="owner-accept@shop.test"
        )
        _, invitation = await _invite(
            api_client, owner, email="employee@example.com"
        )
        shops_before = (await api_session.execute(select(func.count(Shop.id)))).scalar()

        with _mock_identity("clerk_new_employee", "employee@example.com"):
            response = await api_client.post(
                "/employees/invitations/accept",
                json={"token": invitation["token"]},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["shop_id"] == str(shop.id)
        assert body["role"] == "cashier"
        assert body["already_member"] is False
        shops_after = (await api_session.execute(select(func.count(Shop.id)))).scalar()
        assert shops_after == shops_before

        user = (
            await api_session.execute(
                select(User).where(User.clerk_user_id == "clerk_new_employee")
            )
        ).scalar_one()
        assert user.shop_id == shop.id
        assert user.role is UserRole.CASHIER
        assert user.email == "employee@example.com"

        row = (
            await api_session.execute(
                select(EmployeeInvitation).where(
                    EmployeeInvitation.id == uuid.UUID(invitation["id"])
                )
            )
        ).scalar_one()
        assert row.accepted_at is not None
        assert row.accepted_by_user_id == user.id

        # Re-opening the link as the same identity is idempotent.
        with _mock_identity("clerk_new_employee", "employee@example.com"):
            again = await api_client.post(
                "/employees/invitations/accept",
                json={"token": invitation["token"]},
            )
        assert again.status_code == 200
        assert again.json()["already_member"] is True

        # A different identity can never replay the consumed token.
        with _mock_identity("clerk_token_thief", "employee@example.com"):
            stolen = await api_client.post(
                "/employees/invitations/accept",
                json={"token": invitation["token"]},
            )
        assert stolen.status_code == 409

    @pytest.mark.asyncio
    async def test_sync_with_invitation_token_joins_shop_instead_of_provisioning(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_sync", email="owner-sync@shop.test"
        )
        _, invitation = await _invite(
            api_client, owner, email="sync-employee@example.com"
        )
        shops_before = (await api_session.execute(select(func.count(Shop.id)))).scalar()

        with _mock_identity("clerk_sync_employee", "sync-employee@example.com"):
            response = await api_client.post(
                "/auth/sync",
                json={
                    "name": "Sync Employee",
                    "email": "sync-employee@example.com",
                    "invitation_token": invitation["token"],
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["shop_id"] == str(shop.id)
        assert body["role"] == "cashier"
        assert body["clerk_user_id"] == "clerk_sync_employee"
        shops_after = (await api_session.execute(select(func.count(Shop.id)))).scalar()
        assert shops_after == shops_before

        user = (
            await api_session.execute(
                select(User).where(User.clerk_user_id == "clerk_sync_employee")
            )
        ).scalar_one()
        assert user.shop_id == shop.id
        assert user.role is UserRole.CASHIER
        assert user.name == "Sync Employee"

    @pytest.mark.asyncio
    async def test_acceptance_refuses_wrong_email(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_email", email="owner-email@shop.test"
        )
        _, invitation = await _invite(
            api_client, owner, email="intended@example.com"
        )

        with _mock_identity("clerk_wrong_email", "someone-else@example.com"):
            response = await api_client.post(
                "/employees/invitations/accept",
                json={"token": invitation["token"]},
            )

        assert response.status_code == 403
        user = (
            await api_session.execute(
                select(User).where(User.clerk_user_id == "clerk_wrong_email")
            )
        ).scalar_one_or_none()
        assert user is None

    @pytest.mark.asyncio
    async def test_acceptance_requires_a_verified_email(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_noemail", email="owner-noemail@shop.test"
        )
        _, invitation = await _invite(
            api_client, owner, email="needs-email@example.com"
        )

        with _mock_identity("clerk_no_email_claim"), patch(
            "app.api.employees.resolve_verified_email",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = await api_client.post(
                "/employees/invitations/accept",
                json={"token": invitation["token"]},
            )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_expired_and_revoked_invitations_are_gone(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_expiry", email="owner-expiry@shop.test"
        )

        _, expired = await _invite(
            api_client, owner, email="expired@example.com"
        )
        expired_row = (
            await api_session.execute(
                select(EmployeeInvitation).where(
                    EmployeeInvitation.id == uuid.UUID(expired["id"])
                )
            )
        ).scalar_one()
        expired_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await api_session.flush()

        with _mock_identity("clerk_expired", "expired@example.com"):
            expired_response = await api_client.post(
                "/employees/invitations/accept",
                json={"token": expired["token"]},
            )

        _, revoked = await _invite(
            api_client, owner, email="revoked@example.com"
        )
        with _mock_identity(owner.clerk_user_id or "", owner.email):
            revoke = await api_client.delete(
                f"/employees/invitations/{revoked['id']}"
            )
        with _mock_identity("clerk_revoked", "revoked@example.com"):
            revoked_response = await api_client.post(
                "/employees/invitations/accept",
                json={"token": revoked["token"]},
            )

        with _mock_identity("clerk_unknown", "unknown@example.com"):
            unknown_response = await api_client.post(
                "/employees/invitations/accept",
                json={"token": "x" * 43},
            )

        assert expired_response.status_code == 410
        assert revoke.status_code == 204
        assert revoked_response.status_code == 410
        assert unknown_response.status_code == 404


class TestEmployeeRevocation:
    @pytest.mark.asyncio
    async def test_revoke_clears_shop_binding_immediately(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session)
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_owner_revoke", email="owner-revoke@shop.test"
        )
        cashier = await _make_user(
            api_session,
            shop,
            clerk_id="clerk_cashier_revoke",
            email="cashier-revoke@shop.test",
            role=UserRole.CASHIER,
        )

        with _mock_identity(owner.clerk_user_id or "", owner.email):
            listing = await api_client.get("/employees")
            response = await api_client.delete(f"/employees/{cashier.id}")
            self_revoke = await api_client.delete(f"/employees/{owner.id}")

        assert listing.status_code == 200
        assert [row["id"] for row in listing.json()] == [str(cashier.id)]
        assert response.status_code == 204
        assert self_revoke.status_code == 403

        await api_session.refresh(cashier)
        assert cashier.shop_id is None
        assert cashier.role is UserRole.STAFF

        with _mock_identity(cashier.clerk_user_id or "", cashier.email):
            revoked_access = await api_client.get("/customers")
        assert revoked_access.status_code == 403

    @pytest.mark.asyncio
    async def test_cannot_revoke_employee_of_another_shop(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop_a = await _make_shop(api_session, name="Revoke A")
        shop_b = await _make_shop(api_session, name="Revoke B")
        owner_a = await _make_user(
            api_session, shop_a, clerk_id="clerk_revoke_owner_a", email="revoke-a@shop.test"
        )
        foreign = await _make_user(
            api_session,
            shop_b,
            clerk_id="clerk_revoke_foreign",
            email="revoke-b@shop.test",
            role=UserRole.CASHIER,
        )

        with _mock_identity(owner_a.clerk_user_id or "", owner_a.email):
            response = await api_client.delete(f"/employees/{foreign.id}")

        assert response.status_code == 404
        await api_session.refresh(foreign)
        assert foreign.shop_id == shop_b.id


class TestCashierRBAC:
    @pytest.mark.asyncio
    async def test_cashier_reads_pos_data_but_cannot_mutate_or_admin(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session, name="RBAC Shop")
        cashier = await _make_user(
            api_session,
            shop,
            clerk_id="clerk_rbac_cashier",
            email="rbac-cashier@shop.test",
            role=UserRole.CASHIER,
        )
        await _make_user(
            api_session,
            shop,
            clerk_id="clerk_rbac_owner",
            email="rbac-owner@shop.test",
            role=UserRole.OWNER,
        )

        with _mock_identity(cashier.clerk_user_id or "", cashier.email):
            products = await api_client.get("/products")
            customers = await api_client.get("/customers")
            inventory = await api_client.get("/inventory")
            suppliers = await api_client.get("/suppliers")
            dashboard = await api_client.get("/reports/dashboard")
            create_customer = await api_client.post(
                "/customers", json={"name": "Forbidden"}
            )
            delete_product = await api_client.delete(f"/products/{uuid.uuid4()}")
            adjust_stock = await api_client.post(
                f"/inventory/{uuid.uuid4()}/adjust",
                params={"quantity_delta": "1"},
            )
            employee_admin = await api_client.get("/employees")

        assert products.status_code == 200
        assert customers.status_code == 200
        assert inventory.status_code == 200
        assert suppliers.status_code == 403
        assert dashboard.status_code == 403
        assert create_customer.status_code == 403
        assert delete_product.status_code == 403
        assert adjust_stock.status_code == 403
        assert employee_admin.status_code == 403

    @pytest.mark.asyncio
    async def test_cashier_only_lists_their_own_sales(
        self,
        api_client: AsyncClient,
        api_session: AsyncSession,
    ) -> None:
        shop = await _make_shop(api_session, name="Sales RBAC Shop")
        owner = await _make_user(
            api_session, shop, clerk_id="clerk_sales_owner", email="sales-owner@shop.test"
        )
        cashier = await _make_user(
            api_session,
            shop,
            clerk_id="clerk_sales_cashier",
            email="sales-cashier@shop.test",
            role=UserRole.CASHIER,
        )
        other_cashier = await _make_user(
            api_session,
            shop,
            clerk_id="clerk_sales_other",
            email="sales-other@shop.test",
            role=UserRole.CASHIER,
        )

        own_sale = Sale(
            shop_id=shop.id,
            subtotal=100,
            total=100,
            status=SaleStatus.COMPLETED,
            created_by_user_id=cashier.id,
        )
        other_sale = Sale(
            shop_id=shop.id,
            subtotal=200,
            total=200,
            status=SaleStatus.COMPLETED,
            created_by_user_id=other_cashier.id,
        )
        legacy_sale = Sale(
            shop_id=shop.id,
            subtotal=300,
            total=300,
            status=SaleStatus.COMPLETED,
            created_by_user_id=None,
        )
        api_session.add_all([own_sale, other_sale, legacy_sale])
        await api_session.flush()

        with _mock_identity(cashier.clerk_user_id or "", cashier.email):
            listing = await api_client.get("/sales")
            own_detail = await api_client.get(f"/sales/{own_sale.id}")
            other_detail = await api_client.get(f"/sales/{other_sale.id}")
            summary = await api_client.get("/sales/summary")

        with _mock_identity(owner.clerk_user_id or "", owner.email):
            owner_listing = await api_client.get("/sales")

        assert listing.status_code == 200
        assert [row["id"] for row in listing.json()] == [str(own_sale.id)]
        assert own_detail.status_code == 200
        assert other_detail.status_code == 404
        assert summary.status_code == 403

        assert owner_listing.status_code == 200
        assert {row["id"] for row in owner_listing.json()} == {
            str(own_sale.id),
            str(other_sale.id),
            str(legacy_sale.id),
        }
