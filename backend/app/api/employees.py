"""Employee invitation and shop membership endpoints.

Owner-only administration (scoped to the owner's own shop, server-derived
from the verified Clerk token - never a client-supplied `shop_id`):

    POST   /employees/invitations            invite an email as cashier
    GET    /employees/invitations            list invitations + status
    DELETE /employees/invitations/{id}       revoke a pending invitation
    GET    /employees                        list current employees
    DELETE /employees/{user_id}              revoke an employee's access

Authenticated acceptance (any signed-in identity, deliberately *not*
shop-scoped because the caller has no shop yet):

    POST   /employees/invitations/accept     consume a single-use token

The raw invitation token is returned exactly once, from the creation
endpoint. List/read endpoints never include it; the token is stored only
as a SHA-256 digest and is never logged.
"""

import uuid

from fastapi import APIRouter, HTTPException, Request, status

from app.api.dependencies import DbSession, Owner, ShopId
from app.auth.auth import require_auth, resolve_verified_email
from app.config import get_settings
from app.schemas.employees import (
    AcceptInvitationRequest,
    AcceptInvitationResponse,
    EmployeeResponse,
    InvitationCreatedResponse,
    InvitationResponse,
    InviteEmployeeRequest,
)
from app.services import employees as employees_service
from app.services.employees import (
    AlreadyInvitedError,
    CannotRevokeOwnerError,
    CannotRevokePlatformAdminError,
    EmailAlreadyEmployeeError,
    EmailBelongsToAnotherShopError,
    EmailBoundToDifferentIdentityError,
    ExistingMembershipError,
    InvalidInvitationEmailError,
    InvalidInvitationRoleError,
    InvitationAlreadyUsedError,
    InvitationEmailMismatchError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationRevokedError,
    NotAnEmployeeError,
    PlatformAdminCannotAcceptError,
    invitation_status,
)

router = APIRouter(prefix="/employees", tags=["employees"])


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _forbidden(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def _gone(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc))


def _invitation_response(inv) -> InvitationResponse:  # type: ignore[no-untyped-def]
    return InvitationResponse(
        id=inv.id,
        email=inv.email,
        role=inv.role.value if hasattr(inv.role, "value") else str(inv.role),
        status=invitation_status(inv).value,
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        revoked_at=inv.revoked_at,
        created_at=inv.created_at,
    )


@router.post(
    "/invitations",
    response_model=InvitationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite an employee to the owner's shop",
)
async def create_employee_invitation(
    owner: Owner,
    shop_id: ShopId,
    db: DbSession,
    body: InviteEmployeeRequest,
) -> InvitationCreatedResponse:
    try:
        invitation, raw_token = await employees_service.create_invitation(
            db,
            shop_id=shop_id,
            invited_by_user_id=owner.id,
            email=body.email,
            role=body.role,
        )
    except (InvalidInvitationEmailError, InvalidInvitationRoleError) as exc:
        raise _unprocessable(exc) from exc
    except AlreadyInvitedError as exc:
        raise _conflict(exc) from exc
    except (
        EmailAlreadyEmployeeError,
        EmailBelongsToAnotherShopError,
    ) as exc:
        raise _conflict(exc) from exc

    path = f"/invite/{raw_token}"
    base = get_settings().frontend_base_url.rstrip("/")
    return InvitationCreatedResponse(
        **_invitation_response(invitation).model_dump(),
        token=raw_token,
        invite_path=path,
        invite_url=f"{base}{path}" if base else path,
    )


@router.get(
    "/invitations",
    response_model=list[InvitationResponse],
    summary="List the shop's employee invitations",
)
async def list_employee_invitations(
    owner: Owner,
    shop_id: ShopId,
    db: DbSession,
) -> list[InvitationResponse]:
    rows = await employees_service.list_invitations(db, shop_id=shop_id)
    return [_invitation_response(row) for row in rows]


@router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a pending invitation",
)
async def revoke_employee_invitation(
    invitation_id: uuid.UUID,
    owner: Owner,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    try:
        await employees_service.revoke_invitation(
            db, shop_id=shop_id, invitation_id=invitation_id
        )
    except InvitationNotFoundError as exc:
        raise _not_found(exc) from exc
    except InvitationAlreadyUsedError as exc:
        raise _conflict(exc) from exc


@router.post(
    "/invitations/accept",
    response_model=AcceptInvitationResponse,
    summary="Accept an invitation as the signed-in identity",
)
async def accept_employee_invitation(
    request: Request,
    db: DbSession,
    body: AcceptInvitationRequest,
) -> AcceptInvitationResponse:
    payload = await require_auth(request)
    clerk_user_id = payload.get("sub")
    if not clerk_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing subject",
            headers={"WWW-Authenticate": "Bearer"},
        )

    verified_email = await resolve_verified_email(payload)
    if verified_email is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Could not verify the email address of this identity; "
                "sign in with a verified email to accept the invitation"
            ),
        )

    name_claim = payload.get("name")
    display_name = name_claim if isinstance(name_claim, str) else None

    try:
        result = await employees_service.accept_invitation(
            db,
            clerk_user_id=clerk_user_id,
            verified_email=verified_email,
            token=body.token,
            display_name=display_name,
        )
    except InvitationNotFoundError as exc:
        raise _not_found(exc) from exc
    except (InvitationExpiredError, InvitationRevokedError) as exc:
        raise _gone(exc) from exc
    except (
        InvitationAlreadyUsedError,
        ExistingMembershipError,
        EmailBoundToDifferentIdentityError,
    ) as exc:
        raise _conflict(exc) from exc
    except (
        InvitationEmailMismatchError,
        PlatformAdminCannotAcceptError,
    ) as exc:
        raise _forbidden(exc) from exc

    return AcceptInvitationResponse(
        shop_id=result.shop_id,
        shop_name=result.shop_name,
        role=result.role.value,
        already_member=result.already_member,
    )


@router.get(
    "",
    response_model=list[EmployeeResponse],
    summary="List current shop employees",
)
async def list_shop_employees(
    owner: Owner,
    shop_id: ShopId,
    db: DbSession,
) -> list[EmployeeResponse]:
    rows = await employees_service.list_employees(db, shop_id=shop_id)
    return [
        EmployeeResponse(
            id=row.id,
            name=row.name,
            email=row.email,
            role=row.role.value,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an employee's shop access",
)
async def revoke_shop_employee(
    user_id: uuid.UUID,
    owner: Owner,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    try:
        await employees_service.revoke_employee(
            db,
            shop_id=shop_id,
            user_id=user_id,
            acting_user_id=owner.id,
        )
    except NotAnEmployeeError as exc:
        raise _not_found(exc) from exc
    except (CannotRevokeOwnerError, CannotRevokePlatformAdminError) as exc:
        raise _forbidden(exc) from exc
