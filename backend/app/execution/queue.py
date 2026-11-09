"""Run queue wake-ups.

The database is the queue (runs in status ``queued``); workers claim them atomically. Redis only
carries a wake-up signal so idle workers start new runs immediately instead of at their next
poll. Losing a signal is harmless: workers poll the database every
``worker_poll_interval_seconds``.
"""

from __future__ import annotations

import asyncio
import uuid

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger(__name__)

QUEUE_KEY = "openllm:runs:wakeup"
MAX_SIGNALS = 1000


async def notify_run_queued(redis: Redis, run_id: uuid.UUID) -> None:
    try:
        await redis.lpush(QUEUE_KEY, str(run_id))
        await redis.ltrim(QUEUE_KEY, 0, MAX_SIGNALS - 1)
    except Exception as exc:  # the worker's database poll still finds the run
        logger.warning("run_queue_notify_failed", error_type=type(exc).__name__)


async def wait_for_signal(redis: Redis, timeout_seconds: float) -> None:
    """Return when a run may be waiting, or after ``timeout_seconds``."""
    try:
        await redis.blpop([QUEUE_KEY], timeout=max(1, int(timeout_seconds)))
    except Exception:
        await asyncio.sleep(timeout_seconds)
