"""HTTP tools: forward validated arguments to an allowlisted test service.

- The destination must be in ``TOOL_ALLOWED_HOSTS``; it is checked (with DNS resolution)
  before every call. An empty allowlist blocks every HTTP tool.
- Redirects are not followed, proxy variables are ignored, and the response is capped.
- Only literal, non-secret headers declared in tools.yaml are sent.
"""

from __future__ import annotations

import ipaddress
import json
import time
from typing import Any

import httpx

from app.core.network_policy import DestinationBlockedError, NetworkPolicy
from app.toolrunner.limits import ToolResult, truncate_text
from app.toolrunner.registry import ToolSpec


def tool_policy(allowed_hosts: list[str], blocked_hosts: list[str]) -> NetworkPolicy:
    hostnames: list[str] = []
    networks = []
    for entry in allowed_hosts:
        entry = entry.strip().lower()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            hostnames.append(entry)
    return NetworkPolicy(
        allow_private=False,
        allowed_hostnames=tuple(hostnames),
        allowed_networks=tuple(networks),
        blocked_hostnames=tuple(h.strip().lower() for h in blocked_hosts if h.strip()),
    )


def _query(arguments: dict[str, Any]) -> dict[str, str]:
    """GET tools send scalar arguments as query parameters; nested values as JSON text."""
    params = {}
    for key, value in arguments.items():
        if isinstance(value, str):
            params[key] = value
        elif isinstance(value, bool):
            params[key] = "true" if value else "false"
        elif value is None:
            params[key] = ""
        else:
            params[key] = json.dumps(value)
    return params


async def call_http_tool(
    tool: ToolSpec,
    arguments: dict[str, Any],
    *,
    policy: NetworkPolicy,
    timeout: float,  # noqa: ASYNC109 - the tool's own limit, applied to the HTTP client
    transport: httpx.AsyncBaseTransport | None = None,
) -> ToolResult:
    started = time.monotonic()
    if tool.http is None:
        return ToolResult.failure("The tool has no http section.", started)
    if not policy.has_allowlist:
        return ToolResult.failure(
            "HTTP tools are blocked: TOOL_ALLOWED_HOSTS is empty on the tool runner.", started
        )
    try:
        await policy.check_destination(tool.http.url)
    except DestinationBlockedError as exc:
        return ToolResult.failure(f"Destination not allowed: {exc}", started)
    except Exception as exc:  # unresolvable host and similar
        return ToolResult.failure(f"Destination unavailable: {exc}", started)

    headers = {"Accept": "application/json", **tool.http.headers}
    request_kwargs: dict[str, Any] = (
        {"json": arguments} if tool.http.method == "POST" else {"params": _query(arguments)}
    )
    cap = tool.max_output_bytes
    try:
        async with (
            httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
            ) as client,
            client.stream(
                tool.http.method, tool.http.url, headers=headers, **request_kwargs
            ) as response,
        ):
            body = bytearray()
            truncated = False
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > cap:
                    truncated = True
                    del body[cap:]
                    break
    except httpx.TimeoutException:
        return ToolResult.failure(f"The tool timed out after {timeout:g} s.", started)
    except httpx.HTTPError as exc:
        return ToolResult.failure(f"The tool service could not be reached: {exc}", started)

    text = bytes(body).decode(response.encoding or "utf-8", "replace")
    if response.is_redirect:
        return ToolResult.failure(
            f"The tool service redirected (HTTP {response.status_code}); redirects are not "
            "followed.",
            started,
        )
    if not response.is_success:
        return ToolResult.failure(
            f"The tool service returned HTTP {response.status_code}: {truncate_text(text, 300)}",
            started,
        )
    if truncated:
        return ToolResult.failure(
            f"The tool response was larger than {cap} bytes.", started, truncated=True
        )
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            return ToolResult.success(json.loads(text), started)
        except ValueError:
            return ToolResult.failure("The tool service returned invalid JSON.", started)
    return ToolResult.success(text, started)
