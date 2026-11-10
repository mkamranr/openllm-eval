"""Adapter for OpenAI-compatible chat-completions APIs.

Works with vLLM, llama.cpp server, Ollama (``/v1``), LM Studio, TGI's OpenAI route, and hosted
OpenAI-compatible providers. ``base_url`` is the API root that contains ``/chat/completions``
and ``/models``, e.g. ``http://vllm:8000/v1``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app import __version__
from app.adapters.base import (
    AdapterContext,
    EmbeddingError,
    EndpointRuntimeConfig,
    ModelAdapter,
    StreamCallback,
)
from app.adapters.types import (
    AdapterError,
    ConnectionCheck,
    ErrorType,
    GenerationRequest,
    ModelMetadata,
    ModelResponse,
    StreamEvent,
    Timing,
    TokenUsage,
    ToolCall,
)
from app.core.logging import get_logger, redact_text
from app.core.network_policy import DestinationBlockedError, DestinationUnresolvableError
from app.core.secrets import SecretReferenceError

logger = get_logger(__name__)

RETRYABLE_STATUS = frozenset({408, 409, 425, 429})
MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_ERROR_MESSAGE_CHARS = 1000
# Response headers worth keeping for debugging (never request headers).
KEPT_RESPONSE_HEADERS = ("server", "x-request-id", "openai-model", "openai-processing-ms")


class ResponseTooLargeError(Exception):
    pass


class _PreparedRequest:
    __slots__ = ("body", "headers", "secrets", "url")

    def __init__(self, url: str, headers: dict[str, str], body: dict[str, Any], secrets: list[str]):
        self.url = url
        self.headers = headers
        self.body = body
        self.secrets = secrets


class _Clock:
    """Monotonic durations anchored to a wall-clock start time."""

    def __init__(self) -> None:
        self.started_at = datetime.now(UTC)
        self._t0 = time.perf_counter()

    def offset_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def at(self, offset_ms: float) -> datetime:
        return self.started_at + timedelta(milliseconds=offset_ms)


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _parse_usage(raw: Any) -> TokenUsage:
    if not isinstance(raw, dict):
        return TokenUsage()
    inp = _int_or_none(raw.get("prompt_tokens"))
    out = _int_or_none(raw.get("completion_tokens"))
    total = _int_or_none(raw.get("total_tokens"))
    if total is None and inp is not None and out is not None:
        total = inp + out
    if inp is None and out is None and total is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=inp, output_tokens=out, total_tokens=total, source="server_reported"
    )


def _text(value: Any) -> str | None:
    """Message content may be a string or a list of content parts."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [p.get("text", "") for p in value if isinstance(p, dict)]
        return "".join(parts)
    return str(value)


def _finalize_tool_call(call: ToolCall) -> ToolCall:
    raw = call.arguments_raw.strip()
    if not raw:
        call.arguments = {}
        return call
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        call.arguments_error = f"Invalid JSON: {exc.msg} at position {exc.pos}"
        return call
    if isinstance(parsed, dict):
        call.arguments = parsed
    else:
        call.arguments_error = f"Arguments must be a JSON object, got {type(parsed).__name__}"
    return call


def _parse_tool_calls(raw: Any) -> list[ToolCall]:
    calls: list[ToolCall] = []
    if not isinstance(raw, list):
        return calls
    for item in raw:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") or {}
        args = fn.get("arguments")
        calls.append(
            _finalize_tool_call(
                ToolCall(
                    id=item.get("id"),
                    type=item.get("type") or "function",
                    name=fn.get("name"),
                    arguments_raw=args if isinstance(args, str) else json.dumps(args or {}),
                )
            )
        )
    return calls


def _error_message_from_body(body: bytes) -> str:
    try:
        data = json.loads(body)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
        if isinstance(data, dict) and data.get("detail"):
            return str(data["detail"])
    except (ValueError, AttributeError):
        pass
    return body.decode("utf-8", errors="replace")


