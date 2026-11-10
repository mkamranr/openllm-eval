"""The tool runner: a small HTTP service that executes administrator-declared tools.

It runs in its own hardened container (read-only filesystem, no capabilities, non-root, only on
an internal network) and is called by the worker during integration-mode runs. Every request
except ``/health`` needs ``Authorization: Bearer <TOOL_RUNNER_TOKEN>``.

    python -m app.toolrunner.server [--host 0.0.0.0] [--port 8100]
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.toolrunner.http_tool import call_http_tool, tool_policy
from app.toolrunner.limits import ToolResult, argument_error
from app.toolrunner.registry import Registry, RegistryError, load_registry
from app.toolrunner.script_tool import ScriptLimits, run_script

logger = get_logger("app.toolrunner")


class ExecuteRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    # The caller may shorten the declared timeout, never extend it.
    timeout_seconds: float | None = Field(default=None, gt=0, le=60)


@dataclass
class RunnerState:
    settings: Settings
    tools_dir: Path
    filename: str = "tools.yaml"
    registry: Registry = field(default_factory=Registry)
    error: str | None = None
    mtime: float | None = None
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(8))

    def refresh(self) -> None:
        """Reload tools.yaml when it changed (or appeared, or disappeared)."""
        path = self.tools_dir / self.filename
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime == self.mtime and (self.registry.sha256 or self.error):
            return
        self.mtime = mtime
        try:
            self.registry = load_registry(self.tools_dir, self.filename)
            self.error = None
            logger.info(
                "tool_registry_loaded", tools=len(self.registry.tools), sha256=self.registry.sha256
            )
        except RegistryError as exc:
            self.registry = Registry()
            self.error = str(exc)
            logger.warning("tool_registry_invalid", error=self.error)


def create_app(
    settings: Settings | None = None,
    *,
    tools_dir: Path | None = None,
    script_limits: ScriptLimits | None = None,
    http_transport: Any = None,
) -> FastAPI:
    settings = settings or get_settings()
    token = settings.tool_runner_token.get_secret_value() if settings.tool_runner_token else ""
    if len(token) < 16:
        raise RuntimeError("TOOL_RUNNER_TOKEN must be set (at least 16 characters).")
    state = RunnerState(
        settings=settings,
        tools_dir=tools_dir or Path(settings.tools_dir),
        filename=settings.tools_file,
        semaphore=asyncio.Semaphore(settings.tool_runner_max_concurrency),
    )
    state.refresh()
    limits = script_limits or ScriptLimits(memory_mb=settings.tool_script_memory_mb)
    policy = tool_policy(settings.tool_allowed_hosts, settings.endpoint_blocked_hosts)

    app = FastAPI(title="OpenLLM-Eval tool runner", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runner = state

    def authorized(authorization: str | None = Header(default=None)) -> None:
        scheme, _, value = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip(), token):
            raise HTTPException(status_code=401, detail="Invalid tool runner token.")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        state.refresh()
        return {"status": "ok", "registry_ok": state.error is None}

    @app.get("/tools", dependencies=[Depends(authorized)])
    async def list_tools() -> dict[str, Any]:
        state.refresh()
        return state.registry.public() | {"error": state.error}

    @app.post("/execute", dependencies=[Depends(authorized)])
    async def execute(body: ExecuteRequest) -> dict[str, Any]:
        state.refresh()
        started = asyncio.get_running_loop().time()
        tool = state.registry.tools.get(body.tool)
        if tool is None:
            return ToolResult(False, error=f"Tool '{body.tool}' is not registered.").as_dict()
        problem = argument_error(tool.parameters, body.arguments)
        if problem:
            return ToolResult(False, error=problem).as_dict()
        timeout = min(tool.timeout_seconds, body.timeout_seconds or tool.timeout_seconds)
        async with state.semaphore:
            if tool.kind == "script":
                result = await run_script(
                    state.registry.scripts[tool.name],
                    body.arguments,
                    timeout=timeout,
                    max_output_bytes=tool.max_output_bytes,
                    limits=limits,
                )
            else:
                result = await call_http_tool(
                    tool, body.arguments, policy=policy, timeout=timeout, transport=http_transport
                )
        logger.info(
            "tool_executed",
            tool=tool.name,
            ok=result.ok,
            duration_ms=result.duration_ms,
            total_ms=round((asyncio.get_running_loop().time() - started) * 1000, 2),
        )
        return result.as_dict()

    return app


def main() -> None:  # pragma: no cover - container entry point
    import uvicorn

    from app.core.logging import configure_logging

    parser = argparse.ArgumentParser(description="OpenLLM-Eval tool runner")
    parser.add_argument("--host", default=os.environ.get("TOOL_RUNNER_HOST", "0.0.0.0"))  # noqa: S104
    parser.add_argument("--port", type=int, default=int(os.environ.get("TOOL_RUNNER_PORT", "8100")))
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_config=None)


if __name__ == "__main__":  # pragma: no cover
    main()
