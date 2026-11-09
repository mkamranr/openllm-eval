"""Common contract every registered model adapter must satisfy (spec §15.3).

Runs each adapter against the bundled mock OpenAI-compatible server over a real socket, so
streaming timing is measured exactly as in production. Adapters for other protocols should
supply their own server fixture in ``ADAPTER_SERVERS``.
"""

from __future__ import annotations

import pytest

from app.adapters.base import EndpointRuntimeConfig, ModelAdapter
from app.adapters.registry import adapter_classes, create_adapter
from app.adapters.types import GenerationRequest, ModelResponse
from app.schemas.endpoint import RequestConfiguration
from tests.conftest import MOCK_API_KEY, MOCK_SECRET_REF

ADAPTER_TYPES = sorted(adapter_classes())
# asyncio may fire timers up to one clock tick early (~15.6 ms on Windows), so the mock's
# sleeps can be slightly shorter than requested.
CLOCK_TOLERANCE_MS = 20
REQ = GenerationRequest(messages=[{"role": "user", "content": "one two three four"}])


def make(
    adapter_type: str,
    mock_server,
    adapter_context,
    headers=None,
    model="mock-model",
    secret_ref=MOCK_SECRET_REF,
    **rc,
) -> ModelAdapter:
    config = EndpointRuntimeConfig(
        base_url=mock_server.base_url,
        model_identifier=model,
        secret_reference=secret_ref,
        request=RequestConfiguration(headers=headers or {}, **rc),
    )
    return create_adapter(adapter_type, config, adapter_context)


def assert_no_secret(response: ModelResponse) -> None:
    assert MOCK_API_KEY not in response.model_dump_json()


@pytest.mark.parametrize("adapter_type", ADAPTER_TYPES)
def test_adapter_declares_identity(adapter_type):
    cls = adapter_classes()[adapter_type]
    assert cls.adapter_type == adapter_type
    assert cls.display_name and cls.version


@pytest.mark.parametrize("adapter_type", ADAPTER_TYPES)
async def test_non_streaming_contract(adapter_type, mock_server, adapter_context):
    async with make(adapter_type, mock_server, adapter_context) as a:
        r = await a.generate(REQ)
    assert r.ok, r.error
    assert r.adapter_type == adapter_type
    assert r.content and "one two three four" in r.content
    assert r.http_status == 200
    assert r.usage.source == "server_reported" and r.usage.input_tokens == 4
    assert r.timing.streamed is False and r.timing.ttft_ms is None
    assert r.timing.ended_at >= r.timing.started_at
    assert_no_secret(r)


@pytest.mark.parametrize("adapter_type", ADAPTER_TYPES)
async def test_streaming_ttft_excludes_headers_and_empty_events(
    adapter_type, mock_server, adapter_context
):
    # The mock sends HTTP headers and a role-only event immediately, then waits 300 ms
    # before the first content token, then 50 ms between tokens.
    headers = {
        "X-Mock-First-Token-Delay-Ms": "300",
        "X-Mock-Chunk-Delay-Ms": "50",
        "X-Mock-Response": "alpha beta gamma delta",
    }
    events = []
    async with make(adapter_type, mock_server, adapter_context, headers=headers) as a:
        r = await a.generate_stream(REQ, on_event=events.append)
    assert r.ok, r.error
    assert r.content == "alpha beta gamma delta"
    t = r.timing
    assert t.streamed is True
    # The role-only event arrives after ~1 ms; TTFT must reflect the 300 ms content delay.
    assert t.ttft_ms >= 300 - CLOCK_TOLERANCE_MS, f"TTFT {t.ttft_ms} ms counted a pre-content event"
    # Three more tokens 50 ms apart.
    assert t.latency_ms >= t.ttft_ms + 3 * (50 - CLOCK_TOLERANCE_MS)
    assert t.generation_ms == pytest.approx(t.latency_ms - t.ttft_ms, abs=0.01)
    assert t.first_content_at > t.started_at and t.ended_at >= t.first_content_at
    assert len(t.output_event_offsets_ms) == 4
    assert t.output_event_offsets_ms == sorted(t.output_event_offsets_ms)
    assert t.output_event_offsets_ms[0] == pytest.approx(t.ttft_ms, abs=0.01)
    assert [e.text for e in events] == ["alpha", " beta", " gamma", " delta"]
    assert r.usage.source == "server_reported" and r.usage.output_tokens == 4
    assert r.finish_reason == "stop"
    assert r.warnings == []
    assert_no_secret(r)


