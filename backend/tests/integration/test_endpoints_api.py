"""Endpoint registry API, end to end against the mock model server (milestone 1 acceptance)."""

from __future__ import annotations

import uuid

from tests.conftest import MOCK_API_KEY, MOCK_SECRET_REF, build_app, make_adapter_context


def vllm_payload(mock_server, **overrides) -> dict:
    payload = {
        "name": "local-vllm",
        "adapter_type": "openai_compatible",
        "base_url": mock_server.base_url,
        "model_identifier": "mock-model",
        "secret_reference": MOCK_SECRET_REF,
        "request_configuration": {
            "temperature": 0,
            "max_tokens": 64,
            "stream": True,
            "headers": {"X-Mock-First-Token-Delay-Ms": "50"},
        },
        "deployment_metadata": {"backend": "vllm", "gpu": "RTX 4090", "quantization": "unknown"},
        "tags": ["local", "gpu"],
    }
    payload.update(overrides)
    return payload


async def test_register_test_and_inspect_endpoint(api, mock_server, rendered_logs):
    created = await api.post("/api/v1/endpoints", json=vllm_payload(mock_server))
    assert created.status_code == 201, created.text
    ep = created.json()
    assert ep["secret_reference"] == MOCK_SECRET_REF
    assert ep["secret_configured"] is True
    assert ep["request_configuration"]["verify_tls"] is True  # defaults filled in
    assert ep["last_test_status"] is None
    assert ep["warnings"] == ["Credentials will be sent over unencrypted HTTP."]

    test = await api.post(f"/api/v1/endpoints/{ep['id']}/test", json={"prompt": "say ok"})
    assert test.status_code == 200, test.text
    result = test.json()
    assert result["ok"] is True
    assert result["models_check"]["model_listed"] is True
    resp = result["response"]
    assert resp["content"] == "Mock response to: say ok"
    assert resp["timing"]["streamed"] is True
    assert resp["timing"]["ttft_ms"] >= 30  # mock first-token delay 50 ms
    assert resp["timing"]["first_content_at"] and resp["timing"]["ended_at"]
    assert resp["usage"]["source"] == "server_reported"
    assert result["network_policy"]["allow_private_networks"] is True

    refreshed = (await api.get(f"/api/v1/endpoints/{ep['id']}")).json()
    assert refreshed["last_test_status"] == "ok"
    assert refreshed["last_test_summary"]["ttft_ms"] == resp["timing"]["ttft_ms"]

    nonstream = (
        await api.post(
            f"/api/v1/endpoints/{ep['id']}/test", json={"stream": False, "check_models": False}
        )
    ).json()
    assert nonstream["ok"] and nonstream["models_check"] is None
    assert nonstream["response"]["timing"]["ttft_ms"] is None

    meta = (await api.get(f"/api/v1/endpoints/{ep['id']}/metadata")).json()
    assert meta["verified"] is False and meta["model_entry"]["id"] == "mock-model"

    # Acceptance: secrets never appear in API responses or logs.
    for body in (created.text, test.text, str(refreshed), str(meta)):
        assert MOCK_API_KEY not in body
    logs = rendered_logs.getvalue()
    assert "model_request" in logs  # the adapter did log the call...
    assert MOCK_API_KEY not in logs  # ...without the secret


async def test_failed_test_is_recorded(api, mock_server):
    ep = (
        await api.post(
            "/api/v1/endpoints",
            json=vllm_payload(
                mock_server, request_configuration={"headers": {"X-Mock-Status": "500"}}
            ),
        )
    ).json()
    result = (await api.post(f"/api/v1/endpoints/{ep['id']}/test", json={})).json()
    assert result["ok"] is False
    assert result["response"]["error"]["type"] == "http_status"
    assert result["response"]["error"]["status_code"] == 500
    refreshed = (await api.get(f"/api/v1/endpoints/{ep['id']}")).json()
    assert refreshed["last_test_status"] == "failed"
    assert refreshed["last_test_summary"]["error_type"] == "http_status"


async def test_missing_secret_is_reported_not_leaked(
    settings, redis, mock_server, client_for, migrated_app
):
    migrated_app.state.adapter_context = make_adapter_context(settings, environ={})
    async for api in client_for(migrated_app):
        ep = (await api.post("/api/v1/endpoints", json=vllm_payload(mock_server))).json()
        assert ep["secret_configured"] is False
        assert any("is not set" in w for w in ep["warnings"])
        result = (await api.post(f"/api/v1/endpoints/{ep['id']}/test", json={})).json()
    assert result["response"]["error"]["type"] == "secret_unavailable"


