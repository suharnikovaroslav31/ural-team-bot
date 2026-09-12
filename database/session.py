from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import config
from database.models import Base

engine: AsyncEngine | None = None
SessionLocal: async_sessionmaker[AsyncSession] | None = None


def _ensure_sqlite_path(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    # sqlite+aiosqlite:///./data/bot.db
    raw = url.split(":///", 1)[-1]
    if raw.startswith("./"):
        path = config.ROOT / raw[2:]
    else:
        path = Path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> AsyncEngine:
    if engine is None:
        raise RuntimeError("Database engine is not initialized")
    return engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if SessionLocal is None:
        raise RuntimeError("Database session factory is not initialized")
    return SessionLocal


async def init_db() -> None:
    global engine, SessionLocal
    url = config.settings.sqlalchemy_url
    _ensure_sqlite_path(url)
    connect_args: dict[str, object] = {}
    if config.settings.is_sqlite():
        connect_args["check_same_thread"] = False
    engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)


def _migrate(sync_conn) -> None:
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    if "users" in tables:
        cols = {c["name"] for c in inspector.get_columns("users")}
        if "role" not in cols:
            sync_conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(16) DEFAULT 'employee'"))
        if "is_archived" not in cols:
            sync_conn.execute(text("ALTER TABLE users ADD COLUMN is_archived BOOLEAN DEFAULT 0"))
        if "is_employee" not in cols:
            sync_conn.execute(text("ALTER TABLE users ADD COLUMN is_employee BOOLEAN DEFAULT 0"))
        if "in_main_chat" not in cols:
            sync_conn.execute(text("ALTER TABLE users ADD COLUMN in_main_chat BOOLEAN DEFAULT 0"))
        sync_conn.execute(text("UPDATE users SET is_employee = 1 WHERE is_employee = 0 OR is_employee IS NULL"))
        sync_conn.execute(
            text(
                "UPDATE users SET status = 'Воркер' "
                "WHERE status IN ('Новый', 'Кандидат', 'Сотрудник')"
            )
        )
    if "task_reports" in tables:
        rcols = {c["name"] for c in inspector.get_columns("task_reports")}
        if "amount" not in rcols:
            sync_conn.execute(text("ALTER TABLE task_reports ADD COLUMN amount NUMERIC(18, 9)"))
    if "withdrawals" in tables and "payout_requests" in tables:
        count = sync_conn.execute(text("SELECT COUNT(*) FROM payout_requests")).scalar() or 0
        if count == 0:
            sync_conn.execute(
                text(
                    """
                    INSERT INTO payout_requests (user_id, amount, address, status, created_at, processed_at)
                    SELECT user_id, amount, address, status, created_at, processed_at FROM withdrawals
                    """
                )
            )
    if "deal_payouts" in tables and "task_reports" in tables:
        count = sync_conn.execute(text("SELECT COUNT(*) FROM task_reports")).scalar() or 0
        if count == 0:
            sync_conn.execute(
                text(
                    """
                    INSERT INTO task_reports (user_id, deal_number, file_ids, status, created_at)
                    SELECT user_id, deal_number, file_ids, status, created_at FROM deal_payouts
                    """
                )
            )


async def close_db() -> None:
    global engine, SessionLocal
    if engine is not None:
        await engine.dispose()
    engine = None
    SessionLocal = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
