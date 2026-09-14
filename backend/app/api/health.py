"""Liveness/health endpoint.

Deliberately dependency-free: it should answer even if the database is
unreachable, so it's useful as a basic process liveness check. A separate
`/health/db` (or readiness probe) can be added in a later step if needed.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}