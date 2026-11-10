"""Opaque random tokens. Only their SHA-256 digests are stored."""

from __future__ import annotations

import hashlib
import secrets

API_TOKEN_PREFIX = "oel_"  # noqa: S105 (a prefix, not a secret)
SESSION_COOKIE = "oel_session"
CSRF_HEADER = "X-OpenLLM-CSRF"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_api_token() -> str:
    return API_TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_display_prefix(token: str) -> str:
    return token[:10]


def generate_password(length: int = 20) -> str:
    """A random password for resets (letters, digits, and a few symbols)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_.!"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if len(set(value)) >= 5:
            return value
