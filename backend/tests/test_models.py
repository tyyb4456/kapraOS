"""Model-level tests: Shop creation, the Shop<->User relationship, the
tenant-isolation foreign key/cascade behaviour, and a migration-drift check.
"""

import uuid

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.models import Shop, User, UserRole


@pytest.mark.asyncio
async def test_shop_can_be_created(db_session: AsyncSession) -> None:
    shop = Shop(name="Ahmed Fabrics", phone="0300-1234567")
    db_session.add(shop)
    await db_session.flush()

    assert isinstance(shop.id, uuid.UUID)
    assert shop.currency == "PKR"
    assert shop.created_at is not None
    assert shop.updated_at is not None


@pytest.mark.asyncio
async def test_user_belongs_to_a_shop(db_session: AsyncSession) -> None:
    shop = Shop(name="Bilal Textiles")
    db_session.add(shop)
    await db_session.flush()

    user = User(
        shop_id=shop.id,
        name="Bilal",
        email="bilal@example.com",
        role=UserRole.OWNER,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user, attribute_names=["shop"])

    assert user.shop_id == shop.id
    assert user.shop.name == "Bilal Textiles"
    assert user.role is UserRole.OWNER


@pytest.mark.asyncio
async def test_user_role_defaults_to_staff(db_session: AsyncSession) -> None:
    shop = Shop(name="Default Role Shop")
    db_session.add(shop)
    await db_session.flush()

    user = User(
        shop_id=shop.id,
        name="New Hire",
        email="new-hire@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    assert user.role is UserRole.STAFF


@pytest.mark.asyncio
async def test_duplicate_email_is_rejected(db_session: AsyncSession) -> None:
    shop = Shop(name="Uniqueness Test Shop")
    db_session.add(shop)
    await db_session.flush()

    db_session.add(
        User(
            shop_id=shop.id,
            name="First",
            email="dupe@example.com",
        )
    )
    await db_session.flush()

    db_session.add(
        User(
            shop_id=shop.id,
            name="Second",
            email="dupe@example.com",
        )
    )

    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_deleting_shop_cascades_to_users(db_session: AsyncSession) -> None:
    shop = Shop(name="Cascade Test Shop")
    db_session.add(shop)
    await db_session.flush()

    user = User(
        shop_id=shop.id,
        name="Will Be Deleted",
        email="cascade@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    user_id = user.id

    await db_session.delete(shop)
    await db_session.flush()

    result = await db_session.execute(sa.select(User).where(User.id == user_id))
    assert result.scalar_one_or_none() is None


def test_migration_matches_models(alembic_config: Config) -> None:
    """Fails if the models drift from the checked-in migration.

    `apply_migrations` (session-scoped, autouse) has already run
    `upgrade head` against the test database by the time this executes, so
    this is comparing the *actual* migrated schema to `Base.metadata` -
    exactly what you'd want caught before merging a model change without a
    matching migration.
    """

    command.check(alembic_config)