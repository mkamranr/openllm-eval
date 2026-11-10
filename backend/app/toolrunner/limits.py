"""Shared result type and argument checks for tool execution."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    result: Any = None
    error: str | None = None
    duration_ms: float | None = None
    truncated: bool = False

    @classmethod
    def success(cls, result: Any, started: float, *, truncated: bool = False) -> ToolResult:
        return cls(True, result=result, duration_ms=_elapsed(started), truncated=truncated)

    @classmethod
    def failure(cls, error: str, started: float, *, truncated: bool = False) -> ToolResult:
        return cls(False, error=error, duration_ms=_elapsed(started), truncated=truncated)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "result": self.result,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
        }


def _elapsed(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 2)


def truncate_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def argument_error(schema: dict[str, Any], arguments: Any) -> str | None:
    """The first JSON Schema violation of ``arguments``, or None when they are valid."""
    if not isinstance(arguments, dict):
        return "Arguments must be a JSON object."
    errors = sorted(Draft202012Validator(schema).iter_errors(arguments), key=str)
    if not errors:
        return None
    first = errors[0]
    where = "/".join(str(p) for p in first.absolute_path)
    return f"Invalid arguments{f' at {where}' if where else ''}: {first.message[:200]}"
