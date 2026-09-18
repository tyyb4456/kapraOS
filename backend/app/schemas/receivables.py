"""Request/response schemas for the customer Khata endpoints.

These are the API's wire contract only. The service layer
(`app.services.receivables`) returns plain frozen dataclasses, and these models
are built from them with `model_validate()` - so the domain never has to import
Pydantic and the HTTP shape can change independently of it.

Money is `Decimal` all the way out to the response; Pydantic serialises it as a
JSON string, which keeps NUMERIC(14,2) values exact for clients.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.customer import Customer
from app.models.payment import PaymentMethod
from app.services.receivables import StatementEntryType


class CustomerBalanceResponse(BaseModel):
    """What a customer owes, and the figures it was derived from."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    total_sales: Decimal
    total_payments: Decimal
    outstanding_balance: Decimal
    number_of_sales: int
    number_of_payments: int
    last_sale_at: datetime | None = None
    last_payment_at: datetime | None = None


class CustomerSummaryResponse(BaseModel):
    """Dashboard summary: purchases, paid, outstanding."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    name: str
    phone: str | None = None
    total_purchases: Decimal
    total_paid: Decimal
    outstanding_balance: Decimal


class StatementEntryResponse(BaseModel):
    """One Khata line: a sale (debit) or a payment (credit)."""

    model_config = ConfigDict(from_attributes=True)

    entry_type: StatementEntryType
    date: datetime
    reference: str | None = None
    amount: Decimal
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    sale_id: uuid.UUID | None = None
    payment_id: uuid.UUID | None = None
    invoice_number: str | None = None
    payment_method: PaymentMethod | None = None


class CustomerStatementResponse(BaseModel):
    """A page of a customer's Khata, with the balance carried into it."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    opening_balance: Decimal
    closing_balance: Decimal
    total_entries: int
    entries: list[StatementEntryResponse]


class RecordCustomerPaymentRequest(BaseModel):
    """Money received from a customer.

    `sale_id` is optional: omit it for an unallocated settlement ("Ahmed paid
    Rs 5,000"), or name a sale to allocate the payment to that invoice.
    """

    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    method: PaymentMethod
    sale_id: uuid.UUID | None = None
    reference: str | None = Field(default=None, max_length=100)


class PaymentResponse(BaseModel):
    """A recorded `Payment` row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID | None = None
    sale_id: uuid.UUID | None = None
    amount: Decimal
    method: PaymentMethod
    reference: str | None = None
    created_at: datetime


class RecordCustomerPaymentResponse(BaseModel):
    """The created payment plus the customer's resulting balance."""

    payment: PaymentResponse
    balance: CustomerBalanceResponse


class CustomerResponse(BaseModel):
    """A customer record for the directory listing."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shop_id: uuid.UUID
    name: str
    phone: str | None = None
    email: str | None = None
    current_balance: float
    credit_limit: float | None = None
    created_at: datetime


class CreateCustomerRequest(BaseModel):
    """Request to create a new customer."""

    name: str
    phone: str | None = None
    email: str | None = None
    credit_limit: float | None = None