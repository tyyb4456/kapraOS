"""Authenticated tenant context for the AI layer.

The existing authentication model (``app.auth.auth``) remains
authoritative::

    Clerk token -> verify -> clerk_user_id -> User -> shop_id

The LLM must never decide ``shop_id``. Every AI entrypoint receives a
:class:`TenantContext` built server-side from the authenticated
application ``User``. Tools and subagents accept the context as an
explicit argument; none of them take ``shop_id`` as free LLM input.
"""

import uuid
from dataclasses import dataclass

from app.models.user import User


class TenantMismatchError(ValueError):
    """A tool was asked to act on a shop other than the authenticated one."""


@dataclass(frozen=True)
class TenantContext:
    """Server-derived tenant scope for one AI request.

    Frozen so downstream code cannot mutate the tenant mid-request.
    """

    shop_id: uuid.UUID
    user_id: uuid.UUID
    clerk_user_id: str | None = None

    def scope_label(self) -> str:
        """Short human-readable scope label for prompts and logs."""
        return f"shop_id={self.shop_id} user_id={self.user_id}"


def tenant_context_from_user(user: User) -> TenantContext:
    """Build the AI tenant context from the authenticated app user.

    This is the **only** legitimate way to create a :class:`TenantContext`.
    ``user.shop_id`` comes from ``get_current_shop_id()``; client-supplied
    headers, query params, body fields, and any LLM output are ignored.
    """
    return TenantContext(
        shop_id=user.shop_id,
        user_id=user.id,
        clerk_user_id=user.clerk_user_id,
    )


def assert_tenant_matches(context: TenantContext, shop_id: uuid.UUID) -> None:
    """Raise :class:`TenantMismatchError` unless ``shop_id`` is ours."""
    if shop_id != context.shop_id:
        raise TenantMismatchError(
            f"Refusing cross-tenant access: context shop {context.shop_id} "
            f"!= requested shop {shop_id}"
        )
