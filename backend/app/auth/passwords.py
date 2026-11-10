"""Password hashing (argon2id) and the password policy."""

from __future__ import annotations

import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.exceptions import AppError

MIN_LENGTH = 12
MAX_LENGTH = 256

_hasher = PasswordHasher()
# Verified when the account has no password, so it takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash("openllm-eval-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        ok = _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return ok and password_hash is not None


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password_hash: str | None, password: str) -> bool:
    return await asyncio.to_thread(verify_password, password_hash, password)


def check_policy(password: str, *, username: str, forbidden: tuple[str, ...] = ()) -> None:
    """Raise a 422 when the password is too weak. Never echoes the password."""
    problems: list[str] = []
    if len(password) < MIN_LENGTH:
        problems.append(f"at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        problems.append(f"at most {MAX_LENGTH} characters")
    if not password.strip() or password.strip() != password:
        problems.append("no leading or trailing spaces")
    if username and username.lower() in password.lower():
        problems.append("must not contain the username")
    if password in forbidden:
        problems.append("must differ from the initial or current password")
    if len(set(password)) < 5:
        problems.append("at least 5 different characters")
    if problems:
        raise AppError(
            "The password does not meet the policy: " + "; ".join(problems) + ".",
            code="weak_password",
            status_code=422,
            details={"requirements": problems},
        )
