"""Request/response schemas for the accounting endpoints.

The service layer returns plain frozen dataclasses; these models are built from
them with `model_validate()`, so the domain never imports Pydantic. Money is
`Decimal` throughout, which Pydantic serialises as a JSON string so
NUMERIC(14,2) values stay exact for clients.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.account import AccountType


class AccountResponse(BaseModel):
    """One account in the shop's chart of accounts."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    account_type: AccountType
    is_system: bool
    is_active: bool


class AccountBalanceResponse(BaseModel):
    """An account's derived balance, with the totals it came from."""

    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    code: str
    name: str
    account_type: AccountType
    debit_total: Decimal
    credit_total: Decimal
    balance: Decimal


class LedgerEntryResponse(BaseModel):
    """One ledger line, with its running balance."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    date: datetime
    description: str | None = None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    reference_type: str
    reference_id: uuid.UUID


class AccountLedgerResponse(BaseModel):
    """A page of one account's ledger."""

    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    opening_balance: Decimal
    closing_balance: Decimal
    total_entries: int
    entries: list[LedgerEntryResponse]