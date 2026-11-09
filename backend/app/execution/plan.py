"""Resolve a load configuration into a concrete, recorded execution plan (spec §5.5).

The resolved plan is stored in the run's configuration snapshot, and the executor reads only
the snapshot, so a run always executes exactly what it recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.run import SMOKE_MAX_REQUESTS, LoadConfig


class PlanError(ValueError):
    """The configuration is valid on its own but not for this dataset or these limits."""


@dataclass(frozen=True)
class Step:
    """A slice of measured requests executed at one concurrency.

    ``step`` is the ramp step number (``None`` for single-step profiles). Request indices are
    ``start .. start + count - 1``; ``count`` is ``None`` for an uncapped sustained run, which
    stops after ``duration_seconds``.
    """

    step: int | None
    concurrency: int
    start: int
    count: int | None
    duration_seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "concurrency": self.concurrency,
            "start": self.start,
            "count": self.count,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Step:
        return cls(
            step=data.get("step"),
            concurrency=int(data["concurrency"]),
            start=int(data.get("start", 0)),
            count=data.get("count"),
            duration_seconds=data.get("duration_seconds"),
        )


def resolve_load(
    load: LoadConfig,
    *,
    case_count: int,
    max_requests: int,
    max_concurrency: int,
    max_duration_seconds: int,
) -> dict[str, Any]:
    if case_count < 1:
        raise PlanError("The selected datasets have no cases.")
    concurrency = 1 if load.profile == "sequential" else load.concurrency
    steps: list[Step]
    if load.profile == "smoke":
        requests = load.requests or min(SMOKE_MAX_REQUESTS, case_count)
        steps = [Step(None, min(concurrency, requests), 0, requests)]
    elif load.profile in ("sequential", "fixed_concurrency"):
        requests = load.requests or case_count
        steps = [Step(None, concurrency, 0, requests)]
    elif load.profile == "ramp":
        per_step = load.requests_per_step or case_count
        steps = [Step(i, c, i * per_step, per_step) for i, c in enumerate(load.ramp_concurrency)]
        concurrency = max(load.ramp_concurrency)
    else:  # sustained
        assert load.duration_seconds is not None  # noqa: S101  (schema guarantees it)
        if load.duration_seconds > max_duration_seconds:
            raise PlanError(f"duration_seconds may be at most {max_duration_seconds}.")
        steps = [Step(None, concurrency, 0, load.requests, load.duration_seconds)]

    total = None if any(s.count is None for s in steps) else sum(s.count or 0 for s in steps)
    if total is not None and total > max_requests:
        raise PlanError(f"At most {max_requests} measured requests per model (got {total}).")
    if load.profile == "sustained" and load.requests and load.requests > max_requests:
        raise PlanError(f"At most {max_requests} measured requests per model.")
    if max(s.concurrency for s in steps) > max_concurrency:
        raise PlanError(f"Concurrency may be at most {max_concurrency}.")

    return {
        "profile": load.profile,
        "concurrency": concurrency,
        "requests": total,
        "warmup_requests": load.warmup_requests,
        "stream": load.stream,
        "request_timeout_seconds": load.request_timeout_seconds,
        "max_retries": load.max_retries,
        "duration_seconds": load.duration_seconds,
        "steps": [s.as_dict() for s in steps],
        "case_count": case_count,
        "case_order": "dataset order; request i uses case i mod case_count",
        "warmup_policy": "sequential, before measured requests of each model, never retried, "
        "excluded from metrics",
        "endpoint_order": "endpoints run one after another, never concurrently",
    }


def steps_of(load: dict[str, Any]) -> list[Step]:
    return [Step.from_dict(s) for s in load["steps"]]
