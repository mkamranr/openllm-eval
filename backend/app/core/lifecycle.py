"""Application lifespan: create and dispose shared resources (DB engine, Redis client)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app import __version__
from app.adapters import registry as _adapter_registry  # noqa: F401  (registers adapters)
from app.adapters.base import AdapterContext
from app.auth.bootstrap import bootstrap_admin
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.network_policy import NetworkPolicy
from app.core.secrets import SecretResolver
from app.db.session import create_engine, create_session_factory

logger = get_logger(__name__)


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_connect_timeout_seconds,
        decode_responses=True,
    )


def create_adapter_context(settings: Settings) -> AdapterContext:
    return AdapterContext(
        policy=NetworkPolicy.from_settings(settings),
        secrets=SecretResolver(settings.secret_env_prefix),
        max_response_bytes=settings.endpoint_max_response_bytes,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # Tests may pre-populate these with fakes; only create what is missing.
    if getattr(app.state, "engine", None) is None:
        app.state.engine = create_engine(settings)
    if getattr(app.state, "redis", None) is None:
        app.state.redis = create_redis(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    if getattr(app.state, "adapter_context", None) is None:
        app.state.adapter_context = create_adapter_context(settings)
    policy = app.state.adapter_context.policy
    if policy.allow_private:
        logger.warning(
            "endpoint_private_networks_enabled",
            detail="Model endpoints may target private/loopback networks "
            "(ENDPOINT_ALLOW_PRIVATE_NETWORKS=true).",
        )

    await bootstrap_admin(app.state.session_factory, settings)

    logger.info(
        "app_startup",
        version=__version__,
        environment=settings.environment,
        auth_enabled=settings.auth_enabled,
    )
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()
        logger.info("app_shutdown")
