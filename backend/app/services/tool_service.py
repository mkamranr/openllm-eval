"""Integration-mode checks at run creation: the runner is reachable and declares every tool."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.models.dataset import DatasetCase
from app.execution.tools_client import ToolRunnerClient, ToolRunnerError


async def fetch_registry(settings: Settings) -> tuple[dict[str, Any] | None, str | None]:
    """``(registry, None)`` or ``(None, reason)``."""
    try:
        return await ToolRunnerClient.from_settings(settings).registry(), None
    except ToolRunnerError as exc:
        return None, str(exc)


async def case_tool_names(session: AsyncSession, version_ids: list[uuid.UUID]) -> set[str]:
    """Every tool name offered by the tool_call cases of these dataset versions."""
    contents = await session.scalars(
        select(DatasetCase.content).where(
            DatasetCase.dataset_version_id.in_(version_ids), DatasetCase.case_type == "tool_call"
        )
    )
    names: set[str] = set()
    for content in contents:
        for tool in content.get("tools") or []:
            name = (tool.get("function") or {}).get("name")
            if name:
                names.add(name)
    return names


async def registry_for_run(
    session: AsyncSession, settings: Settings, version_ids: list[uuid.UUID]
) -> dict[str, Any]:
    """The registry snapshot recorded with an integration-mode run.

    Raises 422 when execution is disabled, the runner is unreachable or its tools.yaml is
    invalid, or a tool offered by the datasets is not registered.
    """
    if not settings.tool_execution_enabled:
        raise AppError(
            "Integration mode needs TOOL_EXECUTION_ENABLED=true and the tool runner "
            "(deploy.sh --tools).",
            code="tool_execution_disabled",
            status_code=422,
        )
    registry, error = await fetch_registry(settings)
    if registry is None:
        raise AppError(
            f"The tool runner is not available: {error}",
            code="tool_runner_unavailable",
            status_code=422,
        )
    if registry.get("error"):
        raise AppError(
            f"The tool runner's tools.yaml is invalid: {registry['error']}",
            code="tool_registry_invalid",
            status_code=422,
        )
    declared = {t["name"]: t for t in registry.get("tools") or []}
    needed = await case_tool_names(session, version_ids)
    missing = sorted(needed - declared.keys())
    if missing:
        raise AppError(
            "These tools are offered by the dataset but not declared in the tool runner's "
            f"tools.yaml: {', '.join(missing)}.",
            code="tools_not_registered",
            status_code=422,
            details={"missing_tools": missing},
        )
    return {
        "sha256": registry.get("sha256"),
        "tools": {name: declared[name]["version"] for name in sorted(needed)},
    }
