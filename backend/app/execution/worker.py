"""Evaluation worker process.

The worker publishes a heartbeat to Redis (shown by API readiness), claims queued benchmark runs
from the database, and executes one run at a time. Several workers can run side by side: claims
are atomic. On SIGINT/SIGTERM the current run is handed back to the queue (results so far are
kept) and resumed by the next worker.

Run with ``python -m app.execution.worker``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import time
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import __version__
from app.adapters import registry as _adapter_registry  # noqa: F401  (registers adapters)
from app.adapters.base import AdapterContext
from app.core.config import Settings, get_settings
from app.core.lifecycle import create_adapter_context, create_redis
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_session_factory
from app.execution.engine import RunExecutor, claim_next_run
from app.execution.heartbeat import clear_heartbeat, write_heartbeat
from app.execution.queue import wait_for_signal

logger = get_logger(__name__)

# How long a graceful shutdown waits for the current run to hand back its results.
SHUTDOWN_GRACE_SECONDS = 20.0


def make_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class Worker:
    def __init__(
        self,
        settings: Settings,
        redis: Redis,
        worker_id: str | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        adapter_context: AdapterContext | None = None,
    ) -> None:
        self.settings = settings
        self.redis = redis
        self.worker_id = worker_id or make_worker_id()
        # Without a session factory the worker only heartbeats (used in tests).
        self.session_factory = session_factory
        self.adapter_context = adapter_context
        self._stop = asyncio.Event()
        self._executor: RunExecutor | None = None
        self._task: asyncio.Task[str | None] | None = None
        self._last_beat = 0.0

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def request_stop(self) -> None:
        self._stop.set()

    async def _beat(self) -> None:
        try:
            await write_heartbeat(
                self.redis,
                self.worker_id,
                datetime.now(UTC).isoformat(),
                self.settings.worker_heartbeat_ttl_seconds,
            )
            self._last_beat = time.monotonic()
        except Exception as exc:
            # Keep running: Redis may come back. Readiness will show the worker as missing.
            logger.warning("worker_heartbeat_failed", error_type=type(exc).__name__)

    def _reap(self) -> None:
        if self._task is not None and self._task.done():
            if not self._task.cancelled() and (exc := self._task.exception()) is not None:
                logger.error("run_task_crashed", error=f"{type(exc).__name__}: {exc}"[:300])
            self._task = None
            self._executor = None

    async def _try_claim(self) -> None:
        if self.session_factory is None or self.adapter_context is None or self.busy:
            return
        try:
            run_id = await claim_next_run(
                self.session_factory, self.worker_id, self.settings.run_lease_seconds
            )
        except Exception as exc:
            logger.warning("run_claim_failed", error_type=type(exc).__name__)
            return
        if run_id is None:
            return
        logger.info("run_claimed", run_id=str(run_id), worker_id=self.worker_id)
        self._executor = RunExecutor(
            run_id, self.session_factory, self.adapter_context, self.settings, self.worker_id
        )
        self._task = asyncio.create_task(self._executor.execute())

    async def _wait(self) -> None:
        timeout = min(
            self.settings.worker_heartbeat_interval_seconds,
            self.settings.worker_poll_interval_seconds,
        )
        waiters: list[asyncio.Task[object]] = [asyncio.create_task(self._stop.wait())]
        if self.busy:
            waiters.append(self._task)  # type: ignore[arg-type]
        elif self.session_factory is not None:
            waiters.append(asyncio.create_task(wait_for_signal(self.redis, timeout)))
        try:
            await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                if w is not self._task:
                    w.cancel()

    async def run(self) -> None:
        logger.info("worker_started", worker_id=self.worker_id, version=__version__)
        try:
            while not self._stop.is_set():
                if (
                    time.monotonic() - self._last_beat
                    >= self.settings.worker_heartbeat_interval_seconds
                ):
                    await self._beat()
                self._reap()
                await self._try_claim()
                await self._wait()
        finally:
            if self.busy and self._executor is not None and self._task is not None:
                self._executor.stop("shutdown")
                try:
                    await asyncio.wait_for(self._task, timeout=SHUTDOWN_GRACE_SECONDS)
                except Exception as exc:
                    logger.warning("run_handback_incomplete", error_type=type(exc).__name__)
            try:
                await clear_heartbeat(self.redis, self.worker_id)
            except Exception as exc:
                logger.warning("worker_heartbeat_clear_failed", error_type=type(exc).__name__)
            logger.info("worker_stopped", worker_id=self.worker_id)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    redis = create_redis(settings)
    engine = create_engine(settings)
    worker = Worker(
        settings,
        redis,
        session_factory=create_session_factory(engine),
        adapter_context=create_adapter_context(settings),
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:  # Windows event loops do not support signal handlers
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(worker.request_stop))

    try:
        await worker.run()
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
