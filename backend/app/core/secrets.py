"""Secret references.

API keys are never stored in the database. Endpoints store a *reference* such as
``env:OPENLLM_SECRET_VLLM_KEY``. It is resolved from the process environment only when a
request is sent, and the resolved value never leaves the adapter (no logs, API responses,
snapshots, or reports).

Only variables with the configured prefix (``SECRET_ENV_PREFIX``, default ``OPENLLM_SECRET_``)
can be referenced. Otherwise an endpoint pointed at an attacker's URL could request, for example,
``env:POSTGRES_PASSWORD`` and receive it as a bearer token.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

_REF = re.compile(r"^env:([A-Z_][A-Z0-9_]{0,127})$")


class SecretReferenceError(ValueError):
    pass


def parse_secret_reference(reference: str, prefix: str) -> str:
    """Return the environment variable name for a reference, or raise SecretReferenceError."""
    match = _REF.match(reference.strip())
    if not match:
        raise SecretReferenceError(
            "Secret reference must look like 'env:NAME' (uppercase letters, digits, underscores)."
        )
    name = match.group(1)
    if not name.startswith(prefix) or name == prefix:
        raise SecretReferenceError(
            f"Secret reference must name an environment variable starting with '{prefix}'."
        )
    return name


class SecretResolver:
    def __init__(self, prefix: str, environ: Mapping[str, str] | None = None) -> None:
        self.prefix = prefix
        self._environ = environ if environ is not None else os.environ

    def validate(self, reference: str) -> None:
        parse_secret_reference(reference, self.prefix)

    def resolve(self, reference: str) -> str | None:
        """Resolve a reference to its value. Returns None if the variable is unset or empty."""
        name = parse_secret_reference(reference, self.prefix)
        value = self._environ.get(name)
        return value if value else None

    def is_available(self, reference: str) -> bool:
        try:
            return self.resolve(reference) is not None
        except SecretReferenceError:
            return False
