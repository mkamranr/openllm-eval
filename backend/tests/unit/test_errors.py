from __future__ import annotations

from fastapi import Query

from app.core.exceptions import AppError, NotFoundError
from tests.conftest import build_app


async def test_error_envelopes(settings, redis, client_for):
    app = build_app(settings, redis)

    @app.get("/_test/not-found")
    async def _nf():
        raise NotFoundError("Project not found", details={"id": "x"})

    @app.get("/_test/conflict")
    async def _conflict():
        raise AppError("Already exists", code="conflict", status_code=409)

    @app.get("/_test/validate")
    async def _validate(limit: int = Query(..., ge=1)):
        return {"limit": limit}

    @app.get("/_test/boom")
    async def _boom():
        raise RuntimeError("secret internal detail")

    async for client in client_for(app):
        nf = await client.get("/_test/not-found")
        conflict = await client.get("/_test/conflict")
        invalid = await client.get("/_test/validate", params={"limit": "s3cret-value"})
        # Exceptions raised past the error middleware are re-raised by Starlette's test transport
        # after the 500 response is sent, so ask the transport not to re-raise.
        client._transport.raise_app_exceptions = False  # type: ignore[attr-defined]
        boom = await client.get("/_test/boom")

    assert nf.status_code == 404
    assert nf.json()["error"]["code"] == "not_found"
    assert nf.json()["error"]["details"] == {"id": "x"}
    assert nf.json()["error"]["request_id"] == nf.headers["X-Request-ID"]

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert "s3cret-value" not in invalid.text  # submitted input is not echoed back

    assert boom.status_code == 500
    assert boom.json()["error"] == {
        "code": "internal_error",
        "message": "Internal server error",
        "request_id": boom.json()["error"]["request_id"],
        "details": None,
    }
    assert "secret internal detail" not in boom.text
