"""Background telemetry scraping while a model is measured, and the resulting ``gpu.*`` metrics.

Telemetry is optional and best effort: a missing, slow or failing exporter never fails or slows
the run. Each scrape goes through the network policy (like model requests), does not follow
redirects, ignores proxy environment variables and reads at most ``MAX_BYTES``. Exporter URLs
must not embed credentials (rejected at endpoint validation).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.core.network_policy import NetworkPolicy
from app.db.models.run import ResourceSample, RunModel
from app.metrics.performance import MetricValue
from app.metrics.stats import mean
from app.telemetry import prometheus

log = get_logger(__name__)

MAX_BYTES = 4 * 1024 * 1024
TELEMETRY_METRICS_VERSION = "1"


class TelemetryCollector:
    def __init__(
        self,
        config: dict[str, Any],
        policy: NetworkPolicy,
        factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        run_model_id: uuid.UUID,
        status: dict[str, Any],
    ) -> None:
        self.url = str(config["url"])
        self.interval = float(config.get("interval_seconds") or 2.0)
        self.timeout = float(config.get("timeout_seconds") or 2.0)
        self.gpu_indices = config.get("gpu_indices")
        self.source = str(config.get("source") or "prometheus")
        self.policy = policy
        self.factory = factory
        self.run_id = run_id
        self.run_model_id = run_model_id
        # Shared with the model's progress: {"configured", "samples", "failures", "last_error"}.
        self.status = status
        self._task: asyncio.Task[None] | None = None
        self._started = 0.0
        # Set after the first scrape attempt (success or failure).
        self._first_attempt = asyncio.Event()

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        policy: NetworkPolicy,
        factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        run_model_id: uuid.UUID,
        status: dict[str, Any],
    ) -> TelemetryCollector | None:
        config = snapshot.get("telemetry")
        if not config or not config.get("enabled", True) or not config.get("url"):
            return None
        return cls(config, policy, factory, run_id, run_model_id, status)

    async def scrape(self, client: httpx.AsyncClient) -> dict[str, Any]:
        await self.policy.check_destination(self.url)
        async with client.stream("GET", self.url) as response:
            if response.status_code != 200:
                raise RuntimeError(f"exporter returned HTTP {response.status_code}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise RuntimeError("exporter response is too large")
        values = prometheus.extract(
            prometheus.parse(body.decode("utf-8", errors="replace")), self.gpu_indices
        )
        if not values:
            raise RuntimeError("the page exposes no supported GPU or serving metrics")
        return values

    async def _store(self, values: dict[str, Any]) -> None:
        async with self.factory() as session:
            await session.execute(
                insert(ResourceSample).values(
                    id=uuid.uuid4(),
                    run_id=self.run_id,
                    run_model_id=self.run_model_id,
                    captured_at=datetime.now(UTC).replace(tzinfo=None),
                    offset_seconds=round(time.monotonic() - self._started, 3),
                    source=self.source,
                    values=values,
                )
            )
            await session.commit()

    async def _loop(self) -> None:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            while True:
                tick = time.monotonic()
                try:
                    values = await asyncio.wait_for(self.scrape(client), self.timeout + 1)
                    await self._store(values)
                    self.status["samples"] += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # telemetry never fails the run
                    self.status["failures"] += 1
                    message = str(exc) or type(exc).__name__
                    self.status["last_error"] = message[:300]
                    if self.status["failures"] == 1:
                        log.warning(
                            "telemetry_scrape_failed",
                            run_model_id=str(self.run_model_id),
                            error=self.status["last_error"],
                        )
                self._first_attempt.set()
                await asyncio.sleep(max(0.0, self.interval - (time.monotonic() - tick)))

    def start(self) -> None:
        self._started = time.monotonic()
        self.status.update(configured=True, source=self.source, interval_seconds=self.interval)
        self.status.setdefault("samples", 0)
        self.status.setdefault("failures", 0)
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        # A short run may end before the first scrape returns: give that one scrape (bounded by
        # its timeout) the chance to finish, so short runs still get a sample.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._first_attempt.wait(), self.timeout + 1.5)
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None
        if not self.status.get("samples") and not self.status.get("failures"):
            self.status["last_error"] = "no scrape finished while the model was measured"


# ------------------------------------------------------------------------------- metrics

_SERIES = (
    ("gpu.memory_used_mb", "gpu_memory_used_mb", "MB", ("max", "mean")),
    ("gpu.memory_total_mb", "gpu_memory_total_mb", "MB", ("max",)),
    ("gpu.utilization_pct", "gpu_utilization_pct", "%", ("mean", "max")),
    ("gpu.power_w", "gpu_power_w", "W", ("mean", "max")),
    ("gpu.temperature_c", "gpu_temperature_c", "°C", ("max",)),
    ("gpu.kv_cache_usage_pct", "kv_cache_usage_pct", "%", ("mean", "max")),
)


def compute(samples: list[dict[str, Any]], status: dict[str, Any] | None) -> list[MetricValue]:
    """Metrics from stored samples. Empty when telemetry was not configured (absence is not an
    error); explicit nulls with a reason when it was configured but produced nothing."""
    configured = bool(status and status.get("configured"))
    if not samples and not configured:
        return []
    base = {"telemetry_metrics_version": TELEMETRY_METRICS_VERSION}
    out = [
        MetricValue(
            "telemetry.samples",
            "count",
            float(len(samples)),
            "count",
            len(samples),
            base
            | {
                "failures": (status or {}).get("failures"),
                "last_error": (status or {}).get("last_error"),
                "interval_seconds": (status or {}).get("interval_seconds"),
            },
        )
    ]
    for name, key, unit, aggs in _SERIES:
        values = [float(s[key]) for s in samples if isinstance(s.get(key), int | float)]
        if not values:
            reason = (
                "telemetry was configured but no sample was collected"
                if not samples
                else f"the exporter did not report {key}"
            )
            out.extend(
                MetricValue(name, agg, None, unit, 0, base | {"unavailable_reason": reason})
                for agg in aggs
            )
            continue
        for agg in aggs:
            value = max(values) if agg == "max" else mean(values)
            out.append(MetricValue(name, agg, round(value, 3), unit, len(values), dict(base)))
    return out


async def telemetry_metrics(session: AsyncSession, rm: RunModel) -> list[MetricValue]:
    status = (rm.progress or {}).get("telemetry")
    count = await session.scalar(select(func.count()).where(ResourceSample.run_model_id == rm.id))
    if not count and not (status and status.get("configured")):
        return []
    samples = (
        await session.scalars(
            select(ResourceSample.values)
            .where(ResourceSample.run_model_id == rm.id)
            .order_by(ResourceSample.offset_seconds)
        )
    ).all()
    return compute(list(samples), status)