@pytest.mark.parametrize("adapter_type", ADAPTER_TYPES)
async def test_stream_usage_absent_is_reported_as_unavailable(
    adapter_type, mock_server, adapter_context
):
    async with make(
        adapter_type, mock_server, adapter_context, headers={"X-Mock-Mode": "no_usage"}
    ) as a:
        r = await a.generate_stream(REQ)
    assert r.ok
    assert r.usage.source == "unavailable" and r.usage.output_tokens is None


@pytest.mark.parametrize("adapter_type", ADAPTER_TYPES)
async def test_tool_calls_contract(adapter_type, mock_server, adapter_context):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]
    headers = {"X-Mock-Mode": "tool_call", "X-Mock-Tool-Args": '{"city": "Abu Dhabi"}'}
    req = REQ.model_copy(update={"tools": tools})
    async with make(adapter_type, mock_server, adapter_context, headers=headers) as a:
        plain = await a.generate(req)
        streamed = await a.generate_stream(req)
    for r in (plain, streamed):
        assert r.ok, r.error
        assert [(c.name, c.arguments) for c in r.tool_calls] == [
            ("get_weather", {"city": "Abu Dhabi"})
        ]
        assert r.finish_reason == "tool_calls"


@pytest.mark.parametrize("adapter_type", ADAPTER_TYPES)
@pytest.mark.parametrize("stream", [False, True])
async def test_errors_are_returned_not_raised(adapter_type, stream, mock_server, adapter_context):
    cases = [
        ({"X-Mock-Status": "503"}, "mock-model", MOCK_SECRET_REF, "http_status", True),
        ({"X-Mock-Status": "429"}, "mock-model", MOCK_SECRET_REF, "http_status", True),
        ({}, "missing-model", MOCK_SECRET_REF, "http_status", False),
        ({}, "mock-model", None, "http_status", False),  # 401 without key
        ({"X-Mock-Mode": "malformed"}, "mock-model", MOCK_SECRET_REF, "malformed_response", False),
    ]
    for headers, model, ref, expected_type, retryable in cases:
        async with make(
            adapter_type, mock_server, adapter_context, headers=headers, model=model, secret_ref=ref
        ) as a:
            r = await (a.generate_stream(REQ) if stream else a.generate(REQ))
        assert r.error is not None, (headers, model)
        assert r.error.type == expected_type, (headers, model, r.error)
        assert r.error.retryable is retryable, (headers, model, r.error)
        assert r.timing is not None
        assert_no_secret(r)


@pytest.mark.parametrize("adapter_type", ADAPTER_TYPES)
async def test_mid_stream_error_keeps_partial_output(adapter_type, mock_server, adapter_context):
    async with make(
        adapter_type, mock_server, adapter_context, headers={"X-Mock-Mode": "stream_error"}
    ) as a:
        r = await a.generate_stream(REQ)
    assert r.error.type == "stream_error"
    assert r.content  # partial output preserved for inspection
    assert r.timing.ttft_ms is not None


@pytest.mark.parametrize("adapter_type", ADAPTER_TYPES)
async def test_request_timeout(adapter_type, mock_server, adapter_context):
    async with make(
        adapter_type,
        mock_server,
        adapter_context,
        headers={"X-Mock-First-Token-Delay-Ms": "3000"},
        request_timeout_seconds=0.5,
    ) as a:
        r = await a.generate_stream(REQ)
    assert r.error.type == "timeout" and r.error.retryable
    assert 400 <= r.timing.latency_ms < 2500


@pytest.mark.parametrize("adapter_type", ADAPTER_TYPES)
async def test_connection_check_contract(adapter_type, mock_server, adapter_context):
    async with make(adapter_type, mock_server, adapter_context) as a:
        check = await a.validate_connection()
        meta = await a.get_model_metadata()
    assert check.ok and check.model_listed is True
    assert set(check.available_models) == {"mock-model", "other-model"}
    assert meta.verified is False and meta.model_entry["max_model_len"] == 4096
