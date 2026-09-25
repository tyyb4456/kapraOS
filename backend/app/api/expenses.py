"""Expense endpoints.

    POST /expenses          record an immediate-payment expense
    GET  /expenses          list them, filtered by date
    GET  /expenses/{id}     read one
    PATCH /expenses/{id}    edit description/category/amount/method/date
                            (financial changes repost the EXPENSE ledger group)
    DELETE /expenses/{id}   void an expense and drop its ledger posting

The shop is always resolved server-side (see `app.api.dependencies`), so an
expense id from another tenant returns 404 rather than reading anything, and the
request body can never choose a shop. Domain errors are translated here and
nowhere else.

`get_db` does not commit, so the write routes commit explicitly.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.dependencies import DbSession, ShopId
from app.schemas.expenses import CreateExpenseRequest, ExpenseResponse, UpdateExpenseRequest
from app.services import expenses as expenses_service
from app.services.expenses import (
    ExpenseNotFoundError,
    InvalidExpenseAmountError,
    InvalidExpenseCategoryError,
    InvalidExpensePaymentMethodError,
    InvalidPaginationError,
    InvalidStatementRangeError,
    ShopNotFoundError,
)

router = APIRouter(prefix="/expenses", tags=["expenses"])

ExpenseId = Annotated[uuid.UUID, Path(description="Expense to read.")]


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


@router.post(
    "",
    response_model=ExpenseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an expense",
)
async def create_expense(
    shop_id: ShopId,
    db: DbSession,
    body: CreateExpenseRequest,
) -> ExpenseResponse:
    try:
        expense = await expenses_service.create_expense(
            db,
            shop_id=shop_id,
            category=body.category,
            amount=body.amount,
            payment_method=body.payment_method,
            description=body.description,
            expense_date=body.expense_date,
        )
    except ShopNotFoundError as exc:
        raise _not_found(exc) from exc
    except (
        InvalidExpenseAmountError,
        InvalidExpenseCategoryError,
        InvalidExpensePaymentMethodError,
    ) as exc:
        raise _unprocessable(exc) from exc

    response = ExpenseResponse.model_validate(expense)
    await db.commit()
    return response


@router.get(
    "",
    response_model=list[ExpenseResponse],
    summary="List expenses",
)
async def list_expenses(
    shop_id: ShopId,
    db: DbSession,
    start_date: Annotated[
        datetime | None, Query(description="Inclusive lower bound on expense date.")
    ] = None,
    end_date: Annotated[
        datetime | None, Query(description="Inclusive upper bound on expense date.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ExpenseResponse]:
    try:
        page = await expenses_service.list_expenses(
            db,
            shop_id=shop_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
    except (InvalidStatementRangeError, InvalidPaginationError) as exc:
        raise _unprocessable(exc) from exc
    return [ExpenseResponse.model_validate(row) for row in page.expenses]


@router.get(
    "/{expense_id}",
    response_model=ExpenseResponse,
    summary="Read an expense",
)
async def read_expense(
    expense_id: ExpenseId,
    shop_id: ShopId,
    db: DbSession,
) -> ExpenseResponse:
    try:
        expense = await expenses_service.get_expense(
            db, shop_id=shop_id, expense_id=expense_id
        )
    except ExpenseNotFoundError as exc:
        raise _not_found(exc) from exc
    return ExpenseResponse.model_validate(expense)


@router.patch(
    "/{expense_id}",
    response_model=ExpenseResponse,
    summary="Edit an expense",
)
async def update_expense(
    expense_id: ExpenseId,
    shop_id: ShopId,
    db: DbSession,
    body: UpdateExpenseRequest,
) -> ExpenseResponse:
    fields_set = body.model_fields_set
    description_sentinel: object = ...
    if "description" in fields_set:
        description_sentinel = body.description
    try:
        expense = await expenses_service.update_expense(
            db,
            shop_id=shop_id,
            expense_id=expense_id,
            category=body.category,
            amount=body.amount,
            payment_method=body.payment_method,
            description=description_sentinel,
            expense_date=body.expense_date,
        )
    except ExpenseNotFoundError as exc:
        raise _not_found(exc) from exc
    except (
        InvalidExpenseAmountError,
        InvalidExpenseCategoryError,
        InvalidExpensePaymentMethodError,
    ) as exc:
        raise _unprocessable(exc) from exc
    await db.commit()
    await db.refresh(expense)
    return ExpenseResponse.model_validate(expense)


@router.delete(
    "/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Void an expense",
)
async def delete_expense(
    expense_id: ExpenseId,
    shop_id: ShopId,
    db: DbSession,
) -> None:
    try:
        await expenses_service.delete_expense(
            db, shop_id=shop_id, expense_id=expense_id
        )
    except ExpenseNotFoundError as exc:
        raise _not_found(exc) from exc
    await db.commit()