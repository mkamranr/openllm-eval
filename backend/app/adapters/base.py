"""Model adapter interface (spec §7.6).

Every model call in the system goes through an adapter. An adapter instance is bound to one
endpoint configuration and owns its HTTP connection pool, so callers create one per endpoint
(per run), use it for many requests, and close it.

Adapter contract (enforced by ``tests/contract/test_adapter_contract.py``):

* ``generate`` / ``generate_stream`` never raise for request-level failures. They return a
  ``ModelResponse`` whose ``error`` field is set, so failures are recorded instead of lost.
* Timing follows the definitions on ``Timing``.
* Secrets are resolved at call time and never appear in returned objects or logs.
* Redirects are not followed, and every request is checked against the network policy.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import ClassVar, Self

import httpx

from app.adapters.types import (
    ConnectionCheck,
    GenerationRequest,
    ModelMetadata,
    ModelResponse,
    StreamEvent,
)
from app.core.network_policy import NetworkPolicy
from app.core.secrets import SecretResolver
from app.schemas.endpoint import RequestConfiguration

StreamCallback = Callable[[StreamEvent], Awaitable[None] | None]


@dataclass(frozen=True)
class EndpointRuntimeConfig:
    """The endpoint configuration an adapter needs. It holds secret *references* only."""

    base_url: str
    model_identifier: str
    request: RequestConfiguration = field(default_factory=RequestConfiguration)
    secret_reference: str | None = None
    endpoint_id: uuid.UUID | None = None
    name: str | None = None


@dataclass(frozen=True)
class AdapterContext:
    """Process-wide dependencies shared by all adapters."""

    policy: NetworkPolicy
    secrets: SecretResolver
    max_response_bytes: int
    # Test hook: inject an httpx transport (e.g. httpx.MockTransport).
    transport: httpx.AsyncBaseTransport | None = None


class EmbeddingError(Exception):
    """An embeddings request failed. The message is already scrubbed of secrets."""


class ModelAdapter(ABC):
    adapter_type: ClassVar[str]
    display_name: ClassVar[str]
    version: ClassVar[str]

    def __init__(self, config: EndpointRuntimeConfig, context: AdapterContext) -> None:
        self.config = config
        self.context = context

    @abstractmethod
    async def validate_connection(self) -> ConnectionCheck: ...

    @abstractmethod
    async def get_model_metadata(self) -> ModelMetadata: ...

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> ModelResponse: ...

    @abstractmethod
    async def generate_stream(
        self, request: GenerationRequest, on_event: StreamCallback | None = None
    ) -> ModelResponse: ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embedding vectors for ``texts`` (used by the optional similarity evaluator)."""
        raise EmbeddingError(f"The {self.adapter_type} adapter does not support embeddings.")

    @abstractmethod
    async def close(self) -> None: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
