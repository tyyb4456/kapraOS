"""Request/response schemas for the supplier Khata endpoints.

These are the API's wire contract only. The service layer
(`app.services.payables`) returns plain frozen dataclasses, and these models
are built from them with `model_validate()` - so the domain never has to import
Pydantic and the HTTP shape can change independently of it.

Money is `Decimal` all the way out to the response; Pydantic serialises it as a
JSON string, which keeps NUMERIC(14,2) values exact for clients.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.payment import PaymentMethod
from app.services.payables import StatementEntryType


class SupplierBalanceResponse(BaseModel):
    """What the shop owes a supplier, and the figures it was derived from."""

    model_config = ConfigDict(from_attributes=True)

    supplier_id: uuid.UUID
    total_purchases: Decimal
    total_payments: Decimal
    outstanding_balance: Decimal
    number_of_purchases: int
    number_of_payments: int
    last_purchase_at: datetime | None = None
    last_payment_at: datetime | None = None


class SupplierSummaryResponse(BaseModel):
    """Dashboard summary: purchases, paid, outstanding."""

    model_config = ConfigDict(from_attributes=True)

    supplier_id: uuid.UUID
    name: str
    phone: str | None = None
    total_purchases: Decimal
    total_paid: Decimal
    outstanding_balance: Decimal


class StatementEntryResponse(BaseModel):
    """One Khata line: a purchase (debit) or a payment (credit)."""

    model_config = ConfigDict(from_attributes=True)

    entry_type: StatementEntryType
    date: datetime
    reference: str | None = None
    amount: Decimal
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    purchase_id: uuid.UUID | None = None
    payment_id: uuid.UUID | None = None
    invoice_number: str | None = None
    payment_method: PaymentMethod | None = None


class SupplierStatementResponse(BaseModel):
    """A page of a supplier's Khata, with the balance carried into it."""

    model_config = ConfigDict(from_attributes=True)

    supplier_id: uuid.UUID
    opening_balance: Decimal
    closing_balance: Decimal
    total_entries: int
    entries: list[StatementEntryResponse]


class RecordSupplierPaymentRequest(BaseModel):
    """Money paid by the shop to a supplier.

    `purchase_id` is optional: omit it for an unallocated settlement ("we paid
    Al-Madina Rs 5,000 off the account"), or name a purchase to allocate the
    payment to that invoice.
    """

    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    method: PaymentMethod
    purchase_id: uuid.UUID | None = None
    reference: str | None = Field(default=None, max_length=100)


class PaymentResponse(BaseModel):
    """A recorded `Payment` row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    supplier_id: uuid.UUID | None = None
    purchase_id: uuid.UUID | None = None
    amount: Decimal
    method: PaymentMethod
    reference: str | None = None
    created_at: datetime


class RecordSupplierPaymentResponse(BaseModel):
    """The created payment plus the supplier's resulting balance."""

    payment: PaymentResponse
    balance: SupplierBalanceResponse


class SupplierResponse(BaseModel):
    """A supplier record for the directory listing."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shop_id: uuid.UUID
    name: str
    phone: str | None = None
    address: str | None = None
    notes: str | None = None
    current_balance: float
    created_at: datetime


class CreateSupplierRequest(BaseModel):
    """Request to create a new supplier."""

    name: str
    phone: str | None = None
    address: str | None = None
    notes: str | None = None


class UpdateSupplierRequest(BaseModel):
    """Partial update for a supplier - only provided fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=500)