"""Redis connection lifecycle (Step 10).

One shared async client for the whole process. Fail-open by design:
every helper here returns ``None``/``False`` instead of raising when
Redis is unconfigured, unreachable, or the ``redis`` package is missing,
so a Redis outage can never break sales, purchases, payments or reads
(PostgreSQL remains authoritative).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import get_settings

logger = logging.getLogger("app.cache")

_client: Any | None = None
_client_lock = threading.Lock()
_client_url: str | None = None


def is_redis_configured() -> bool:
    """True when ``REDIS_URL`` is set (and caching is enabled)."""
    try:
        settings = get_settings()
    except Exception:  # noqa: BLE001 — fail open when settings break
        return False
    return bool(getattr(settings, "cache_enabled", True)) and bool(
        (getattr(settings, "redis_url", "") or "").strip()
    )


def _build_client(url: str) -> Any | None:
    try:
        import redis.asyncio as redis_async
    except ImportError:
        logger.warning("cache redis package missing; running without Redis")
        return None
    try:
        return redis_async.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    except Exception as exc:  # noqa: BLE001 — fail open
        logger.warning("cache redis connect failed: %s", type(exc).__name__)
        return None


def get_redis() -> Any | None:
    """Return the shared async Redis client, or ``None`` when unavailable.

    ``None`` means "no Redis": callers must fall back to PostgreSQL (for
    reads) or skip the write (for SET/invalidation). Never raises.
    """
    global _client, _client_url
    if not is_redis_configured():
        return None
    try:
        url = (get_settings().redis_url or "").strip()
    except Exception:  # noqa: BLE001 — fail open
        return None
    if _client is not None and _client_url == url:
        return _client
    with _client_lock:
        if _client is not None and _client_url == url:
            return _client
        # Drop a stale client when the URL changed (tests reconfigure it).
        old, _client, _client_url = _client, None, None
        if old is not None:
            try:
                close_fn = getattr(old, "aclose", None)
                if close_fn is not None:
                    # Synchronous context: best-effort scheduling is the
                    # caller's job; just drop the reference here.
                    pass
            except Exception:  # noqa: BLE001, S110 — fail open
                pass
        _client = _build_client(url)
        _client_url = url
        return _client


async def close_redis() -> None:
    """Close the shared client (app shutdown). Never raises."""
    global _client, _client_url
    client, _client, _client_url = _client, None, None
    if client is None:
        return
    try:
        await client.aclose()
    except Exception:
        logger.debug("cache redis close failed", exc_info=True)


async def ping_redis() -> bool:
    """True when Redis answers PING. Used by health checks/tests only."""
    client = get_redis()
    if client is None:
        return False
    try:
        pong = await client.ping()
        return bool(pong)
    except Exception:  # noqa: BLE001 — fail open
        return False


__all__ = ["close_redis", "get_redis", "is_redis_configured", "ping_redis"]
