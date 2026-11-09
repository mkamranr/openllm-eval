"""OpenAI-compatible adapter behaviour against canned HTTP responses (no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.base import EndpointRuntimeConfig
from app.adapters.openai_compatible import OpenAICompatibleAdapter
from app.adapters.types import GenerationRequest
from app.schemas.endpoint import RequestConfiguration
from tests.conftest import MOCK_API_KEY, MOCK_SECRET_REF, make_adapter_context

BASE = "http://model.test/v1"


async def fake_resolver(host: str, port: int) -> list[str]:
    return {"model.test": ["10.0.0.9"], "meta.test": ["169.254.169.254"]}.get(host, [])


def adapter(
    settings,
    handler,
    *,
    rc: RequestConfiguration | None = None,
    secret_ref: str | None = MOCK_SECRET_REF,
    base_url: str = BASE,
    environ=None,
    **settings_overrides,
):
    for key, value in settings_overrides.items():
        setattr(settings, key, value)
    ctx = make_adapter_context(
        settings, environ=environ, transport=httpx.MockTransport(handler), resolver=fake_resolver
    )
    config = EndpointRuntimeConfig(
        base_url=base_url,
        model_identifier="m",
        request=rc or RequestConfiguration(),
        secret_reference=secret_ref,
    )
    return OpenAICompatibleAdapter(config, ctx)


REQ = GenerationRequest(messages=[{"role": "user", "content": "hi"}])


def completion(**message) -> dict:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "model": "served-name",
        "choices": [
            {"index": 0, "message": {"role": "assistant", **message}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


async def test_non_streaming_normalization_and_request_body(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=completion(content="hello there"),
            headers={"x-request-id": "srv-1", "set-cookie": "s=1"},
        )

    rc = RequestConfiguration(temperature=0, max_tokens=64, headers={"X-Team": "eval"})
    async with adapter(settings, handler, rc=rc) as a:
        r = await a.generate(REQ.model_copy(update={"top_p": 0.9, "extra_body": {"seed": 7}}))

    assert seen["url"] == f"{BASE}/chat/completions"
    assert seen["auth"] == f"Bearer {MOCK_API_KEY}"
    assert seen["body"] == {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0,
        "top_p": 0.9,
        "max_tokens": 64,
        "seed": 7,
    }
    assert r.ok and r.content == "hello there"
    assert r.model == "served-name" and r.response_id == "cmpl-1"
    assert r.finish_reason == "stop"
    assert r.usage.model_dump() == {
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
        "source": "server_reported",
    }
    assert r.timing.streamed is False
    assert r.timing.ttft_ms is None  # not observable without streaming
    assert r.timing.latency_ms >= 0
    assert r.raw_metadata["response_headers"] == {"x-request-id": "srv-1"}  # no cookies
    assert MOCK_API_KEY not in r.model_dump_json()


async def test_missing_usage_is_unavailable_not_zero(settings):
    body = completion(content="x")
    del body["usage"]
    async with adapter(settings, lambda req: httpx.Response(200, json=body)) as a:
        r = await a.generate(REQ)
    assert r.usage.model_dump() == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "source": "unavailable",
    }


async def test_tool_calls_parsed_including_malformed_arguments(settings):
    body = completion(
        content=None,
        tool_calls=[
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Abu Dhabi"}'},
            },
            {"id": "c2", "type": "function", "function": {"name": "bad", "arguments": "{oops"}},
            {"id": "c3", "type": "function", "function": {"name": "arr", "arguments": "[1]"}},
        ],
    )
    async with adapter(settings, lambda req: httpx.Response(200, json=body)) as a:
        r = await a.generate(REQ)
    good, bad, arr = r.tool_calls
    assert (good.name, good.arguments, good.arguments_error) == (
        "get_weather",
        {"city": "Abu Dhabi"},
        None,
    )
    assert bad.arguments is None and "Invalid JSON" in bad.arguments_error
    assert arr.arguments is None and "JSON object" in arr.arguments_error
    assert r.content is None


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (404, False), (429, True), (500, True), (503, True)],
)
async def test_http_errors_normalized(settings, status, retryable):
    def handler(req):
        return httpx.Response(status, json={"error": {"message": f"boom {status}"}})

    async with adapter(settings, handler) as a:
        r = await a.generate(REQ)
    assert r.error.type == "http_status"
    assert r.error.status_code == status == r.http_status
    assert r.error.retryable is retryable
    assert r.error.message == f"boom {status}"


async def test_redirects_are_not_followed(settings):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    async with adapter(settings, handler) as a:
        r = await a.generate(REQ)
    assert calls == [f"{BASE}/chat/completions"]
    assert r.error.type == "http_status" and "Redirects are not followed" in r.error.message


async def test_malformed_body(settings):
    async with adapter(settings, lambda req: httpx.Response(200, content=b"{nope")) as a:
        r = await a.generate(REQ)
    assert r.error.type == "malformed_response"
    async with adapter(settings, lambda req: httpx.Response(200, json={"choices": []})) as a:
        r = await a.generate(REQ)
    assert r.error.type == "malformed_response"


async def test_response_size_limit(settings):
    big = completion(content="x" * 5000)
    async with adapter(
        settings, lambda req: httpx.Response(200, json=big), endpoint_max_response_bytes=1000
    ) as a:
        r = await a.generate(REQ)
    assert r.error.type == "response_too_large"
    assert r.error.retryable is False


async def test_timeouts_and_connection_errors(settings):
    def timeout(req):
        raise httpx.ReadTimeout("read timed out", request=req)

    def refused(req):
        raise httpx.ConnectError("connection refused", request=req)

    async with adapter(settings, timeout) as a:
        r = await a.generate(REQ)
    assert (r.error.type, r.error.retryable) == ("timeout", True)
    assert r.timing is not None  # failures keep timing
    async with adapter(settings, refused) as a:
        r = await a.generate(REQ)
    assert (r.error.type, r.error.retryable) == ("connection_error", True)


async def test_missing_secret_fails_before_any_request(settings):
    calls = []
    async with adapter(
        settings, lambda req: calls.append(req) or httpx.Response(200), environ={}
    ) as a:
        r = await a.generate(REQ)
    assert calls == []
    assert r.error.type == "secret_unavailable"
    assert "OPENLLM_SECRET_MOCK_KEY" in r.error.message


async def test_secret_headers_sent_and_secrets_scrubbed_from_errors(settings):
    seen = {}

    def handler(req):
        seen["gw"] = req.headers.get("x-gateway-key")
        # A misbehaving server echoing credentials back in its error message.
        echoed = f"bad key {req.headers['x-gateway-key']} / {req.headers['authorization']}"
        return httpx.Response(403, json={"error": {"message": echoed}})

    rc = RequestConfiguration(secret_headers={"X-Gateway-Key": "env:OPENLLM_SECRET_GW"})
    environ = {"OPENLLM_SECRET_MOCK_KEY": MOCK_API_KEY, "OPENLLM_SECRET_GW": "gw-secret-123"}
    async with adapter(settings, handler, rc=rc, environ=environ) as a:
        r = await a.generate(REQ)
    assert seen["gw"] == "gw-secret-123"
    dumped = r.model_dump_json()
    assert "gw-secret-123" not in dumped
    assert MOCK_API_KEY not in dumped
    assert "REDACTED" in r.error.message


async def test_network_policy_enforced_at_call_time(settings):
    calls = []
    handler = lambda req: calls.append(req) or httpx.Response(200)  # noqa: E731
    async with adapter(settings, handler, base_url="http://meta.test/v1") as a:
        r = await a.generate(REQ)
    assert r.error.type == "destination_blocked"
    async with adapter(settings, handler, endpoint_allow_private_networks=False) as a:
        r = await a.generate(REQ)  # model.test resolves to 10.0.0.9
    assert r.error.type == "destination_blocked"
    assert "ENDPOINT_ALLOW_PRIVATE_NETWORKS" in r.error.message
    async with adapter(settings, handler, base_url="http://unknown.test/v1") as a:
        r = await a.generate(REQ)
    assert (r.error.type, r.error.retryable) == ("dns_resolution_failed", True)
    assert calls == []


def sse(*events: object) -> bytes:
    out = []
    for e in events:
        if isinstance(e, str) and e.startswith(":"):
            out.append(f"{e}\n\n")  # SSE comment line
            continue
        out.append(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n")
    return "".join(out).encode()


def delta(**d) -> dict:
    return {
        "id": "s1",
        "model": "served",
        "choices": [{"index": 0, "delta": d, "finish_reason": None}],
    }


async def test_stream_assembly_ignores_empty_events(settings):
    body = sse(
        ": keep-alive comment",
        delta(role="assistant", content=""),
        delta(content="Hel"),
        delta(reasoning_content="thinking"),
        delta(content="lo"),
        {"id": "s1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"id": "s1", "choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}},
        "[DONE]",
    )
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    events = []
    async with adapter(settings, handler) as a:
        r = await a.generate_stream(REQ, on_event=events.append)
    assert seen["body"]["stream"] is True
    assert seen["body"]["stream_options"] == {"include_usage": True}
    assert r.ok and r.content == "Hello" and r.reasoning_content == "thinking"
    assert r.finish_reason == "stop"
    assert r.usage.total_tokens == 6 and r.usage.source == "server_reported"
    assert [e.kind for e in events] == ["content", "reasoning", "content"]
    assert len(r.timing.output_event_offsets_ms) == 3  # role-only/empty events excluded
    assert r.timing.ttft_ms is not None and r.timing.first_content_at is not None
    assert r.warnings == []


async def test_stream_tool_call_deltas_assembled(settings):
    body = sse(
        delta(role="assistant"),
        delta(
            tool_calls=[
                {
                    "index": 0,
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": ""},
                }
            ]
        ),
        delta(tool_calls=[{"index": 0, "function": {"arguments": '{"city":'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": ' "Paris"}'}}]),
        "[DONE]",
    )
    async with adapter(settings, lambda req: httpx.Response(200, content=body)) as a:
        r = await a.generate_stream(REQ)
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0].name == "get_weather"
    assert r.tool_calls[0].arguments == {"city": "Paris"}
    assert r.content is None


async def test_stream_errors_and_warnings(settings):
    mid = sse(delta(content="a"), {"error": {"message": "upstream died"}})
    async with adapter(settings, lambda req: httpx.Response(200, content=mid)) as a:
        r = await a.generate_stream(REQ)
    assert r.error.type == "stream_error" and r.content == "a"

    bad = sse(delta(content="a")) + b"data: {broken\n\n"
    async with adapter(settings, lambda req: httpx.Response(200, content=bad)) as a:
        r = await a.generate_stream(REQ)
    assert r.error.type == "malformed_response"

    no_done = sse(delta(content="a"))
    async with adapter(settings, lambda req: httpx.Response(200, content=no_done)) as a:
        r = await a.generate_stream(REQ)
    assert r.ok
    assert any("[DONE]" in w for w in r.warnings)
    assert any("usage" in w for w in r.warnings)


async def test_stream_without_output_has_no_ttft(settings):
    body = sse(delta(role="assistant", content=""), "[DONE]")
    async with adapter(settings, lambda req: httpx.Response(200, content=body)) as a:
        r = await a.generate_stream(REQ)
    assert r.timing.ttft_ms is None and r.timing.generation_ms is None
    assert r.content is None


async def test_validate_connection_and_metadata(settings):
    def handler(req):
        assert req.url.path == "/v1/models"
        return httpx.Response(
            200, json={"data": [{"id": "m", "max_model_len": 8192}, {"id": "other"}]}
        )

    async with adapter(settings, handler) as a:
        check = await a.validate_connection()
        meta = await a.get_model_metadata()
    assert check.ok and check.model_listed is True
    assert check.available_models == ["m", "other"]
    assert meta.verified is False and meta.source == "server_reported"
    assert meta.model_entry == {"id": "m", "max_model_len": 8192}


async def test_validate_connection_reports_failures(settings):
    async with adapter(settings, lambda req: httpx.Response(404, json={"detail": "no"})) as a:
        check = await a.validate_connection()
    assert not check.ok and check.http_status == 404 and check.error.type == "http_status"
