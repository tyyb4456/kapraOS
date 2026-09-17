"""FastAPI application entrypoint.

Routers are included here as their domains are built. So far the health check,
the customer Khata (Step 6) and the supplier Khata (Step 7) are exposed;
products, sales, purchases and inventory remain service-layer only.
"""

from fastapi import FastAPI

from app.api.accounting import router as accounting_router
from app.api.auth import router as auth_router
from app.api.customers import router as customers_router
from app.api.expenses import router as expenses_router
from app.api.health import router as health_router
from app.api.reporting import router as reporting_router
from app.api.suppliers import router as suppliers_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(customers_router)
    app.include_router(suppliers_router)
    app.include_router(accounting_router)
    app.include_router(reporting_router)
    app.include_router(expenses_router)

    return app


app = create_app()