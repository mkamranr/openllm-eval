from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterContext
from app.adapters.types import ModelMetadata
from app.api.deps import get_adapter_context, get_app_settings, get_session
from app.core.config import Settings
from app.schemas.common import Page
from app.schemas.endpoint import (
    AdapterInfo,
    EndpointClone,
    EndpointCreate,
    EndpointRead,
    EndpointTestRequest,
    EndpointTestResult,
    EndpointUpdate,
)
from app.services import endpoint_service

router = APIRouter(tags=["endpoints"])


@router.get("/adapters", response_model=list[AdapterInfo], summary="Available adapter types")
async def list_adapters() -> list[AdapterInfo]:
    return endpoint_service.list_adapters()


@router.get("/endpoints", response_model=Page[EndpointRead])
async def list_endpoints(
    project_id: uuid.UUID | None = None,
    enabled: bool | None = None,
    name: str | None = Query(None, max_length=200, description="Exact name match"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    context: AdapterContext = Depends(get_adapter_context),
) -> Page[EndpointRead]:
    return await endpoint_service.list_endpoints(
        session,
        context,
        project_id=project_id,
        enabled=enabled,
        name=name,
        limit=limit,
        offset=offset,
    )


@router.post("/endpoints", response_model=EndpointRead, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    data: EndpointCreate,
    session: AsyncSession = Depends(get_session),
    context: AdapterContext = Depends(get_adapter_context),
    settings: Settings = Depends(get_app_settings),
) -> EndpointRead:
    return await endpoint_service.create_endpoint(session, context, settings, data)


@router.get("/endpoints/{endpoint_id}", response_model=EndpointRead)
async def get_endpoint(
    endpoint_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    context: AdapterContext = Depends(get_adapter_context),
) -> EndpointRead:
    return endpoint_service.to_read(
        await endpoint_service.get_endpoint(session, endpoint_id), context
    )


@router.patch("/endpoints/{endpoint_id}", response_model=EndpointRead)
async def update_endpoint(
    endpoint_id: uuid.UUID,
    data: EndpointUpdate,
    session: AsyncSession = Depends(get_session),
    context: AdapterContext = Depends(get_adapter_context),
    settings: Settings = Depends(get_app_settings),
) -> EndpointRead:
    return await endpoint_service.update_endpoint(session, context, settings, endpoint_id, data)


@router.delete("/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(
    endpoint_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    await endpoint_service.delete_endpoint(session, endpoint_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/endpoints/{endpoint_id}/clone",
    response_model=EndpointRead,
    status_code=status.HTTP_201_CREATED,
)
async def clone_endpoint(
    endpoint_id: uuid.UUID,
    data: EndpointClone = Body(default_factory=EndpointClone),
    session: AsyncSession = Depends(get_session),
    context: AdapterContext = Depends(get_adapter_context),
) -> EndpointRead:
    return await endpoint_service.clone_endpoint(session, context, endpoint_id, data)


@router.post(
    "/endpoints/{endpoint_id}/test",
    response_model=EndpointTestResult,
    summary="Test connectivity and send one small generation request",
)
async def test_endpoint(
    endpoint_id: uuid.UUID,
    options: EndpointTestRequest = Body(default_factory=EndpointTestRequest),
    session: AsyncSession = Depends(get_session),
    context: AdapterContext = Depends(get_adapter_context),
) -> EndpointTestResult:
    return await endpoint_service.run_endpoint_test(session, context, endpoint_id, options)


@router.get(
    "/endpoints/{endpoint_id}/metadata",
    response_model=ModelMetadata,
    summary="Model metadata as reported by the server (unverified)",
)
async def endpoint_metadata(
    endpoint_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    context: AdapterContext = Depends(get_adapter_context),
) -> ModelMetadata:
    return await endpoint_service.endpoint_metadata(session, context, endpoint_id)
