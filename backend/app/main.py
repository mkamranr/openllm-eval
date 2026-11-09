"""FastAPI application factory.

Run with ``uvicorn app.main:app`` (module-level instance) or
``uvicorn --factory app.main:create_app``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.lifecycle import lifespan
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="OpenLLM-Eval API",
        version=__version__,
        description="Repeatable evaluation and benchmarking of LLM endpoints.",
        lifespan=lifespan,
        # OpenAPI and docs are served locally; the Swagger/ReDoc pages are disabled because
        # they load JS from a CDN, which the offline requirement forbids.
        openapi_url="/api/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = None
    app.state.redis = None
    app.state.adapter_context = None

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
