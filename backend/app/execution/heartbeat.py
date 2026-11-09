"""Worker heartbeat keys in Redis, shared by the worker (writer) and API readiness (reader)."""

from __future__ import annotations

from redis.asyncio import Redis

HEARTBEAT_PREFIX = "openllm:worker:heartbeat:"


def heartbeat_key(worker_id: str) -> str:
    return f"{HEARTBEAT_PREFIX}{worker_id}"


async def write_heartbeat(redis: Redis, worker_id: str, timestamp: str, ttl_seconds: int) -> None:
    await redis.set(heartbeat_key(worker_id), timestamp, ex=ttl_seconds)


async def clear_heartbeat(redis: Redis, worker_id: str) -> None:
    await redis.delete(heartbeat_key(worker_id))


async def count_live_workers(redis: Redis) -> int:
    count = 0
    async for _ in redis.scan_iter(match=f"{HEARTBEAT_PREFIX}*", count=100):
        count += 1
    return count
