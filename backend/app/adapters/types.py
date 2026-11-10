"""Provider-neutral request/response types (spec §7.6).

Evaluators and the run engine only see these types, never provider-specific payloads. Missing
values are ``None``, never zero (spec §2.3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TokenCountSource = Literal["server_reported", "tokenizer_estimated", "unavailable"]

ErrorType = Literal[
    "destination_blocked",
    "dns_resolution_failed",
    "secret_unavailable",
    "tls_configuration",
    "connect_timeout",
    "timeout",
    "connection_error",
    "http_status",
    # Server errors recognized from their message (Milestone 7): the request did not fit the
    # model's context window, or the server ran out of (GPU) memory.
    "context_overflow",
    "out_of_memory",
    "malformed_response",
    "stream_error",
    "response_too_large",
    "transport_error",
]


class GenerationRequest(BaseModel):
    """A chat-completion request. Unset generation parameters fall back to endpoint defaults."""

    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    messages: list[dict[str, Any]] = Field(min_length=1)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    # Extra provider parameters merged into the request body (e.g. {"seed": 1}).
    extra_body: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    id: str | None = None
    type: str = "function"
    name: str | None = None
    arguments_raw: str = ""
    arguments: dict[str, Any] | None = None
    # Set when arguments_raw is not a valid JSON object (counted as malformed in M4).
    arguments_error: str | None = None


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    source: TokenCountSource = "unavailable"


class Timing(BaseModel):
    """Client-side timing.

    ``started_at`` is taken immediately before the request is sent. ``first_content_at`` is the
    arrival of the first stream event carrying non-empty generated output (content, reasoning,
    or tool-call data); HTTP headers and empty/role-only events do not count. For non-streaming
    requests TTFT is not observable and is ``None`` (spec §5.5).
    Durations come from a monotonic clock; wall-clock timestamps are for correlation only.
    """

    streamed: bool
    started_at: datetime
    first_content_at: datetime | None = None
    ended_at: datetime
    ttft_ms: float | None = None
    latency_ms: float
    # ended - first content; None when no content arrived or not streamed.
    generation_ms: float | None = None
    # Offsets (ms from start) of each stream event that carried output, for inter-token analysis.
    output_event_offsets_ms: list[float] = Field(default_factory=list)


class AdapterError(BaseModel):
    type: ErrorType
    message: str
    status_code: int | None = None
    retryable: bool = False


class ModelResponse(BaseModel):
    request_id: uuid.UUID
    response_id: str | None = None
    endpoint_id: uuid.UUID | None = None
    adapter_type: str
    model: str | None = None  # as reported by the server; not proof of the weights served
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    http_status: int | None = None
    timing: Timing | None = None
    error: AdapterError | None = None
    retry_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    # Sanitised provider metadata for debugging (ids, fingerprints, selected headers).
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


class ConnectionCheck(BaseModel):
    ok: bool
    latency_ms: float | None = None
    http_status: int | None = None
    available_models: list[str] = Field(default_factory=list)
    # True/False if the server lists models; None if the listing was unavailable.
    model_listed: bool | None = None
    error: AdapterError | None = None


class ModelMetadata(BaseModel):
    """Metadata as *reported by the server*. Not verified (spec §5.2)."""

    source: Literal["server_reported"] = "server_reported"
    verified: Literal[False] = False
    model_identifier: str
    model_entry: dict[str, Any] | None = None
    available_models: list[str] = Field(default_factory=list)
    error: AdapterError | None = None


class StreamEvent(BaseModel):
    kind: Literal["content", "reasoning", "tool_call"]
    text: str
    offset_ms: float
