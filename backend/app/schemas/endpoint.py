"""Model endpoint API schemas."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.adapters.types import ConnectionCheck, ModelResponse
from app.schemas.model_metadata import (
    DEPLOYMENT_FIELDS,
    MODEL_FIELDS,
    TelemetryConfiguration,
    normalize_metadata,
)

_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}$")
# Plain (non-secret) headers that look like credentials are rejected: their values belong in
# a secret. Matching is per name segment so e.g. "X-Mock-First-Token-Delay-Ms" is not flagged.
_SENSITIVE_SEGMENTS = frozenset(
    {
        "authorization",
        "auth",
        "cookie",
        "password",
        "passwd",
        "secret",
        "credential",
        "credentials",
        "session",
        "apikey",
        "bearer",
        "signature",
    }
)
_SENSITIVE_LAST_SEGMENTS = frozenset({"token", "key"})


def is_sensitive_header(name: str) -> bool:
    lowered = name.lower()
    if re.search(r"api[-_]?key", lowered):
        return True
    segments = [s for s in re.split(r"[-_]", lowered) if s]
    return bool(set(segments) & _SENSITIVE_SEGMENTS) or (
        bool(segments) and segments[-1] in _SENSITIVE_LAST_SEGMENTS
    )


# Managed by the HTTP client or the adapter; user values would break or spoof requests.
RESERVED_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "content-type", "accept"}
)
MAX_HEADERS = 32
MAX_METADATA_BYTES = 64 * 1024


def _check_header_name(name: str) -> str:
    if not _HEADER_NAME.match(name):
        raise ValueError(f"Invalid header name: {name!r}")
    if name.lower() in RESERVED_HEADERS:
        raise ValueError(f"Header {name!r} is managed by the client and cannot be set.")
    return name


def _check_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(value, default=str)) > MAX_METADATA_BYTES:
        raise ValueError(f"Metadata must be at most {MAX_METADATA_BYTES} bytes as JSON.")
    return value


def _model_meta(value: dict[str, Any]) -> dict[str, Any]:
    return _check_metadata(normalize_metadata(value, MODEL_FIELDS))


def _deployment_meta(value: dict[str, Any]) -> dict[str, Any]:
    return _check_metadata(normalize_metadata(value, DEPLOYMENT_FIELDS))


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in tags:
        tag = tag.strip()
        if not tag or len(tag) > 64:
            raise ValueError("Tags must be 1-64 characters.")
        if tag not in cleaned:
            cleaned.append(tag)
    if len(cleaned) > 50:
        raise ValueError("At most 50 tags.")
    return cleaned


class RequestConfiguration(BaseModel):
    """Per-endpoint request settings and generation defaults."""

    model_config = ConfigDict(extra="forbid")

    request_timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    max_retries: int = Field(default=1, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=1.0, ge=0, le=300)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    stream: bool = True
    # Ask the server for usage in the final stream chunk (OpenAI `stream_options`).
    stream_include_usage: bool = True
    verify_tls: bool = True
    ca_cert_path: str | None = Field(default=None, max_length=1024)
    # Non-secret custom headers. Header names that look sensitive are rejected here.
    headers: dict[str, str] = Field(default_factory=dict)
    # Header name -> secret reference (e.g. {"X-Api-Key": "env:OPENLLM_SECRET_GATEWAY"}).
    secret_headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("headers")
    @classmethod
    def _validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_HEADERS:
            raise ValueError(f"At most {MAX_HEADERS} headers.")
        for name, header_value in value.items():
            _check_header_name(name)
            if is_sensitive_header(name):
                raise ValueError(
                    f"Header {name!r} looks sensitive; put it in secret_headers with a "
                    "secret reference instead of a plain value."
                )
            if len(header_value) > 4096 or any(c in header_value for c in "\r\n\0"):
                raise ValueError(f"Invalid value for header {name!r}.")
        return value

    @field_validator("secret_headers")
    @classmethod
    def _validate_secret_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_HEADERS:
            raise ValueError(f"At most {MAX_HEADERS} secret headers.")
        for name in value:
            _check_header_name(name)
            if name.lower() == "authorization":
                raise ValueError("Use secret_reference for the Authorization bearer token.")
        return value


class EndpointBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EndpointCreate(EndpointBase):
    project_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    adapter_type: str = Field(default="openai_compatible", max_length=64)
    base_url: str = Field(min_length=1, max_length=2048)
    model_identifier: str = Field(min_length=1, max_length=512)
    secret_reference: str | None = Field(default=None, max_length=256)
    request_configuration: RequestConfiguration = Field(default_factory=RequestConfiguration)
    model_metadata: dict[str, Any] = Field(default_factory=dict)
    deployment_metadata: dict[str, Any] = Field(default_factory=dict)
    telemetry: TelemetryConfiguration | None = None
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True

    _tags = field_validator("tags")(classmethod(lambda cls, v: _clean_tags(v)))
    _model = field_validator("model_metadata")(classmethod(lambda cls, v: _model_meta(v)))
    _deploy = field_validator("deployment_metadata")(
        classmethod(lambda cls, v: _deployment_meta(v))
    )


class EndpointUpdate(EndpointBase):
    """Partial update. ``request_configuration`` replaces the stored object as a whole."""

    project_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    adapter_type: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    model_identifier: str | None = Field(default=None, min_length=1, max_length=512)
    secret_reference: str | None = Field(default=None, max_length=256)
    request_configuration: RequestConfiguration | None = None
    model_metadata: dict[str, Any] | None = None
    deployment_metadata: dict[str, Any] | None = None
    # Explicit null removes the telemetry source.
    telemetry: TelemetryConfiguration | None = None
    tags: list[str] | None = None
    enabled: bool | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _clean_tags(v)

    @field_validator("model_metadata")
    @classmethod
    def _model(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if v is None else _model_meta(v)

    @field_validator("deployment_metadata")
    @classmethod
    def _deploy(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if v is None else _deployment_meta(v)


class EndpointClone(EndpointBase):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class EndpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    adapter_type: str
    base_url: str
    model_identifier: str
    # A reference (variable name), not a secret. The value itself is never returned.
    secret_reference: str | None
    # Whether the referenced secret is set in this server's environment; None if no reference.
    secret_configured: bool | None = None
    request_configuration: RequestConfiguration
    model_metadata: dict[str, Any]
    deployment_metadata: dict[str, Any]
    telemetry: TelemetryConfiguration | None = None
    tags: list[str]
    enabled: bool
    last_test_status: str | None
    last_tested_at: datetime | None
    last_test_summary: dict[str, Any] | None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EndpointTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(default="Reply with the single word: ok", min_length=1, max_length=4000)
    max_tokens: int = Field(default=16, ge=1, le=4096)
    # None = use the endpoint's configured stream setting.
    stream: bool | None = None
    check_models: bool = True


class EndpointTestResult(BaseModel):
    endpoint_id: uuid.UUID
    tested_at: datetime
    ok: bool
    models_check: ConnectionCheck | None
    response: ModelResponse
    network_policy: dict[str, Any]


class AdapterInfo(BaseModel):
    adapter_type: str
    display_name: str
    version: str
