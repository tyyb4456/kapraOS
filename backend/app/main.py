"""FastAPI application entrypoint.

Routers are included here as their domains are built. So far only the health
check and the customer Khata (Step 6) are exposed; products, sales, purchases
and inventory remain service-layer only.
"""

from fastapi import FastAPI

from app.api.customers import router as customers_router
from app.api.health import router as health_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
    )

    app.include_router(health_router)
    app.include_router(customers_router)

    return app


app = create_app()