async def test_crud_clone_enable_disable(api, mock_server):
    ep = (await api.post("/api/v1/endpoints", json=vllm_payload(mock_server))).json()
    eid = ep["id"]

    dup = await api.post("/api/v1/endpoints", json=vllm_payload(mock_server))
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "conflict"

    clone = await api.post(f"/api/v1/endpoints/{eid}/clone")
    assert clone.status_code == 201
    assert clone.json()["name"] == "local-vllm (copy)"
    assert clone.json()["deployment_metadata"] == ep["deployment_metadata"]
    clone2 = (await api.post(f"/api/v1/endpoints/{eid}/clone")).json()
    assert clone2["name"] == "local-vllm (copy) 2"
    named = (await api.post(f"/api/v1/endpoints/{eid}/clone", json={"name": "q4"})).json()
    assert named["name"] == "q4"

    disabled = (await api.patch(f"/api/v1/endpoints/{eid}", json={"enabled": False})).json()
    assert disabled["enabled"] is False
    assert (await api.get("/api/v1/endpoints?enabled=false")).json()["total"] == 1
    assert (await api.get("/api/v1/endpoints?enabled=true")).json()["total"] == 3
    assert (await api.get("/api/v1/endpoints?name=q4")).json()["items"][0]["id"] == named["id"]

    renamed = await api.patch(f"/api/v1/endpoints/{eid}", json={"name": "q4"})
    assert renamed.status_code == 409

    # Connection-relevant edits clear the previous test result.
    await api.post(f"/api/v1/endpoints/{eid}/test", json={"check_models": False})
    assert (await api.get(f"/api/v1/endpoints/{eid}")).json()["last_test_status"] == "ok"
    edited = (
        await api.patch(f"/api/v1/endpoints/{eid}", json={"model_identifier": "other-model"})
    ).json()
    assert edited["last_test_status"] is None and edited["model_identifier"] == "other-model"
    tagged = (await api.patch(f"/api/v1/endpoints/{eid}", json={"tags": ["x"]})).json()
    assert tagged["tags"] == ["x"]

    assert (await api.delete(f"/api/v1/endpoints/{eid}")).status_code == 204
    assert (await api.get(f"/api/v1/endpoints/{eid}")).status_code == 404
    assert (await api.delete(f"/api/v1/endpoints/{uuid.uuid4()}")).status_code == 404


async def test_validation_rules(api, mock_server):
    async def create(**overrides):
        return await api.post("/api/v1/endpoints", json=vllm_payload(mock_server, **overrides))

    cases = {
        "adapter_type": (await create(adapter_type="nope"), "Unknown adapter"),
        "metadata url": (await create(base_url="http://169.254.169.254/v1"), "metadata"),
        "own db": (await create(base_url="http://postgres:5432"), "BLOCKED"),
        "creds in url": (await create(base_url="http://u:p@host/v1"), "Credentials"),
        "scheme": (await create(base_url="file:///etc/passwd"), "http and https"),
        "secret scope": (await create(secret_reference="env:POSTGRES_PASSWORD"), "OPENLLM_SECRET_"),
        "secret format": (await create(secret_reference="sk-plaintext-key"), "env:NAME"),
        "plaintext key field": (await create(api_key="sk-123"), "Extra inputs"),
        "sensitive header": (
            await create(request_configuration={"headers": {"Authorization": "Bearer x"}}),
            "secret_headers",
        ),
        "ca disabled": (
            await create(request_configuration={"ca_cert_path": "/etc/ssl/ca.pem"}),
            "CA_CERT_DIR",
        ),
        "unknown project": (await create(project_id=str(uuid.uuid4())), "Project not found"),
    }
    for label, (response, fragment) in cases.items():
        assert response.status_code in (404, 422), (label, response.text)
        assert fragment in response.text, (label, response.text)
        assert "sk-123" not in response.text  # submitted values are not echoed back


async def test_private_networks_disabled_blocks_at_create_and_call(
    settings, redis, mock_server, client_for, migrated_app
):
    settings.endpoint_allow_private_networks = False
    migrated_app.state.adapter_context = make_adapter_context(settings)
    async for api in client_for(migrated_app):
        rejected = await api.post("/api/v1/endpoints", json=vllm_payload(mock_server))
        assert rejected.status_code == 422
        assert "ENDPOINT_ALLOW_PRIVATE_NETWORKS" in rejected.text
        # A hostname that resolves to a private address passes static validation but is
        # blocked when the request is made.
        ep = (
            await api.post(
                "/api/v1/endpoints",
                json=vllm_payload(
                    mock_server, base_url=mock_server.base_url.replace("127.0.0.1", "localhost")
                ),
            )
        ).json()
        result = (await api.post(f"/api/v1/endpoints/{ep['id']}/test", json={})).json()
    assert result["ok"] is False
    assert result["response"]["error"]["type"] == "destination_blocked"
    assert result["models_check"]["error"]["type"] == "destination_blocked"


async def test_adapters_listed(api):
    adapters = (await api.get("/api/v1/adapters")).json()
    assert {
        "adapter_type": "openai_compatible",
        "display_name": adapters[0]["display_name"],
        "version": "1.0.0",
    } in adapters


def test_build_app_default_context_uses_settings(settings):
    app = build_app(settings)
    assert app.state.adapter_context is None  # created from settings during lifespan
