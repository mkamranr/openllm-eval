"""Integration-mode tool execution status (spec §5.7 mode C)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_app_settings
from app.core.config import Settings
from app.schemas.tools import ToolInfo, ToolsStatus
from app.services import tool_service

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("/status", response_model=ToolsStatus, summary="Tool runner status and registry")
async def tools_status(
    settings: Settings = Depends(get_app_settings),
) -> ToolsStatus:
    """Whether integration mode is available, and which tools the runner declares. Tool
    definitions are read-only here: they come from tools.yaml on the server."""
    if not settings.tool_execution_enabled:
        return ToolsStatus(enabled=False, reachable=False, detail="TOOL_EXECUTION_ENABLED=false")
    registry, error = await tool_service.fetch_registry(settings)
    if registry is None:
        return ToolsStatus(enabled=True, reachable=False, detail=error)
    return ToolsStatus(
        enabled=True,
        reachable=True,
        registry_sha256=registry.get("sha256"),
        registry_error=registry.get("error"),
        tools=[ToolInfo.model_validate(t) for t in registry.get("tools") or []],
    )
