"""Request/response schemas for the Shop Settings endpoints.

The domain (`app.models.shop.Shop`) never imports Pydantic; these models are
the HTTP wire contract only.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ShopResponse(BaseModel):
    """The current tenant's shop profile and operational defaults."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone: str | None = None
    address: str | None = None
    currency: str
    tax_id: str | None = None
    default_unit: str
    created_at: datetime
    updated_at: datetime


class UpdateShopRequest(BaseModel):
    """Partial update for shop settings - only provided fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=300)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    tax_id: str | None = Field(default=None, max_length=20)
    default_unit: str | None = Field(default=None, max_length=20)
