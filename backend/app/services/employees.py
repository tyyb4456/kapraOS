"""Employee invitation + shop membership service.

Owner-authorized onboarding for a single shop. One user belongs to one
shop in this data model (`users.shop_id`), so an invitation does not
create a second membership system: accepting it binds the invited
identity to the inviting shop with the invited role, and revoking an
employee clears that binding.

Security invariants (all enforced here, never trusted from the client):

* Tokens are 256 bits from `secrets.token_urlsafe`, stored only as
  SHA-256 hex digests, single-use, expiring, and revocable.
* Only the owner's own shop is ever written: every function takes the
  server-derived `shop_id` and filters by it.
* Acceptance is bound to both the invitation email (normalized) and the
  authenticated Clerk identity; a mismatch is refused.
* A user who already belongs to another shop is never silently moved.
* The only invitable role is CASHIER ("salesman"); no endpoint can grant
  OWNER or the platform-admin flag.
* Acceptance claims the invitation with one conditional UPDATE, so a
  replayed/concurrent accept can never bind twice.
"""

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.employee_invitation import EmployeeInvitation, InvitationStatus
from app.models.shop import Shop
from app.models.user import User, UserRole

# Only these roles may be granted through an invitation. "salesman" is the
# shop-floor name for the CASHIER role and normalizes to it.
_INVITABLE_ROLES: dict[str, UserRole] = {
    "cashier": UserRole.CASHIER,
    "salesman": UserRole.CASHIER,
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmployeeError(Exception):
    """Base class for employee-domain failures."""


class InvalidInvitationEmailError(EmployeeError):
    """The invitee email is blank or not a syntactically valid address."""


class InvalidInvitationRoleError(EmployeeError):
    """The requested role is not one an owner may grant."""


class AlreadyInvitedError(EmployeeError):
    """An unexpired, unaccepted invitation for this email already exists."""


class EmailAlreadyEmployeeError(EmployeeError):
    """The email already belongs to a member of this shop."""


class EmailBelongsToAnotherShopError(EmployeeError):
    """The email belongs to a user of a different shop."""


class InvitationNotFoundError(EmployeeError):
    """No invitation for this token (or for this shop/id pair)."""


class InvitationExpiredError(EmployeeError):
    """The invitation's expiry has passed."""


class InvitationRevokedError(EmployeeError):
    """The owner revoked the invitation."""


class InvitationAlreadyUsedError(EmployeeError):
    """The invitation was already accepted by a different identity."""


class InvitationEmailMismatchError(EmployeeError):
    """The authenticated identity's verified email is not the invitee."""


class ExistingMembershipError(EmployeeError):
    """The user already belongs to a different shop."""


class PlatformAdminCannotAcceptError(EmployeeError):
    """Platform administrators stay shop-less; they cannot accept invites."""


class EmailBoundToDifferentIdentityError(EmployeeError):
    """Another Clerk identity already owns this email address."""


class NotAnEmployeeError(EmployeeError):
    """No revocable employee row for this shop/user pair."""


class CannotRevokeOwnerError(EmployeeError):
    """An owner cannot be revoked through the employee endpoint."""


class CannotRevokePlatformAdminError(EmployeeError):
    """A platform administrator is never managed through shop membership."""


@dataclass(frozen=True)
class AcceptedInvitation:
    """Successful acceptance result (shop context for the new member)."""

    shop_id: uuid.UUID
    shop_name: str
    role: UserRole
    already_member: bool = False


def normalize_email(email: str) -> str:
    """Trim and lowercase - the single normalization used everywhere."""
    return email.strip().lower()


def hash_token(token: str) -> str:
    """SHA-256 hex digest of the raw invitation token."""
    return sha256(token.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def invitation_status(invitation: EmployeeInvitation) -> InvitationStatus:
    """Derive the lifecycle state shown to the owner."""
    if invitation.revoked_at is not None:
        return InvitationStatus.REVOKED
    if invitation.accepted_at is not None:
        return InvitationStatus.ACCEPTED
    if _as_aware(invitation.expires_at) <= _utcnow():
        return InvitationStatus.EXPIRED
    return InvitationStatus.PENDING


def _as_aware(value: datetime) -> datetime:
    """Make a naive DB datetime comparable in tests that bypass Postgres."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _normalize_invitable_role(role: str | UserRole) -> UserRole:
    if isinstance(role, UserRole):
        if role == UserRole.CASHIER:
            return role
        raise InvalidInvitationRoleError(
            "Only the cashier (salesman) role can be granted by invitation"
        )
    key = role.strip().lower()
    if key not in _INVITABLE_ROLES:
        raise InvalidInvitationRoleError(
            f"Unsupported invite role {role!r}: expected cashier or salesman"
        )
    return _INVITABLE_ROLES[key]


async def create_invitation(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    invited_by_user_id: uuid.UUID | None,
    email: str,
    role: str | UserRole = UserRole.CASHIER,
) -> tuple[EmployeeInvitation, str]:
    """Create a single-use invitation and return it with the raw token.

    The raw token is never persisted; only its digest is. Raises domain
    errors (duplicate pending invite, existing member, foreign-shop email)
    before writing anything.
    """
    normalized = normalize_email(email)
    if not normalized or not _EMAIL_RE.match(normalized) or len(normalized) > 255:
        raise InvalidInvitationEmailError(
            "A valid employee email address is required"
        )

    invitable_role = _normalize_invitable_role(role)

    # Existing application user with this email?
    existing_user = (
        await session.execute(
            select(User).where(func.lower(User.email) == normalized).limit(1)
        )
    ).scalar_one_or_none()
    if existing_user is not None:
        if existing_user.shop_id == shop_id:
            raise EmailAlreadyEmployeeError(
                "This email already belongs to a member of your shop"
            )
        if existing_user.shop_id is not None or existing_user.is_platform_admin:
            raise EmailBelongsToAnotherShopError(
                "This email already belongs to another shop or a platform account"
                " and cannot be invited"
            )
        # Shop-less (e.g. a previously revoked employee): re-inviting is fine.

    now = _utcnow()
    pending = (
        await session.execute(
            select(EmployeeInvitation)
            .where(
                EmployeeInvitation.shop_id == shop_id,
                func.lower(EmployeeInvitation.email) == normalized,
                EmployeeInvitation.accepted_at.is_(None),
                EmployeeInvitation.revoked_at.is_(None),
                EmployeeInvitation.expires_at > now,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise AlreadyInvitedError(
            "An active invitation for this email already exists"
        )

    raw_token = secrets.token_urlsafe(32)
    settings = get_settings()
    expiry_days = max(1, int(settings.employee_invitation_expiry_days or 7))
    invitation = EmployeeInvitation(
        shop_id=shop_id,
        invited_by_user_id=invited_by_user_id,
        email=normalized,
        role=invitable_role,
        token_hash=hash_token(raw_token),
        expires_at=now + timedelta(days=expiry_days),
    )
    session.add(invitation)
    await session.commit()
    await session.refresh(invitation)
    return invitation, raw_token


async def list_invitations(
    session: AsyncSession, *, shop_id: uuid.UUID
) -> list[EmployeeInvitation]:
    """All invitations for the shop, newest first (owner view)."""
    rows = (
        await session.execute(
            select(EmployeeInvitation)
            .where(EmployeeInvitation.shop_id == shop_id)
            .order_by(EmployeeInvitation.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


async def revoke_invitation(
    session: AsyncSession, *, shop_id: uuid.UUID, invitation_id: uuid.UUID
) -> None:
    """Revoke a pending invitation. Already-accepted rows are immutable."""
    invitation = (
        await session.execute(
            select(EmployeeInvitation)
            .where(
                EmployeeInvitation.id == invitation_id,
                EmployeeInvitation.shop_id == shop_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if invitation is None:
        raise InvitationNotFoundError("Invitation not found")
    if invitation.accepted_at is not None:
        raise InvitationAlreadyUsedError(
            "This invitation was already accepted; revoke the employee instead"
        )
    if invitation.revoked_at is None:
        invitation.revoked_at = _utcnow()
        await session.commit()


async def list_employees(
    session: AsyncSession, *, shop_id: uuid.UUID
) -> list[User]:
    """Non-owner members of the shop (current employees)."""
    rows = (
        await session.execute(
            select(User)
            .where(User.shop_id == shop_id, User.role != UserRole.OWNER)
            .order_by(User.created_at.asc())
        )
    ).scalars().all()
    return list(rows)


async def revoke_employee(
    session: AsyncSession,
    *,
    shop_id: uuid.UUID,
    user_id: uuid.UUID,
    acting_user_id: uuid.UUID,
) -> None:
    """Remove an employee's shop membership immediately.

    The user row is kept (never deleted) but unbound from the shop, so the
    very next authenticated request fails the tenant guard. Owners and
    platform administrators are not revocable here.
    """
    user = (
        await session.execute(
            select(User).where(User.id == user_id).limit(1)
        )
    ).scalar_one_or_none()
    if user is None or user.shop_id != shop_id:
        raise NotAnEmployeeError("Employee not found in this shop")
    if user.id == acting_user_id:
        raise CannotRevokeOwnerError("You cannot revoke your own access")
    if user.role == UserRole.OWNER:
        raise CannotRevokeOwnerError("The shop owner cannot be revoked")
    if user.is_platform_admin:
        raise CannotRevokePlatformAdminError(
            "Platform administrators are not managed through shop employees"
        )
    user.shop_id = None
    # Defensive role reset: with no shop the role grants nothing, and a
    # later invitation sets the role it is authorized to grant.
    user.role = UserRole.STAFF
    await session.commit()


async def accept_invitation(
    session: AsyncSession,
    *,
    clerk_user_id: str,
    verified_email: str,
    token: str,
    display_name: str | None = None,
) -> AcceptedInvitation:
    """Accept an invitation for the authenticated Clerk identity.

    Creates (or re-binds) the application User in the invited shop with the
    invited role, in one transaction, consuming the token exactly once.
    """
    normalized_email = normalize_email(verified_email)
    if not normalized_email:
        raise InvitationEmailMismatchError(
            "The authenticated identity has no verified email to match"
        )

    invitation = (
        await session.execute(
            select(EmployeeInvitation)
            .where(EmployeeInvitation.token_hash == hash_token(token))
            .limit(1)
        )
    ).scalar_one_or_none()
    if invitation is None:
        # Same message for unknown/forged tokens - no oracle.
        raise InvitationNotFoundError("Invitation not found or no longer valid")

    existing_user = (
        await session.execute(
            select(User).where(User.clerk_user_id == clerk_user_id).limit(1)
        )
    ).scalar_one_or_none()

    now = _utcnow()

    # Idempotency: re-opening the link after a successful accept is a no-op
    # for the same identity, an error for anyone else.
    if invitation.accepted_at is not None:
        if existing_user is not None and invitation.accepted_by_user_id == existing_user.id:
            shop = await session.get(Shop, invitation.shop_id)
            return AcceptedInvitation(
                shop_id=invitation.shop_id,
                shop_name=shop.name if shop else "",
                role=invitation.role,
                already_member=True,
            )
        raise InvitationAlreadyUsedError("This invitation has already been used")

    if invitation.revoked_at is not None:
        raise InvitationRevokedError("This invitation has been revoked")
    if _as_aware(invitation.expires_at) <= now:
        raise InvitationExpiredError("This invitation has expired")
    if invitation.email != normalized_email:
        raise InvitationEmailMismatchError(
            "This invitation was issued to a different email address"
        )

    if existing_user is not None:
        if existing_user.is_platform_admin:
            raise PlatformAdminCannotAcceptError(
                "Platform administrators cannot accept shop invitations"
            )
        if existing_user.shop_id is not None:
            if existing_user.shop_id == invitation.shop_id:
                shop = await session.get(Shop, invitation.shop_id)
                return AcceptedInvitation(
                    shop_id=invitation.shop_id,
                    shop_name=shop.name if shop else "",
                    role=invitation.role,
                    already_member=True,
                )
            raise ExistingMembershipError(
                "This account already belongs to another shop"
            )
    else:
        # A different Clerk identity must never claim an email address that
        # already has an application account (mirrors /auth/sync's guard).
        email_owner = (
            await session.execute(
                select(User)
                .where(func.lower(User.email) == normalized_email)
                .limit(1)
            )
        ).scalar_one_or_none()
        if email_owner is not None:
            if email_owner.is_platform_admin:
                raise PlatformAdminCannotAcceptError(
                    "Platform administrators cannot accept shop invitations"
                )
            if email_owner.shop_id is not None:
                raise EmailBoundToDifferentIdentityError(
                    "An account already exists for this email address"
                )
            # Shop-less row with the same verified email but a different
            # Clerk identity: refuse rather than hijack the account.
            if email_owner.clerk_user_id != clerk_user_id:
                raise EmailBoundToDifferentIdentityError(
                    "An account already exists for this email address"
                )
            existing_user = email_owner

    if existing_user is None:
        name = (display_name or "").strip() or normalized_email.split("@")[0]
        user = User(
            clerk_user_id=clerk_user_id,
            shop_id=invitation.shop_id,
            name=name[:150],
            email=normalized_email,
            role=invitation.role,
        )
        session.add(user)
        await session.flush()
    else:
        user = existing_user
        user.shop_id = invitation.shop_id
        user.role = invitation.role

    # Claim the token atomically: the WHERE clause re-checks every guard, so
    # two concurrent accepts cannot both bind. Any failure rolls back the
    # membership change above with it.
    claimed = (
        await session.execute(
            update(EmployeeInvitation)
            .where(
                EmployeeInvitation.id == invitation.id,
                EmployeeInvitation.accepted_at.is_(None),
                EmployeeInvitation.revoked_at.is_(None),
                EmployeeInvitation.expires_at > now,
            )
            .values(accepted_at=now, accepted_by_user_id=user.id)
            .returning(EmployeeInvitation.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if claimed is None:
        await session.rollback()
        raise InvitationAlreadyUsedError("This invitation has already been used")

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise EmailBoundToDifferentIdentityError(
            "An account already exists for this email address"
        ) from exc

    shop = await session.get(Shop, invitation.shop_id)
    return AcceptedInvitation(
        shop_id=invitation.shop_id,
        shop_name=shop.name if shop else "",
        role=invitation.role,
        already_member=False,
    )
