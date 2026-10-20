"""Request/response schemas for employee invitations and membership.

The wire contract for owner-side employee administration
(`/employees*`) and for invitation acceptance. Domain rules live in
`app.services.employees`; these models only validate shape.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InviteEmployeeRequest(BaseModel):
    """Owner's request to invite one email address into their shop."""

    email: str = Field(min_length=3, max_length=255)
    # Only limited employee roles are ever grantable through this endpoint.
    # "salesman" is accepted as an alias for "cashier" (same role).
    role: str = Field(default="cashier", max_length=20)


class InvitationResponse(BaseModel):
    """One invitation as shown to the owner. Never carries the raw token."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    status: str
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class InvitationCreatedResponse(InvitationResponse):
    """Creation response: the only place the raw invite token is returned.

    ``token`` and ``invite_path`` are shown exactly once to the owner so the
    link can be delivered out-of-band; list/read endpoints never include
    them. The token is stored server-side only as a SHA-256 digest.
    """

    token: str
    invite_path: str
    invite_url: str


class AcceptInvitationRequest(BaseModel):
    """Authenticated acceptance of a single-use invitation token."""

    token: str = Field(min_length=8, max_length=200)


class AcceptInvitationResponse(BaseModel):
    """Result of a successful (or idempotent) acceptance."""

    shop_id: uuid.UUID
    shop_name: str
    role: str
    already_member: bool = False


class EmployeeResponse(BaseModel):
    """One current shop member visible to the owner."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    role: str
    created_at: datetime
