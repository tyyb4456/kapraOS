"""Shared pytest fixtures.

Two things happen here, in order, and the order matters:

1. `DATABASE_URL` is pointed at a dedicated test database *before* any
   `app.*` module is imported. `app.database.session` builds its async
   engine once at import time from `Settings.database_url`, so if we
   imported `app` first and only then patched the env var, the app would
   keep talking to the dev database for the whole test session.

2. The schema is built once per test session by actually running the
   Alembic migration (`alembic upgrade head`) against that test database,
   rather than calling `Base.metadata.create_all()`. That means a broken
   migration fails the test suite the same way a broken model would -
   which is the "migration is valid" check called for in the spec.
"""

import os
import pathlib
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

TEST_DATABASE_URL = os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://kapraos_app:changeme@localhost:5432/kapraos_test",
)
# Must happen before the first `import app...` anywhere in the test session.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
import app.database.session as session_module
from app.database.session import get_db
from app.main import app

# Use NullPool during tests so asyncpg connections are not pooled across
# distinct event loops created by pytest-asyncio for each test.
session_module.engine = create_async_engine(
    TEST_DATABASE_URL,
    poolclass=NullPool,
    future=True,
)
session_module.AsyncSessionLocal = async_sessionmaker(
    bind=session_module.engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
AsyncSessionLocal = session_module.AsyncSessionLocal

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


@pytest.fixture(scope="session", autouse=True)
def apply_migrations() -> Generator[None, None, None]:
    """Build the schema via the real migration once, tear it down once.

    Session-scoped and autouse: every test in the suite runs against a
    schema that Alembic itself produced.
    """

    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    return _alembic_config()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A session-per-test, rolled back afterwards so tests stay isolated."""

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """An httpx client wired directly to the FastAPI app (no network)."""

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def api_session() -> AsyncGenerator[AsyncSession, None]:
    """A session bound to an outer transaction that is always rolled back.

    Endpoint tests need two things at once: fixture rows the request can see,
    and isolation even though the route under test calls `session.commit()`.
    Binding the session to a connection that already has a transaction open
    (with `join_transaction_mode="create_savepoint"`) gives both - the route's
    commit only releases a savepoint, and rolling back the outer transaction at
    the end discards everything the test wrote.
    """

    async with session_module.engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
            autoflush=False,
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


@pytest_asyncio.fixture
async def api_client(api_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An httpx client whose requests run inside `api_session`.

    Overriding `get_db` (rather than letting the app open its own session) is
    what lets a test create a shop/customer/sale and have the very next HTTP
    request see them.
    """

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield api_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)