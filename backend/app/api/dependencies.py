"""Shared FastAPI dependencies.

`db_arch.md` section 33 is the rule this module exists to protect: **never
trust a `shop_id` that arrives in a request body or path**. Every tenant-scoped
route takes its shop from here and nowhere else.

Authentication (JWT, password hashing) is still out of scope - it was deferred
in Step 1 and Step 6 must not invent it. So `get_current_shop_id()` currently
reads an `X-Shop-Id` header, which is a **development placeholder, not a
security boundary**: any caller can set it. It is deliberately the single choke
point, so when auth lands the change is one function body:

    JWT -> user_id -> user.shop_id

and every route that depends on `ShopId` becomes properly authorized without
being touched.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db


async def get_current_shop_id(
    x_shop_id: Annotated[
        uuid.UUID | None,
        Header(description="Tenant the request acts on (placeholder for JWT auth)."),
    ] = None,
) -> uuid.UUID:
    """Resolve the shop this request acts on.

    Raises 401 when the header is absent, so an un-scoped request can never
    fall through to a query that forgot to filter by tenant.
    """

    if x_shop_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Shop-Id header",
        )
    return x_shop_id


#: Request-scoped async session. Routes are responsible for committing.
DbSession = Annotated[AsyncSession, Depends(get_db)]

#: The authenticated tenant. Always server-resolved, never client-supplied.
ShopId = Annotated[uuid.UUID, Depends(get_current_shop_id)]