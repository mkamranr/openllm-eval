"""Shared fixtures.

By default tests use a temporary SQLite database and an in-memory fake Redis, so the suite runs
with no external services. Set ``TEST_DATABASE_URL`` and/or ``TEST_REDIS_URL`` (as CI does) to run
the same tests against real PostgreSQL and Redis.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import fakeredis
import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError

from app.adapters.base import AdapterContext
from app.core.config import Settings
from app.core.lifecycle import create_redis
from app.core.network_policy import NetworkPolicy
from app.core.secrets import SecretResolver
from app.main import create_app
from app.testing.mock_openai import create_mock_app

BACKEND_ROOT = Path(__file__).resolve().parents[1]

MOCK_API_KEY = "sk-mock-test-key-7f3a9c0d1e2b"
MOCK_SECRET_REF = "env:OPENLLM_SECRET_MOCK_KEY"
TEST_ENVIRON = {"OPENLLM_SECRET_MOCK_KEY": MOCK_API_KEY}
TEST_AUTH_SECRET_KEY = "test-auth-secret-key-0123456789"


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        redis_url=os.environ.get("TEST_REDIS_URL") or "redis://127.0.0.1:6379/15",
        log_format="json",
        log_level="INFO",
        db_connect_timeout_seconds=2,
        redis_connect_timeout_seconds=1,
        # Tests talk to a mock model server on 127.0.0.1.
        endpoint_allow_private_networks=True,
        auth_enabled=True,
        auth_secret_key=TEST_AUTH_SECRET_KEY,
    )


def make_adapter_context(
    settings: Settings,
    environ: dict[str, str] | None = None,
    transport=None,
    resolver=None,
) -> AdapterContext:
    return AdapterContext(
        policy=NetworkPolicy.from_settings(settings, resolver=resolver),
        secrets=SecretResolver(
            settings.secret_env_prefix, TEST_ENVIRON if environ is None else environ
        ),
        max_response_bytes=settings.endpoint_max_response_bytes,
        transport=transport,
    )


@pytest.fixture
def adapter_context(settings: Settings) -> AdapterContext:
    return make_adapter_context(settings)


class ServerThread:
    """Runs an ASGI app with uvicorn on a free localhost port in a background thread.

    A real socket is required for streaming tests: httpx's ASGITransport buffers whole
    responses, which would hide time-to-first-token.
    """

    def __init__(self, app: FastAPI) -> None:
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, log_config=None, log_level="warning", lifespan="off"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.port = 0

    def __enter__(self) -> ServerThread:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("mock server failed to start")
            time.sleep(0.01)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


@pytest.fixture(scope="session")
def mock_server() -> Iterator[ServerThread]:
    """Mock OpenAI-compatible server that requires MOCK_API_KEY."""
    with ServerThread(
        create_mock_app(models=["mock-model", "other-model"], api_key=MOCK_API_KEY)
    ) as server:
        yield server


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    if os.environ.get("TEST_REDIS_URL"):
        client = create_redis(settings)
        await client.flushdb()
    else:
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield client
    await client.flushdb()
    await client.aclose()


def build_app(
    settings: Settings,
    redis: Redis | None = None,
    adapter_context: AdapterContext | None = None,
) -> FastAPI:
    app = create_app(settings)
    if redis is not None:
        app.state.redis = redis
    app.state.adapter_context = adapter_context
    return app


async def create_test_user(
    app: FastAPI,
    username: str,
    role: str = "admin",
    *,
    password: str | None = None,
    must_change_password: bool = False,
    source: str = "local",
) -> tuple[str, str]:
    """Create a user directly in the database and return (user id, API token)."""
    from app.auth.passwords import hash_password
    from app.db.models.auth import User
    from app.services.auth_service import issue_token

    async with app.state.session_factory() as session:
        user = User(
            username=username,
            source=source,
            password_hash=hash_password(password) if password else None,
            role=role,
            role_pinned=False,
            must_change_password=must_change_password,
            disabled=False,
            groups=[],
        )
        session.add(user)
        await session.flush()
        created = await issue_token(session, user, "tests", None)
        await session.commit()
        return str(user.id), created.token


async def _client_for(app: FastAPI, auth: bool = True) -> AsyncIterator[httpx.AsyncClient]:
    """Client around ``app``. With ``auth`` (default), requests carry an administrator's API
    token whenever the database has been migrated, so tests written before sign-in existed
    keep working."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        headers = {}
        if auth and app.state.settings.auth_enabled:
            try:
                _, token = await create_test_user(app, f"test-admin-{uuid.uuid4().hex[:8]}")
                headers["Authorization"] = f"Bearer {token}"
            except SQLAlchemyError:
                pass  # schema not migrated: only public routes are used
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as client:
            yield client


@pytest.fixture
async def app(settings: Settings, redis: Redis, adapter_context: AdapterContext) -> FastAPI:
    return build_app(settings, redis, adapter_context)


@pytest.fixture
async def migrated_app(app: FastAPI, database_url: str) -> FastAPI:
    """App whose database schema has been created via Alembic migrations."""
    from alembic import command

    from tests.integration.test_migrations import _alembic_config

    await asyncio.to_thread(command.upgrade, _alembic_config(database_url), "head")
    yield app
    await asyncio.to_thread(command.downgrade, _alembic_config(database_url), "base")


@pytest.fixture
async def api(migrated_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """API client backed by a fully migrated database."""
    async for c in _client_for(migrated_app):
        yield c


@pytest.fixture
async def anon(migrated_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Unauthenticated client backed by a fully migrated database."""
    async for c in _client_for(migrated_app, auth=False):
        yield c


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client_for(app):
        yield c


@pytest.fixture
def client_for():
    """Factory for clients around a custom app (e.g. one with unreachable dependencies)."""
    return _client_for


@pytest.fixture
def rendered_logs(app: FastAPI) -> Iterator[io.StringIO]:
    """Every log line exactly as the app renders it (after redaction), captured in memory."""
    root = logging.getLogger()
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(root.handlers[0].formatter)
    root.addHandler(handler)
    yield buffer
    root.removeHandler(handler)