# Error messages that identify the cause of a failure (vLLM, llama.cpp, TGI, Ollama, OpenAI).
_CONTEXT_OVERFLOW = re.compile(
    r"maximum context length|context length|context window|context_length_exceeded|"
    r"exceeds? the (?:model'?s? )?(?:maximum|max)|too many tokens|prompt is too long|"
    r"input (?:is )?too long|max_model_len|n_ctx|longer than the maximum|"
    r"requested \d+ tokens",
    re.IGNORECASE,
)
_OUT_OF_MEMORY = re.compile(
    r"out of memory|\boom\b|cuda error: out|cudaErrorMemoryAllocation|"
    r"failed to allocate|insufficient memory|not enough memory",
    re.IGNORECASE,
)


def classify_error_message(message: str, status: int | None) -> str | None:
    """``context_overflow`` or ``out_of_memory`` when the message says so, else None.
    Classification uses the server's message only; it is recorded, never guessed from timing."""
    if _OUT_OF_MEMORY.search(message):
        return "out_of_memory"
    if (status is None or 400 <= status < 500 or status == 500) and _CONTEXT_OVERFLOW.search(
        message
    ):
        return "context_overflow"
    return None


class OpenAICompatibleAdapter(ModelAdapter):
    adapter_type = "openai_compatible"
    display_name = "OpenAI-compatible (vLLM, llama.cpp, Ollama /v1, hosted APIs)"
    # 1.1.0: context_overflow / out_of_memory error types (Milestone 7).
    version = "1.1.0"

    def __init__(self, config: EndpointRuntimeConfig, context: AdapterContext) -> None:
        super().__init__(config, context)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ plumbing
    def _url(self, path: str) -> str:
        return self.config.base_url.rstrip("/") + path

    def _verify(self) -> bool | str:
        rc = self.config.request
        if rc.ca_cert_path:
            if not Path(rc.ca_cert_path).is_file():
                raise FileNotFoundError(f"CA certificate not found: {rc.ca_cert_path}")
            return rc.ca_cert_path
        return rc.verify_tls

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            rc = self.config.request
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    rc.request_timeout_seconds, connect=rc.connect_timeout_seconds
                ),
                verify=self._verify(),
                follow_redirects=False,  # a permitted host must not bounce us elsewhere
                trust_env=False,  # no implicit proxies from the environment (§12.2)
                transport=self.context.transport,
                limits=httpx.Limits(max_connections=256, max_keepalive_connections=64),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> tuple[dict[str, str], list[str]]:
        """Build request headers. Returns (headers, resolved secret values for scrubbing)."""
        rc = self.config.request
        headers = {
            "Accept": "application/json",
            "User-Agent": f"openllm-eval/{__version__}",
            **rc.headers,
        }
        secrets: list[str] = []
        resolver = self.context.secrets
        if self.config.secret_reference:
            value = resolver.resolve(self.config.secret_reference)
            if value is None:
                raise LookupError(self.config.secret_reference)
            headers["Authorization"] = f"Bearer {value}"
            secrets.append(value)
        for name, reference in rc.secret_headers.items():
            value = resolver.resolve(reference)
            if value is None:
                raise LookupError(reference)
            headers[name] = value
            secrets.append(value)
        return headers, secrets

    async def _preflight(self, url: str) -> tuple[dict[str, str], list[str]] | AdapterError:
        """Network policy + secret resolution. Returns headers or a normalized error."""
        try:
            await self.context.policy.check_destination(url)
        except DestinationBlockedError as exc:
            return AdapterError(type="destination_blocked", message=str(exc))
        except DestinationUnresolvableError as exc:
            return AdapterError(type="dns_resolution_failed", message=str(exc), retryable=True)
        try:
            headers, secrets = self._headers()
        except LookupError as exc:
            return AdapterError(
                type="secret_unavailable",
                message=f"Secret {exc.args[0]} is not set in the server environment.",
            )
        except SecretReferenceError as exc:
            return AdapterError(type="secret_unavailable", message=str(exc))
        try:
            self._get_client()
        except (OSError, ValueError) as exc:
            return AdapterError(type="tls_configuration", message=str(exc))
        return headers, secrets

    def _body(self, request: GenerationRequest, stream: bool) -> dict[str, Any]:
        rc = self.config.request
        body: dict[str, Any] = {"model": self.config.model_identifier, "messages": request.messages}
        for key, value in (
            (
                "temperature",
                request.temperature if request.temperature is not None else rc.temperature,
            ),
            ("top_p", request.top_p if request.top_p is not None else rc.top_p),
            ("max_tokens", request.max_tokens if request.max_tokens is not None else rc.max_tokens),
        ):
            if value is not None:
                body[key] = value
        if request.tools:
            body["tools"] = request.tools
        if request.tool_choice is not None:
            body["tool_choice"] = request.tool_choice
        if stream:
            body["stream"] = True
            if rc.stream_include_usage:
                body["stream_options"] = {"include_usage": True}
        body.update(request.extra_body)
        return body

    @staticmethod
    def _scrub(text: str, secrets: list[str]) -> str:
        for secret in secrets:
            if secret:
                text = text.replace(secret, "***REDACTED***")
        return redact_text(text)[:MAX_ERROR_MESSAGE_CHARS]

    @staticmethod
    def _exception_error(exc: BaseException, secrets: list[str]) -> AdapterError:
        mapping: list[tuple[type[BaseException], ErrorType, bool]] = [
            (ResponseTooLargeError, "response_too_large", False),
            (httpx.ConnectTimeout, "connect_timeout", True),
            (httpx.TimeoutException, "timeout", True),
            (TimeoutError, "timeout", True),
            (httpx.ConnectError, "connection_error", True),
            (httpx.RemoteProtocolError, "connection_error", True),
            (httpx.ReadError, "connection_error", True),
            (httpx.HTTPError, "transport_error", False),
        ]
        for exc_type, error_type, retryable in mapping:
            if isinstance(exc, exc_type):
                message = str(exc) or type(exc).__name__
                return AdapterError(
                    type=error_type,
                    message=OpenAICompatibleAdapter._scrub(message, secrets),
                    retryable=retryable,
                )
        raise exc

    def _status_error(self, status: int, body: bytes, secrets: list[str]) -> AdapterError:
        if 300 <= status < 400:
            message = f"Redirects are not followed (HTTP {status}). Use the final URL."
        else:
            message = self._scrub(_error_message_from_body(body), secrets) or f"HTTP {status}"
        kind = classify_error_message(message, status)
        if kind == "context_overflow":
            # The same request will not fit next time either.
            return AdapterError(
                type="context_overflow", message=message, status_code=status, retryable=False
            )
        return AdapterError(
            type=kind or "http_status",
            message=message,
            status_code=status,
            retryable=status in RETRYABLE_STATUS or status >= 500,
        )

    async def _read_bounded(self, response: httpx.Response, limit: int) -> bytes:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise ResponseTooLargeError(f"Response of {declared} bytes exceeds limit of {limit}")
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise ResponseTooLargeError(f"Response exceeds limit of {limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    def _base_response(self, request: GenerationRequest) -> ModelResponse:
        return ModelResponse(
            request_id=request.request_id,
            endpoint_id=self.config.endpoint_id,
            adapter_type=self.adapter_type,
        )

    @staticmethod
    def _kept_headers(response: httpx.Response) -> dict[str, str]:
        return {h: response.headers[h] for h in KEPT_RESPONSE_HEADERS if h in response.headers}

    def _log(self, result: ModelResponse) -> None:
        logger.info(
            "model_request",
            endpoint_id=str(self.config.endpoint_id) if self.config.endpoint_id else None,
            request_id=str(result.request_id),
            streamed=result.timing.streamed if result.timing else None,
            http_status=result.http_status,
            latency_ms=result.timing.latency_ms if result.timing else None,
            ttft_ms=result.timing.ttft_ms if result.timing else None,
            error_type=result.error.type if result.error else None,
        )

    # ------------------------------------------------------------------ public API
    async def validate_connection(self) -> ConnectionCheck:
        url = self._url("/models")
        pre = await self._preflight(url)
        if isinstance(pre, AdapterError):
            return ConnectionCheck(ok=False, error=pre)
        headers, secrets = pre
        clock = _Clock()
        try:
            async with asyncio.timeout(self.config.request.request_timeout_seconds):
                async with self._get_client().stream("GET", url, headers=headers) as resp:
                    limit = (
                        self.context.max_response_bytes
                        if resp.status_code == 200
                        else MAX_ERROR_BODY_BYTES
                    )
                    body = await self._read_bounded(resp, limit)
        except Exception as exc:
            return ConnectionCheck(ok=False, error=self._exception_error(exc, secrets))
        latency = round(clock.offset_ms(), 2)
        if resp.status_code != 200:
            return ConnectionCheck(
                ok=False,
                latency_ms=latency,
                http_status=resp.status_code,
                error=self._status_error(resp.status_code, body, secrets),
            )
        try:
            data = json.loads(body)
            entries = data.get("data") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                raise ValueError("missing 'data' list")
            models = [str(e["id"]) for e in entries if isinstance(e, dict) and "id" in e]
        except (ValueError, KeyError, TypeError) as exc:
            return ConnectionCheck(
                ok=False,
                latency_ms=latency,
                http_status=200,
                error=AdapterError(
                    type="malformed_response", message=f"Unexpected /models response: {exc}"
                ),
            )
        return ConnectionCheck(
            ok=True,
            latency_ms=latency,
            http_status=200,
            available_models=models,
            model_listed=self.config.model_identifier in models,
        )

    async def get_model_metadata(self) -> ModelMetadata:
        url = self._url("/models")
        result = ModelMetadata(model_identifier=self.config.model_identifier)
        pre = await self._preflight(url)
        if isinstance(pre, AdapterError):
            result.error = pre
            return result
        headers, secrets = pre
        try:
            async with asyncio.timeout(self.config.request.request_timeout_seconds):
                async with self._get_client().stream("GET", url, headers=headers) as resp:
                    body = await self._read_bounded(resp, self.context.max_response_bytes)
            if resp.status_code != 200:
                result.error = self._status_error(resp.status_code, body, secrets)
                return result
            entries = json.loads(body).get("data") or []
        except Exception as exc:
            if isinstance(exc, ValueError | AttributeError):
                result.error = AdapterError(type="malformed_response", message=str(exc))
            else:
                result.error = self._exception_error(exc, secrets)
            return result
        result.available_models = [str(e.get("id")) for e in entries if isinstance(e, dict)]
        result.model_entry = next(
            (
                e
                for e in entries
                if isinstance(e, dict) and e.get("id") == self.config.model_identifier
            ),
            None,
        )
        return result

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """POST /embeddings with the endpoint's model. Same network policy, secrets handling,
        timeout, and size limit as chat requests."""
        url = self._url("/embeddings")
        pre = await self._preflight(url)
        if isinstance(pre, AdapterError):
            raise EmbeddingError(pre.message)
        headers, secrets = pre
        body = {"model": self.config.model_identifier, "input": texts}
        try:
            async with asyncio.timeout(self.config.request.request_timeout_seconds):
                async with self._get_client().stream(
                    "POST", url, json=body, headers=headers
                ) as resp:
                    raw = await self._read_bounded(resp, self.context.max_response_bytes)
        except Exception as exc:
            raise EmbeddingError(self._exception_error(exc, secrets).message) from None
        if resp.status_code != 200:
            raise EmbeddingError(self._status_error(resp.status_code, raw, secrets).message)
        try:
            items = sorted(json.loads(raw)["data"], key=lambda d: d.get("index", 0))
            vectors = [[float(x) for x in item["embedding"]] for item in items]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise EmbeddingError(f"Unexpected embeddings response: {type(exc).__name__}") from None
        if len(vectors) != len(texts):
            raise EmbeddingError(f"Expected {len(texts)} embeddings, got {len(vectors)}.")
        return vectors

    async def generate(self, request: GenerationRequest) -> ModelResponse:
        result = self._base_response(request)
        url = self._url("/chat/completions")
        pre = await self._preflight(url)
        if isinstance(pre, AdapterError):
            result.error = pre
            return result
        headers, secrets = pre
        body = self._body(request, stream=False)
        clock = _Clock()  # immediately before sending
        try:
            async with asyncio.timeout(self.config.request.request_timeout_seconds):
                async with self._get_client().stream(
                    "POST", url, json=body, headers=headers
                ) as resp:
                    limit = (
                        self.context.max_response_bytes
                        if resp.status_code == 200
                        else MAX_ERROR_BODY_BYTES
                    )
                    raw = await self._read_bounded(resp, limit)
        except Exception as exc:
            end = clock.offset_ms()
            result.timing = Timing(
                streamed=False,
                started_at=clock.started_at,
                ended_at=clock.at(end),
                latency_ms=round(end, 3),
            )
            result.error = self._exception_error(exc, secrets)
            self._log(result)
            return result

        end = clock.offset_ms()
        result.http_status = resp.status_code
        result.timing = Timing(
            streamed=False,
            started_at=clock.started_at,
            ended_at=clock.at(end),
            latency_ms=round(end, 3),
        )
        if resp.status_code != 200:
            result.error = self._status_error(resp.status_code, raw, secrets)
            self._log(result)
            return result
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            result.error = AdapterError(
                type="malformed_response",
                message=f"Unexpected chat completion response: {type(exc).__name__}: {exc}",
            )
            self._log(result)
            return result

        result.response_id = data.get("id")
        result.model = data.get("model")
        result.content = _text(message.get("content"))
        result.reasoning_content = _text(
            message.get("reasoning_content") or message.get("reasoning")
        )
        result.tool_calls = _parse_tool_calls(message.get("tool_calls"))
        result.finish_reason = choice.get("finish_reason")
        result.usage = _parse_usage(data.get("usage"))
        result.raw_metadata = {
            "object": data.get("object"),
            "created": data.get("created"),
            "system_fingerprint": data.get("system_fingerprint"),
            "usage": data.get("usage"),
            "response_headers": self._kept_headers(resp),
        }
        self._log(result)
        return result

    async def generate_stream(
        self, request: GenerationRequest, on_event: StreamCallback | None = None
    ) -> ModelResponse:
        result = self._base_response(request)
        url = self._url("/chat/completions")
        pre = await self._preflight(url)
        if isinstance(pre, AdapterError):
            result.error = pre
            return result
        headers, secrets = pre
        headers = {**headers, "Accept": "text/event-stream"}
        body = self._body(request, stream=True)

        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, ToolCall] = {}
        offsets: list[float] = []
        first_ms: float | None = None
        saw_done = False
        last_meta: dict[str, Any] = {}
        bytes_read = 0
        limit = self.context.max_response_bytes

        async def emit(kind: str, text: str, offset: float) -> None:
            if on_event is None:
                return
            ret = on_event(StreamEvent(kind=kind, text=text, offset_ms=round(offset, 3)))  # type: ignore[arg-type]
            if inspect.isawaitable(ret):
                await ret

        clock = _Clock()  # immediately before sending
        try:
            async with asyncio.timeout(self.config.request.request_timeout_seconds):
                async with self._get_client().stream(
                    "POST", url, json=body, headers=headers
                ) as resp:
                    result.http_status = resp.status_code
                    if resp.status_code != 200:
                        raw = await self._read_bounded(resp, MAX_ERROR_BODY_BYTES)
                        result.error = self._status_error(resp.status_code, raw, secrets)
                    else:
                        result.raw_metadata["response_headers"] = self._kept_headers(resp)
                        async for line in resp.aiter_lines():
                            bytes_read += len(line) + 1
                            if bytes_read > limit:
                                raise ResponseTooLargeError(
                                    f"Stream exceeds limit of {limit} bytes"
                                )
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue  # blank lines, comments, event:/id: fields
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                saw_done = True
                                break
                            now = clock.offset_ms()
                            try:
                                chunk = json.loads(payload)
                                if not isinstance(chunk, dict):
                                    raise ValueError("chunk is not an object")
                            except ValueError as exc:
                                result.error = AdapterError(
                                    type="malformed_response",
                                    message=f"Invalid stream chunk: {exc}",
                                )
                                break
                            if chunk.get("error"):
                                err = chunk["error"]
                                msg = err.get("message") if isinstance(err, dict) else str(err)
                                result.error = AdapterError(
                                    type=classify_error_message(str(msg), None) or "stream_error",
                                    message=self._scrub(str(msg), secrets),
                                )
                                break
                            result.response_id = result.response_id or chunk.get("id")
                            result.model = result.model or chunk.get("model")
                            for key in ("object", "created", "system_fingerprint"):
                                if chunk.get(key) is not None:
                                    last_meta[key] = chunk[key]
                            if chunk.get("usage"):
                                result.usage = _parse_usage(chunk["usage"])
                                last_meta["usage"] = chunk["usage"]
                            had_output = False
                            for choice in chunk.get("choices") or []:
                                if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                                    continue
                                delta = choice.get("delta") or {}
                                text = _text(delta.get("content"))
                                if text:
                                    content.append(text)
                                    had_output = True
                                    await emit("content", text, now)
                                rtext = _text(
                                    delta.get("reasoning_content") or delta.get("reasoning")
                                )
                                if rtext:
                                    reasoning.append(rtext)
                                    had_output = True
                                    await emit("reasoning", rtext, now)
                                for tc in delta.get("tool_calls") or []:
                                    if not isinstance(tc, dict):
                                        continue
                                    idx = tc.get("index", len(calls))
                                    call = calls.setdefault(idx, ToolCall())
                                    call.id = tc.get("id") or call.id
                                    call.type = tc.get("type") or call.type
                                    fn = tc.get("function") or {}
                                    if fn.get("name"):
                                        call.name = (call.name or "") + fn["name"]
                                        had_output = True
                                    if fn.get("arguments"):
                                        call.arguments_raw += fn["arguments"]
                                        had_output = True
                                        await emit("tool_call", fn["arguments"], now)
                                if choice.get("finish_reason"):
                                    result.finish_reason = choice["finish_reason"]
                            if had_output:
                                offsets.append(round(now, 3))
                                if first_ms is None:
                                    first_ms = now
        except Exception as exc:
            result.error = self._exception_error(exc, secrets)

        end = clock.offset_ms()
        result.timing = Timing(
            streamed=True,
            started_at=clock.started_at,
            first_content_at=clock.at(first_ms) if first_ms is not None else None,
            ended_at=clock.at(end),
            ttft_ms=round(first_ms, 3) if first_ms is not None else None,
            latency_ms=round(end, 3),
            generation_ms=round(end - first_ms, 3) if first_ms is not None else None,
            output_event_offsets_ms=offsets,
        )
        result.content = "".join(content) if content else None
        result.reasoning_content = "".join(reasoning) if reasoning else None
        result.tool_calls = [_finalize_tool_call(calls[i]) for i in sorted(calls)]
        result.raw_metadata.update(last_meta)
        if result.error is None and result.http_status == 200:
            if not saw_done:
                result.warnings.append("Stream ended without a [DONE] marker.")
            if result.usage.source == "unavailable":
                result.warnings.append(
                    "Server did not report token usage for this stream; counts unavailable."
                )
        self._log(result)
        return result
