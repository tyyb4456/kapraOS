"""FastAPI application entrypoint.

This step only wires up app creation + the health endpoint. Business routers
(products, sales, inventory, ...) will be included here as they're built.
"""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
    )

    app.include_router(health_router)

    return app


app = create_app()