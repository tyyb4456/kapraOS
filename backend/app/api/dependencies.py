"""Shared FastAPI dependencies.

`db_arch.md` section 33 is the rule this module exists to protect: **never
trust a `shop_id` that arrives in a request body or path**. Every tenant-scoped
route takes its shop from here and nowhere else.

Clerk handles authentication. The `get_current_shop_id()` dependency derives
`shop_id` server-side from the verified Clerk token via `app.auth.auth`:

    Clerk token -> verify -> clerk_user_id -> User -> shop_id

No client-provided header, query param, or body field may select the tenant.
"""

import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import CurrentUser, ShopId as AuthShopId
from app.database import get_db

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = CurrentUser
ShopId = AuthShopId