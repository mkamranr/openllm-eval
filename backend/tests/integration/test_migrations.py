"""Migrations apply cleanly, match the ORM models, and fully reverse."""

from __future__ import annotations

import asyncio

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import normalize_database_url
from app.db.base import Base
from tests.conftest import BACKEND_ROOT


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "app" / "db" / "migrations"))
    cfg.attributes["database_url"] = database_url
    cfg.attributes["configure_logger"] = False
    return cfg


async def _inspect(database_url: str):
    engine = create_async_engine(normalize_database_url(database_url))
    try:
        async with engine.connect() as conn:

            def collect(sync_conn):
                tables = set(inspect(sync_conn).get_table_names())
                ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
                diff = compare_metadata(ctx, Base.metadata)
                return tables, diff

            return await conn.run_sync(collect)
    finally:
        await engine.dispose()


def test_upgrade_matches_models_and_downgrade_reverses(database_url):
    cfg = _alembic_config(database_url)

    command.upgrade(cfg, "head")
    tables, diff = asyncio.run(_inspect(database_url))
    assert "projects" in tables
    assert "alembic_version" in tables
    assert diff == [], f"ORM models and migrations have drifted: {diff}"

    command.downgrade(cfg, "base")
    tables, _ = asyncio.run(_inspect(database_url))
    assert "projects" not in tables


def test_single_head():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    assert len(script.get_heads()) == 1
