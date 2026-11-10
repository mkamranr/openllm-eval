"""Client for the tool runner (spec §5.7 mode C), used by the API and the worker."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings

# Time allowed on top of the tool's own timeout for the round trip to the runner.
NETWORK_GRACE_SECONDS = 5.0


class ToolRunnerError(Exception):
    """The tool runner is disabled, unreachable, or rejected the request."""


class ToolRunnerClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._transport = transport

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> ToolRunnerClient:
        if not settings.tool_execution_enabled:
            raise ToolRunnerError("Tool execution is disabled (TOOL_EXECUTION_ENABLED=false).")
        if settings.tool_runner_token is None:
            raise ToolRunnerError("TOOL_RUNNER_TOKEN is not set.")
        return cls(
            settings.tool_runner_url,
            settings.tool_runner_token.get_secret_value(),
            transport=transport,
        )

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.url,
            headers=self._headers,
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            transport=self._transport,
        )

    async def registry(self, limit_seconds: float = 5.0) -> dict[str, Any]:
        """``{"sha256", "tools": [...], "error"}``. Raises :class:`ToolRunnerError`."""
        try:
            async with self._client(limit_seconds) as client:
                response = await client.get("/tools")
        except httpx.HTTPError as exc:
            raise ToolRunnerError(f"The tool runner is unreachable: {exc}") from None
        if response.status_code == 401:
            raise ToolRunnerError("The tool runner rejected TOOL_RUNNER_TOKEN.")
        if not response.is_success:
            raise ToolRunnerError(f"The tool runner returned HTTP {response.status_code}.")
        return response.json()

    async def execute(
        self, tool: str, arguments: dict[str, Any], *, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        """Run one tool call. Never raises: failures come back as ``{"ok": False, "error"}``."""
        body: dict[str, Any] = {"tool": tool, "arguments": arguments}
        if timeout_seconds:
            body["timeout_seconds"] = timeout_seconds
        limit = (timeout_seconds or 60.0) + NETWORK_GRACE_SECONDS
        try:
            async with self._client(limit) as client:
                response = await client.post("/execute", json=body)
        except httpx.HTTPError as exc:
            return _failure(f"The tool runner is unreachable: {exc}")
        if response.status_code == 401:
            return _failure("The tool runner rejected TOOL_RUNNER_TOKEN.")
        if not response.is_success:
            return _failure(f"The tool runner returned HTTP {response.status_code}.")
        try:
            data = response.json()
        except ValueError:
            return _failure("The tool runner returned an invalid response.")
        if not isinstance(data, dict) or "ok" not in data:
            return _failure("The tool runner returned an invalid response.")
        return data


def _failure(message: str) -> dict[str, Any]:
    return {"ok": False, "result": None, "error": message, "duration_ms": None, "truncated": False}
