"""Authentication endpoints.

Only `GET /auth/me` is exposed in V1. All other Clerk integration
(sign-in, sign-up, sign-out) happens on the React frontend via the
Clerk SDK. The backend only verifies tokens and returns application
identity.
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import CurrentUserDep, DbSession
from app.auth.auth import require_auth, resolve_verified_email
from app.models.shop import Shop
from app.models.user import User, UserRole
from app.services import employees as employees_service
from app.services.accounting import ensure_system_accounts

router = APIRouter(prefix="/auth", tags=["authentication"])


class SyncUserRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    shop_name: str | None = None
    # Set by the invite-acceptance screen / stored invite link. When present
    # and valid, sync joins the invited shop instead of creating a new one.
    invitation_token: str | None = None


def _identity_payload(user_obj: User) -> dict:
    """Shared response shape for /auth/me and /auth/sync."""
    return {
        "id": str(user_obj.id),
        "clerk_user_id": user_obj.clerk_user_id,
        "shop_id": str(user_obj.shop_id) if user_obj.shop_id else None,
        "role": user_obj.role.value if user_obj.role else None,
        "is_platform_admin": bool(user_obj.is_platform_admin),
    }


@router.get("/me")
async def me(current_user: CurrentUserDep) -> dict:
    """Return the authenticated application user and their shop context.

    The `shop_id` here is server-derived from the Clerk token, never
    client-supplied. It is `null` for a platform administrator who is not
    bound to a tenant - platform administration does not require a shop.
    """
    return {
        "id": str(current_user.id),
        "clerk_user_id": current_user.clerk_user_id,
        "shop_id": str(current_user.shop_id) if current_user.shop_id else None,
        "role": current_user.role.value if current_user.role else None,
        # Platform-operator flag, server-derived. The frontend uses it only
        # to show/hide admin navigation; every admin API re-checks it.
        "is_platform_admin": bool(current_user.is_platform_admin),
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

    A pre-provisioned identity is returned as-is - including a platform
    administrator with no shop (`shop_id: null`), for whom no tenant is
    created.

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

    # Invitation-based onboarding: never create a shop for an invited
    # employee. A bad/expired/revoked/mismatched token fails loudly instead
    # of silently falling back to owner provisioning.
    if body and body.invitation_token:
        verified_email = await resolve_verified_email(payload)
        if verified_email is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Could not verify the email address of this identity; "
                    "sign in with a verified email to accept the invitation"
                ),
            )
        try:
            await employees_service.accept_invitation(
                db,
                clerk_user_id=clerk_user_id,
                verified_email=verified_email,
                token=body.invitation_token,
                display_name=body.name,
            )
        except employees_service.InvitationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (
            employees_service.InvitationExpiredError,
            employees_service.InvitationRevokedError,
        ) as exc:
            raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
        except (
            employees_service.InvitationAlreadyUsedError,
            employees_service.ExistingMembershipError,
            employees_service.EmailBoundToDifferentIdentityError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        except (
            employees_service.InvitationEmailMismatchError,
            employees_service.PlatformAdminCannotAcceptError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc

        accepted_user = (
            await db.execute(
                select(User).where(User.clerk_user_id == clerk_user_id)
            )
        ).scalar_one_or_none()
        if accepted_user is None:  # pragma: no cover - defensive
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not provisioned",
            )
        return _identity_payload(accepted_user)

    result = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    user_obj = result.scalar_one_or_none()

    if user_obj is not None:
        return _identity_payload(user_obj)

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
            return _identity_payload(retry)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists for this email. Kindly sign in instead.",
        )
    await db.refresh(user_obj)

    return _identity_payload(user_obj)