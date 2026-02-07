from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database.models import Base, BotRun
from database.repositories.bot_run_repository import BotRunRepository


@pytest.mark.asyncio
async def test_update_bot_run_running_updates_latest_created_row_without_crashing():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    bot_name = "bot-A"
    now = datetime(2026, 2, 7, tzinfo=timezone.utc)

    async with Session() as session:
        session.add_all([
            BotRun(
                bot_name=bot_name,
                instance_name=bot_name,
                strategy_type="controller",
                strategy_name="clmm",
                account_name="acc",
                deployed_at=now - timedelta(minutes=5),
                deployment_status="DEPLOYED",
                run_status="CREATED",
            ),
            BotRun(
                bot_name=bot_name,
                instance_name=bot_name,
                strategy_type="controller",
                strategy_name="clmm",
                account_name="acc",
                deployed_at=now,
                deployment_status="DEPLOYED",
                run_status="CREATED",
            ),
        ])
        await session.commit()

        repo = BotRunRepository(session)
        updated = await repo.update_bot_run_running(bot_name)

        assert updated is not None
        assert updated.run_status == "RUNNING"
        # SQLite does not preserve tzinfo even if the column is timezone-aware.
        assert updated.deployed_at == now.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_mark_orphan_as_stopped_updates_latest_created_row_without_crashing():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    bot_name = "bot-B"
    now = datetime(2026, 2, 7, tzinfo=timezone.utc)

    async with Session() as session:
        session.add_all([
            BotRun(
                bot_name=bot_name,
                instance_name=bot_name,
                strategy_type="controller",
                strategy_name="clmm",
                account_name="acc",
                deployed_at=now - timedelta(minutes=5),
                deployment_status="DEPLOYED",
                run_status="CREATED",
            ),
            BotRun(
                bot_name=bot_name,
                instance_name=bot_name,
                strategy_type="controller",
                strategy_name="clmm",
                account_name="acc",
                deployed_at=now,
                deployment_status="DEPLOYED",
                run_status="CREATED",
            ),
        ])
        await session.commit()

        repo = BotRunRepository(session)
        updated = await repo.mark_orphan_as_stopped(bot_name)

        assert updated is not None
        assert updated.run_status == "STOPPED"
        # SQLite does not preserve tzinfo even if the column is timezone-aware.
        assert updated.deployed_at == now.replace(tzinfo=None)
