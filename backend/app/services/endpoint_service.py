"""Model endpoint registry: CRUD, validation, connection tests, metadata."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterContext, EndpointRuntimeConfig, ModelAdapter
from app.adapters.registry import adapter_classes, create_adapter, is_registered
from app.adapters.types import GenerationRequest, ModelMetadata
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.core.network_policy import DestinationBlockedError
from app.core.secrets import SecretReferenceError
from app.db.models import ModelEndpoint
from app.schemas.common import Page
from app.schemas.endpoint import (
    AdapterInfo,
    EndpointClone,
    EndpointCreate,
    EndpointRead,
    EndpointTestRequest,
    EndpointTestResult,
    EndpointUpdate,
    RequestConfiguration,
)
from app.schemas.model_metadata import TelemetryConfiguration
from app.services.project_service import get_project


def _validation_error(message: str, field: str) -> AppError:
    return AppError(
        message,
        code="validation_error",
        status_code=422,
        details=[{"loc": ["body", field], "msg": message}],
    )


def list_adapters() -> list[AdapterInfo]:
    return [
        AdapterInfo(
            adapter_type=cls.adapter_type, display_name=cls.display_name, version=cls.version
        )
        for cls in adapter_classes().values()
    ]


def _validate_fields(
    context: AdapterContext,
    settings: Settings,
    *,
    adapter_type: str,
    base_url: str,
    secret_reference: str | None,
    request_configuration: RequestConfiguration,
    telemetry: TelemetryConfiguration | None = None,
) -> None:
    if telemetry is not None:
        try:
            context.policy.validate_url(telemetry.url)
        except DestinationBlockedError as exc:
            raise _validation_error(str(exc), "telemetry.url") from None
    if not is_registered(adapter_type):
        raise _validation_error(
            f"Unknown adapter type '{adapter_type}'. Available: {sorted(adapter_classes())}",
            "adapter_type",
        )
    try:
        context.policy.validate_url(base_url)
    except DestinationBlockedError as exc:
        raise _validation_error(str(exc), "base_url") from None
    refs = [("secret_reference", secret_reference)] + [
        (f"request_configuration.secret_headers.{name}", ref)
        for name, ref in request_configuration.secret_headers.items()
    ]
    for field, ref in refs:
        if ref is None:
            continue
        try:
            context.secrets.validate(ref)
        except SecretReferenceError as exc:
            raise _validation_error(str(exc), field) from None
    ca = request_configuration.ca_cert_path
    if ca:
        if not settings.ca_cert_dir:
            raise _validation_error(
                "Custom CA certificates are disabled; set CA_CERT_DIR on the server.",
                "request_configuration.ca_cert_path",
            )
        base = Path(settings.ca_cert_dir).resolve()
        target = Path(ca)
        if not target.is_absolute() or not target.resolve().is_relative_to(base):
            raise _validation_error(
                f"CA certificate must be an absolute path inside {settings.ca_cert_dir}.",
                "request_configuration.ca_cert_path",
            )


def _warnings(
    endpoint: ModelEndpoint, rc: RequestConfiguration, context: AdapterContext
) -> list[str]:
    warnings: list[str] = []
    if not rc.verify_tls and endpoint.base_url.lower().startswith("https"):
        warnings.append("TLS certificate verification is disabled for this endpoint.")
    has_secret = bool(endpoint.secret_reference or rc.secret_headers)
    if has_secret and endpoint.base_url.lower().startswith("http://"):
        warnings.append("Credentials will be sent over unencrypted HTTP.")
    for ref in [endpoint.secret_reference, *rc.secret_headers.values()]:
        if ref and not context.secrets.is_available(ref):
            warnings.append(f"Secret {ref} is not set in the server environment.")
    return warnings


def to_read(endpoint: ModelEndpoint, context: AdapterContext) -> EndpointRead:
    rc = RequestConfiguration.model_validate(endpoint.request_configuration or {})
    read = EndpointRead.model_validate(endpoint, from_attributes=True)
    read.request_configuration = rc
    read.telemetry = (
        TelemetryConfiguration.model_validate(endpoint.telemetry_configuration)
        if endpoint.telemetry_configuration
        else None
    )
    read.secret_configured = (
        context.secrets.is_available(endpoint.secret_reference)
        if endpoint.secret_reference
        else None
    )
    read.warnings = _warnings(endpoint, rc, context)
    return read


async def get_endpoint(session: AsyncSession, endpoint_id: uuid.UUID) -> ModelEndpoint:
    endpoint = await session.get(ModelEndpoint, endpoint_id)
    if endpoint is None:
        raise NotFoundError("Endpoint not found", details={"endpoint_id": str(endpoint_id)})
    return endpoint


async def list_endpoints(
    session: AsyncSession,
    context: AdapterContext,
    *,
    project_id: uuid.UUID | None,
    enabled: bool | None,
    name: str | None,
    limit: int,
    offset: int,
) -> Page[EndpointRead]:
    query = select(ModelEndpoint)
    if project_id is not None:
        query = query.where(ModelEndpoint.project_id == project_id)
    if enabled is not None:
        query = query.where(ModelEndpoint.enabled.is_(enabled))
    if name is not None:
        query = query.where(ModelEndpoint.name == name)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (
        await session.scalars(query.order_by(ModelEndpoint.name).limit(limit).offset(offset))
    ).all()
    return Page(items=[to_read(e, context) for e in rows], total=total, limit=limit, offset=offset)


async def _commit_unique(session: AsyncSession, name: str) -> None:
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AppError(
            f"An endpoint named '{name}' already exists.", code="conflict", status_code=409
        ) from None


async def create_endpoint(
    session: AsyncSession, context: AdapterContext, settings: Settings, data: EndpointCreate
) -> EndpointRead:
    _validate_fields(
        context,
        settings,
        adapter_type=data.adapter_type,
        base_url=data.base_url,
        secret_reference=data.secret_reference,
        request_configuration=data.request_configuration,
        telemetry=data.telemetry,
    )
    if data.project_id is not None:
        await get_project(session, data.project_id)
    endpoint = ModelEndpoint(
        project_id=data.project_id,
        name=data.name,
        adapter_type=data.adapter_type,
        base_url=data.base_url.strip(),
        model_identifier=data.model_identifier,
        secret_reference=data.secret_reference or None,
        request_configuration=data.request_configuration.model_dump(mode="json"),
        model_metadata=data.model_metadata,
        deployment_metadata=data.deployment_metadata,
        telemetry_configuration=data.telemetry.model_dump(mode="json") if data.telemetry else None,
        tags=data.tags,
        enabled=data.enabled,
    )
    session.add(endpoint)
    await _commit_unique(session, data.name)
    await session.refresh(endpoint)
    return to_read(endpoint, context)


async def update_endpoint(
    session: AsyncSession,
    context: AdapterContext,
    settings: Settings,
    endpoint_id: uuid.UUID,
    data: EndpointUpdate,
) -> EndpointRead:
    endpoint = await get_endpoint(session, endpoint_id)
    changes = data.model_dump(exclude_unset=True)
    for required in (
        "name",
        "adapter_type",
        "base_url",
        "model_identifier",
        "enabled",
        "request_configuration",
        "model_metadata",
        "deployment_metadata",
        "tags",
    ):
        if required in changes and changes[required] is None:
            raise _validation_error(f"'{required}' cannot be null.", required)
    rc = (
        data.request_configuration
        if data.request_configuration is not None
        else RequestConfiguration.model_validate(endpoint.request_configuration or {})
    )
    secret_reference = (
        (changes["secret_reference"] or None)
        if "secret_reference" in changes
        else endpoint.secret_reference
    )
    _validate_fields(
        context,
        settings,
        adapter_type=changes.get("adapter_type", endpoint.adapter_type),
        base_url=changes.get("base_url", endpoint.base_url),
        secret_reference=secret_reference,
        request_configuration=rc,
        telemetry=data.telemetry if "telemetry" in changes else None,
    )
    if changes.get("project_id") is not None:
        await get_project(session, changes["project_id"])

    for key, value in changes.items():
        if key == "request_configuration":
            endpoint.request_configuration = rc.model_dump(mode="json")
        elif key == "secret_reference":
            endpoint.secret_reference = secret_reference
        elif key == "telemetry":
            endpoint.telemetry_configuration = (
                data.telemetry.model_dump(mode="json") if data.telemetry else None
            )
        else:
            setattr(endpoint, key, value)
    # Connection-relevant changes invalidate the previous test result.
    if changes.keys() & {
        "base_url",
        "model_identifier",
        "adapter_type",
        "secret_reference",
        "request_configuration",
    }:
        endpoint.last_test_status = None
        endpoint.last_tested_at = None
        endpoint.last_test_summary = None
    await _commit_unique(session, endpoint.name)
    await session.refresh(endpoint)
    return to_read(endpoint, context)


async def clone_endpoint(
    session: AsyncSession, context: AdapterContext, endpoint_id: uuid.UUID, data: EndpointClone
) -> EndpointRead:
    source = await get_endpoint(session, endpoint_id)
    name = data.name
    if name is None:
        base = f"{source.name} (copy)"
        name, n = base, 2
        while await session.scalar(select(ModelEndpoint.id).where(ModelEndpoint.name == name)):
            name, n = f"{base} {n}", n + 1
    clone = ModelEndpoint(
        project_id=source.project_id,
        name=name,
        adapter_type=source.adapter_type,
        base_url=source.base_url,
        model_identifier=source.model_identifier,
        secret_reference=source.secret_reference,
        request_configuration=dict(source.request_configuration or {}),
        model_metadata=dict(source.model_metadata or {}),
        deployment_metadata=dict(source.deployment_metadata or {}),
        telemetry_configuration=dict(source.telemetry_configuration)
        if source.telemetry_configuration
        else None,
        tags=list(source.tags or []),
        enabled=source.enabled,
    )
    session.add(clone)
    await _commit_unique(session, name)
    await session.refresh(clone)
    return to_read(clone, context)


async def delete_endpoint(session: AsyncSession, endpoint_id: uuid.UUID) -> None:
    endpoint = await get_endpoint(session, endpoint_id)
    await session.delete(endpoint)
    await session.commit()


def runtime_config(endpoint: ModelEndpoint) -> EndpointRuntimeConfig:
    return EndpointRuntimeConfig(
        endpoint_id=endpoint.id,
        name=endpoint.name,
        base_url=endpoint.base_url,
        model_identifier=endpoint.model_identifier,
        secret_reference=endpoint.secret_reference,
        request=RequestConfiguration.model_validate(endpoint.request_configuration or {}),
    )


def build_adapter(endpoint: ModelEndpoint, context: AdapterContext) -> ModelAdapter:
    return create_adapter(endpoint.adapter_type, runtime_config(endpoint), context)


def _summary(result: EndpointTestResult) -> dict[str, Any]:
    r = result.response
    return {
        "ok": result.ok,
        "error_type": r.error.type if r.error else None,
        "error_message": r.error.message if r.error else None,
        "http_status": r.http_status,
        "latency_ms": r.timing.latency_ms if r.timing else None,
        "ttft_ms": r.timing.ttft_ms if r.timing else None,
        "streamed": r.timing.streamed if r.timing else None,
        "model_reported": r.model,
        "model_listed": result.models_check.model_listed if result.models_check else None,
    }


async def run_endpoint_test(
    session: AsyncSession,
    context: AdapterContext,
    endpoint_id: uuid.UUID,
    options: EndpointTestRequest,
) -> EndpointTestResult:
    endpoint = await get_endpoint(session, endpoint_id)
    config = runtime_config(endpoint)
    stream = options.stream if options.stream is not None else config.request.stream
    request = GenerationRequest(
        messages=[{"role": "user", "content": options.prompt}], max_tokens=options.max_tokens
    )
    async with build_adapter(endpoint, context) as adapter:
        models_check = await adapter.validate_connection() if options.check_models else None
        if stream:
            response = await adapter.generate_stream(request)
        else:
            response = await adapter.generate(request)

    tested_at = datetime.now(UTC)
    result = EndpointTestResult(
        endpoint_id=endpoint.id,
        tested_at=tested_at,
        ok=response.ok,  # /models is informational: some servers do not implement it
        models_check=models_check,
        response=response,
        network_policy=context.policy.describe(),
    )
    endpoint.last_test_status = "ok" if result.ok else "failed"
    endpoint.last_tested_at = tested_at
    endpoint.last_test_summary = _summary(result)
    await session.commit()
    return result


async def endpoint_metadata(
    session: AsyncSession, context: AdapterContext, endpoint_id: uuid.UUID
) -> ModelMetadata:
    endpoint = await get_endpoint(session, endpoint_id)
    async with build_adapter(endpoint, context) as adapter:
        return await adapter.get_model_metadata()
