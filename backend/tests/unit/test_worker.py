from __future__ import annotations

import asyncio

from app.execution.heartbeat import count_live_workers, heartbeat_key
from app.execution.worker import Worker


async def test_worker_heartbeats_and_cleans_up(settings, redis):
    settings.worker_heartbeat_interval_seconds = 0.05
    worker = Worker(settings, redis, worker_id="test-worker")
    task = asyncio.create_task(worker.run())

    for _ in range(50):
        if await redis.exists(heartbeat_key("test-worker")):
            break
        await asyncio.sleep(0.02)
    assert await count_live_workers(redis) == 1
    ttl = await redis.ttl(heartbeat_key("test-worker"))
    assert 0 < ttl <= settings.worker_heartbeat_ttl_seconds

    worker.request_stop()
    await asyncio.wait_for(task, timeout=2)
    assert await count_live_workers(redis) == 0


class _BrokenRedis:
    async def set(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def delete(self, *args, **kwargs):
        raise ConnectionError("redis down")


async def test_worker_survives_redis_outage(settings):
    settings.worker_heartbeat_interval_seconds = 0.01
    worker = Worker(settings, _BrokenRedis(), worker_id="w")  # type: ignore[arg-type]
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)
    assert not task.done()  # still running despite failing heartbeats
    worker.request_stop()
    await asyncio.wait_for(task, timeout=2)
