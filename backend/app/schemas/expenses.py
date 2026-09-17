"""Request/response schemas for the Step 10 expense endpoints.

The service layer returns ORM `Expense` rows and plain frozen dataclasses; these
models are the API's wire contract only, so the domain never imports Pydantic.
Money is `Decimal`, which serialises as a JSON string and keeps NUMERIC(14,2)
exact for clients.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.expense import ExpenseCategory
from app.models.payment import PaymentMethod


class CreateExpenseRequest(BaseModel):
    """An immediate-payment operating expense.

    `shop_id` is deliberately absent: it always comes from the authenticated
    tenant server-side (`app.api.dependencies`), never from the request body.
    """

    category: ExpenseCategory
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    payment_method: PaymentMethod
    description: str | None = Field(default=None, max_length=300)
    expense_date: datetime | None = None


class ExpenseResponse(BaseModel):
    """A recorded expense row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shop_id: uuid.UUID
    category: ExpenseCategory
    description: str | None = None
    amount: Decimal
    payment_method: PaymentMethod
    expense_date: datetime
    created_at: datetime
    updated_at: datetime