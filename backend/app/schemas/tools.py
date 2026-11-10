from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolInfo(BaseModel):
    name: str
    description: str = ""
    version: str
    kind: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = None
    max_output_bytes: int | None = None


class ToolsStatus(BaseModel):
    # TOOL_EXECUTION_ENABLED on the API/worker.
    enabled: bool
    # The tool runner answered with its registry.
    reachable: bool
    detail: str | None = None
    registry_sha256: str | None = None
    # tools.yaml exists but is invalid (the runner then offers no tools).
    registry_error: str | None = None
    tools: list[ToolInfo] = Field(default_factory=list)
