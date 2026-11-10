"""Encryption at rest for directory secrets (AES-256-GCM, key derived from AUTH_SECRET_KEY)."""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.exceptions import AppError

_VERSION = "v1"
_INFO = b"openllm-eval auth settings v1"
MIN_KEY_LENGTH = 16


class SecretKeyMissingError(AppError):
    status_code = 409
    code = "auth_secret_key_missing"


def _unreadable() -> AppError:
    return AppError(
        "A stored directory secret cannot be decrypted (was AUTH_SECRET_KEY changed?). "
        "Enter it again in Settings > Authentication.",
        code="secret_unreadable",
        status_code=409,
    )


class SecretBox:
    def __init__(self, key_material: str | None) -> None:
        self._aes: AESGCM | None = None
        if key_material and len(key_material) >= MIN_KEY_LENGTH:
            key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(
                key_material.encode("utf-8")
            )
            self._aes = AESGCM(key)

    @property
    def available(self) -> bool:
        return self._aes is not None

    def _require(self) -> AESGCM:
        if self._aes is None:
            raise SecretKeyMissingError(
                f"AUTH_SECRET_KEY is not set (at least {MIN_KEY_LENGTH} characters), so "
                "directory secrets cannot be stored or read. Set it in .env and restart the API."
            )
        return self._aes

    def encrypt(self, plaintext: bytes, *, purpose: str) -> str:
        nonce = os.urandom(12)
        ct = self._require().encrypt(nonce, plaintext, purpose.encode())
        return f"{_VERSION}:" + base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, token: str, *, purpose: str) -> bytes:
        aes = self._require()
        version, _, payload = token.partition(":")
        if version != _VERSION:
            raise _unreadable()
        try:
            raw = base64.b64decode(payload, validate=True)
            return aes.decrypt(raw[:12], raw[12:], purpose.encode())
        except (InvalidTag, binascii.Error, ValueError):
            raise _unreadable() from None
