"""The tool registry: tools an administrator declared in ``tools.yaml`` (spec §12.6).

Tools cannot be added through the UI or the API. The file is mounted read-only into the
tool-runner container, so adding a tool means editing a reviewed file on the server.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
MAX_TIMEOUT_SECONDS = 60
MAX_OUTPUT_BYTES = 1024 * 1024
# Header names that carry credentials. Tools must not hold secrets: the registry is returned to
# the worker and recorded with runs.
FORBIDDEN_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "x-api-key", "api-key", "x-auth-token"}
)
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class RegistryError(Exception):
    """tools.yaml is missing or invalid; the message says where."""


class HttpToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2000)
    method: Literal["POST", "GET"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("method", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("headers")
    @classmethod
    def _headers(cls, value: dict[str, str]) -> dict[str, str]:
        for name, header_value in value.items():
            if not _HEADER_NAME.match(name):
                raise ValueError(f"invalid header name '{name}'")
            if name.lower() in FORBIDDEN_HEADERS:
                raise ValueError(
                    f"header '{name}' would carry a credential; tools must not hold secrets"
                )
            if header_value.strip().lower().startswith("env:") or "\n" in header_value:
                raise ValueError(f"header '{name}' must be a literal, non-secret value")
        return value


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_PATTERN)
    description: str = Field(default="", max_length=2000)
    version: str = Field(default="1", min_length=1, max_length=50)
    kind: Literal["http", "script"]
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    timeout_seconds: float = Field(default=10, gt=0, le=MAX_TIMEOUT_SECONDS)
    max_output_bytes: int = Field(default=65_536, ge=256, le=MAX_OUTPUT_BYTES)
    http: HttpToolConfig | None = None
    # Path of a Python script, relative to the tools directory (e.g. scripts/calculator.py).
    script: str | None = Field(default=None, max_length=500)

    @field_validator("version", mode="before")
    @classmethod
    def _version_str(cls, value: object) -> object:
        return str(value) if isinstance(value, int | float) else value

    @field_validator("parameters")
    @classmethod
    def _schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as exc:
            raise ValueError(f"parameters is not a valid JSON Schema: {exc.message}") from None
        if value.get("type", "object") != "object":
            raise ValueError("parameters must describe an object (type: object)")
        return value

    @model_validator(mode="after")
    def _kind_fields(self) -> ToolSpec:
        if self.kind == "http" and (self.http is None or self.script is not None):
            raise ValueError("an http tool needs an 'http' section and no 'script'")
        if self.kind == "script" and (self.script is None or self.http is not None):
            raise ValueError("a script tool needs 'script' and no 'http' section")
        return self

    def public(self) -> dict[str, Any]:
        """What the worker, the API and run snapshots see: no URLs or headers."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "kind": self.kind,
            "parameters": self.parameters,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
        }


class _RegistryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: list[ToolSpec] = Field(default_factory=list, max_length=200)


@dataclass(frozen=True)
class Registry:
    tools: dict[str, ToolSpec] = field(default_factory=dict)
    sha256: str | None = None
    # Absolute paths of script tools, resolved inside the tools directory.
    scripts: dict[str, Path] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "tools": [t.public() for t in sorted(self.tools.values(), key=lambda t: t.name)],
        }


def _format_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first["loc"])
    return f"{loc}: {first['msg']}" if loc else first["msg"]


def parse_registry(raw: bytes, tools_dir: Path) -> Registry:
    """Validate the contents of tools.yaml. Raises :class:`RegistryError`."""
    try:
        data = yaml.safe_load(raw.decode("utf-8")) if raw.strip() else {}
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise RegistryError(f"tools.yaml is not valid YAML: {exc}") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise RegistryError("tools.yaml must be a mapping with a 'tools' list")
    try:
        parsed = _RegistryFile.model_validate(data)
    except ValidationError as exc:
        raise RegistryError(f"tools.yaml: {_format_error(exc)}") from None

    tools: dict[str, ToolSpec] = {}
    scripts: dict[str, Path] = {}
    root = tools_dir.resolve()
    for tool in parsed.tools:
        if tool.name in tools:
            raise RegistryError(f"tools.yaml: tool '{tool.name}' is declared twice")
        tools[tool.name] = tool
        if tool.kind == "script" and tool.script is not None:
            path = (root / tool.script).resolve()
            if not path.is_relative_to(root):
                raise RegistryError(
                    f"tools.yaml: tool '{tool.name}': script must be inside the tools directory"
                )
            if path.suffix != ".py" or not path.is_file():
                raise RegistryError(
                    f"tools.yaml: tool '{tool.name}': script '{tool.script}' is not a .py file"
                )
            scripts[tool.name] = path
    return Registry(tools=tools, sha256=hashlib.sha256(raw).hexdigest(), scripts=scripts)


def load_registry(tools_dir: Path, filename: str = "tools.yaml") -> Registry:
    path = tools_dir / filename
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise RegistryError(f"{path} not found") from None
    except OSError as exc:
        raise RegistryError(f"{path} could not be read: {exc}") from None
    return parse_registry(raw, tools_dir)
