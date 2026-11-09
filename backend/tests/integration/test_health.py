from __future__ import annotations

import uuid

from app import __version__
from app.core.config import Settings
from app.execution.heartbeat import write_heartbeat
from tests.conftest import build_app


async def test_liveness(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_request_id_generated(client):
    response = await client.get("/api/v1/health")
    uuid.UUID(response.headers["X-Request-ID"])  # raises if not a UUID


async def test_request_id_propagated_when_safe(client):
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "ci-run.42_a"})
    assert response.headers["X-Request-ID"] == "ci-run.42_a"


async def test_request_id_replaced_when_unsafe(client):
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "bad id\nInjected"})
    assert response.headers["X-Request-ID"] != "bad id\nInjected"
    uuid.UUID(response.headers["X-Request-ID"])


async def test_version(client):
    response = await client.get("/api/v1/version")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == __version__
    assert body["api_version"] == "v1"
    assert body["name"] == "openllm-eval"


async def test_ready_ok_without_worker(client):
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["status"] == "ok"
    assert body["redis"]["status"] == "ok"
    # Worker presence is reported but does not gate readiness.
    assert body["worker"] == {
        "status": "unavailable",
        "active_workers": 0,
        "detail": "no worker heartbeat",
    }


async def test_ready_reports_live_worker(client, redis):
    await write_heartbeat(redis, "w1", "2026-11-09T00:00:00+00:00", ttl_seconds=30)
    response = await client.get("/api/v1/health/ready")
    assert response.json()["worker"]["status"] == "ok"
    assert response.json()["worker"]["active_workers"] == 1


async def test_ready_503_when_dependencies_down(client_for):
    # Port 1 on localhost refuses connections immediately.
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql://user:pw@127.0.0.1:1/none",
        redis_url="redis://127.0.0.1:1/0",
        db_connect_timeout_seconds=2,
        redis_connect_timeout_seconds=1,
    )
    app = build_app(settings)
    async for client in client_for(app):
        response = await client.get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["status"] == "error"
    assert body["redis"]["status"] == "error"
    assert body["worker"]["status"] == "unavailable"
    # Error details are exception class names only; no connection strings or credentials leak.
    assert "pw" not in response.text


async def test_openapi_served_locally(client):
    response = await client.get("/api/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert {"/api/v1/health", "/api/v1/health/ready", "/api/v1/version"} <= set(paths)


async def test_unknown_route_uses_error_envelope(client):
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["X-Request-ID"]
