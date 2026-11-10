"""Failed sign-in counters in Redis: lock a username or client address after repeated failures.

If Redis is unreachable, sign-in still works and the problem is logged; the readiness probe
already reports Redis as down.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _keys(kind: str, value: str) -> tuple[str, str]:
    return f"auth:fail:{kind}:{value}", f"auth:lock:{kind}:{value}"


class Lockout:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.window = settings.auth_lockout_window_seconds
        self.duration = settings.auth_lockout_seconds
        self.limits = {
            "user": settings.auth_lockout_threshold,
            "ip": settings.auth_lockout_ip_threshold,
        }

    @staticmethod
    def _subjects(username: str, ip: str | None) -> list[tuple[str, str]]:
        subjects = [("user", username.lower())]
        if ip:
            subjects.append(("ip", ip))
        return subjects

    async def locked_for(self, username: str, ip: str | None) -> int:
        """Seconds until sign-in is allowed again (0 = not locked)."""
        try:
            remaining = 0
            for kind, value in self._subjects(username, ip):
                ttl = await self.redis.ttl(_keys(kind, value)[1])
                remaining = max(remaining, ttl if ttl and ttl > 0 else 0)
            return remaining
        except Exception as exc:
            logger.warning("auth_lockout_unavailable", error_type=type(exc).__name__)
            return 0

    async def record_failure(self, username: str, ip: str | None) -> None:
        try:
            for kind, value in self._subjects(username, ip):
                counter, lock = _keys(kind, value)
                count = await self.redis.incr(counter)
                if count == 1:
                    await self.redis.expire(counter, self.window)
                if count >= self.limits[kind]:
                    await self.redis.set(lock, "1", ex=self.duration)
                    await self.redis.delete(counter)
                    logger.warning("auth_locked", kind=kind, seconds=self.duration)
        except Exception as exc:
            logger.warning("auth_lockout_unavailable", error_type=type(exc).__name__)

    async def clear_user(self, username: str) -> None:
        """After a successful sign-in, forget earlier failures for this username."""
        try:
            await self.redis.delete(_keys("user", username.lower())[0])
        except Exception as exc:
            logger.warning("auth_lockout_unavailable", error_type=type(exc).__name__)

    async def unlock(self, *, username: str | None = None, ip: str | None = None) -> None:
        if username:
            await self.redis.delete(*_keys("user", username.lower()))
        if ip:
            await self.redis.delete(*_keys("ip", ip))
