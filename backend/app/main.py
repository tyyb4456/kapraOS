"""FastAPI application entrypoint.

Routers are included here as their domains are built. All domains
are now exposed via REST endpoints.
"""

from fastapi import FastAPI

from app.api.accounting import router as accounting_router
from app.api.auth import router as auth_router
from app.api.customers import router as customers_router
from app.api.expenses import router as expenses_router
from app.api.health import router as health_router
from app.api.inventory import router as inventory_router
from app.api.payments import router as payments_router
from app.api.products import router as products_router
from app.api.reporting import compat_router as reporting_compat_router
from app.api.reporting import router as reporting_router
from app.api.returns import router as returns_router
from app.api.sales import router as sales_router
from app.api.shops import router as shops_router
from app.api.suppliers import router as suppliers_router
from app.api.purchases import router as purchases_router
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
    app.include_router(reporting_compat_router)
    app.include_router(expenses_router)
    app.include_router(products_router)
    app.include_router(sales_router)
    app.include_router(purchases_router)
    app.include_router(payments_router)
    app.include_router(inventory_router)
    app.include_router(returns_router)
    app.include_router(shops_router)

    return app


app = create_app()
