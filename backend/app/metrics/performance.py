"""Performance metrics from per-request observations (spec §5.5).

Definitions (also returned in each metric's metadata so reports can state them):

* A *request* is one planned measured request (``request_index``). It may have several
  attempts. Its outcome is its final attempt: the success, or the failure after the last
  permitted retry. Warm-up requests are never passed in here.
* ``error_rate`` = failed requests / requests with an outcome. Retries do not hide failures.
  A request that succeeded on retry counts as succeeded, and the retry is counted in ``retries``.
* Latency, TTFT, and token metrics use successful requests only, so failures can't distort them.
  Failures are reported through the counts and rates.
* Throughput uses the measurement window: from the first measured attempt's start to the last
  one's end, within the original execution pass. That includes client-side queueing at the
  configured concurrency. If a run was resumed or retried, the window isn't contiguous and
  throughput is ``None``.
* Unknown values are ``None`` with a reason, never 0.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from typing import Any

from app.metrics.stats import PERCENTILE_METHOD, distribution, mean, percentile

METRICS_VERSION = "1"
TIMEOUT_ERRORS = frozenset({"timeout", "connect_timeout"})


@dataclass(frozen=True)
class Observation:
    request_index: int
    attempt_number: int
    pass_number: int
    is_final: bool
    succeeded: bool
    started_at: datetime
    completed_at: datetime
    error_type: str | None = None
    latency_ms: float | None = None
    ttft_ms: float | None = None
    generation_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    token_source: str = "unavailable"  # noqa: S105  (not a secret)
    output_tokens_per_second: float | None = None
    itl_mean_ms: float | None = None


@dataclass(frozen=True)
class MetricValue:
    name: str
    aggregation: str
    value: float | None
    unit: str
    sample_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


def per_request_rates(
    *,
    streamed: bool,
    latency_ms: float | None,
    generation_ms: float | None,
    output_tokens: int | None,
    output_event_offsets_ms: Sequence[float],
) -> dict[str, float | int | None]:
    """Per-request derived values stored with each result.

    * ``itl_mean_ms``: mean gap between consecutive stream events that carried output. Servers
      may pack several tokens into one event, so this is inter-*chunk* latency, an upper bound on
      per-token latency.
    * ``output_tokens_per_second``: streaming = (output_tokens - 1) / (last output - first
      output), the decode rate after the first token. Non-streaming = output_tokens / latency,
      which includes prefill and network time.
    """
    chunks = len(output_event_offsets_ms)
    gaps = [b - a for a, b in pairwise(output_event_offsets_ms)]
    itl = mean(gaps) if gaps else None
    tps: float | None = None
    if output_tokens is not None:
        if streamed:
            span = output_event_offsets_ms[-1] - output_event_offsets_ms[0] if chunks >= 2 else None
            if span and span > 0 and output_tokens >= 2:
                tps = (output_tokens - 1) / (span / 1000)
        elif latency_ms and latency_ms > 0 and output_tokens > 0:
            tps = output_tokens / (latency_ms / 1000)
    return {
        "output_chunks": chunks if streamed else None,
        "itl_mean_ms": itl,
        "output_tokens_per_second": tps,
        "generation_ms": generation_ms,
    }


def final_outcomes(observations: Iterable[Observation]) -> dict[int, Observation]:
    """The deciding attempt per request: the latest final attempt (by pass, then attempt)."""
    finals: dict[int, Observation] = {}
    for o in observations:
        if not o.is_final:
            continue
        best = finals.get(o.request_index)
        if best is None or (o.pass_number, o.attempt_number) > (
            best.pass_number,
            best.attempt_number,
        ):
            finals[o.request_index] = o
    return finals


def compute(
    observations: Sequence[Observation],
    *,
    planned_requests: int | None,
    streamed: bool,
) -> list[MetricValue]:
    """Aggregate metrics for one group of measured requests (one model, optionally one step)."""
    out: list[MetricValue] = []

    def add(name: str, agg: str, value: float | None, unit: str, n: int, **meta: Any) -> None:
        if value is not None and (math.isnan(value) or math.isinf(value)):
            value = None
        out.append(MetricValue(name, agg, value, unit, n, meta))

    finals = final_outcomes(observations)
    outcomes = list(finals.values())
    ok = [o for o in outcomes if o.succeeded]
    failed = [o for o in outcomes if not o.succeeded]
    timeouts = [o for o in failed if o.error_type in TIMEOUT_ERRORS]
    attempts_by_request: dict[int, int] = {}
    for o in observations:
        attempts_by_request[o.request_index] = attempts_by_request.get(o.request_index, 0) + 1
    retries = sum(n - 1 for n in attempts_by_request.values())
    attempted = len(outcomes)

    add("requests", "planned", planned_requests, "count", 0)
    add("requests", "attempted", attempted, "count", attempted)
    add("requests", "succeeded", len(ok), "count", attempted)
    add("requests", "failed", len(failed), "count", attempted)
    add("requests", "timed_out", len(timeouts), "count", attempted)
    not_completed = None if planned_requests is None else max(planned_requests - attempted, 0)
    add("requests", "not_completed", not_completed, "count", 0)
    add("retries", "count", retries, "count", len(observations), definition="extra attempts")
    rate_meta = {
        "definition": "failed requests / requests with an outcome (final attempt)",
        "retry_policy": "a request counts as failed only after its last permitted retry",
    }
    add(
        "error_rate",
        "ratio",
        len(failed) / attempted if attempted else None,
        "ratio",
        attempted,
        **rate_meta,
    )
    add(
        "timeout_rate",
        "ratio",
        len(timeouts) / attempted if attempted else None,
        "ratio",
        attempted,
    )
    errors: dict[str, int] = {}
    for o in failed:
        errors[o.error_type or "unknown"] = errors.get(o.error_type or "unknown", 0) + 1
    add("errors", "by_type", len(failed), "count", attempted, by_type=errors)

    sample = "successful measured requests (final attempt)"

    def dist(
        name: str,
        values: list[float],
        unit: str,
        *,
        reason: str | None = None,
        aggs: Sequence[str] | None = None,
        **meta: Any,
    ) -> None:
        summary = distribution(values)
        extra = {"sample": sample, "percentile_method": PERCENTILE_METHOD, **meta}
        if not values and reason:
            extra["unavailable_reason"] = reason
        for agg, value in summary.items():
            if aggs is None or agg in aggs:
                add(name, agg, value, unit, len(values), **extra)

    dist(
        "latency_ms",
        [o.latency_ms for o in ok if o.latency_ms is not None],
        "ms",
        definition="client-observed end-to-end latency, request sent to response complete",
    )
    dist(
        "ttft_ms",
        [o.ttft_ms for o in ok if o.ttft_ms is not None],
        "ms",
        reason=None if streamed else "TTFT is not observable without streaming",
        definition="request sent to first non-empty generated output (content, reasoning, "
        "or tool-call data)",
    )
    if streamed:
        dist(
            "generation_ms",
            [o.generation_ms for o in ok if o.generation_ms is not None],
            "ms",
            aggs=("mean", "p50", "p95"),
            definition="first output to response complete",
        )
        dist(
            "inter_token_latency_ms",
            [o.itl_mean_ms for o in ok if o.itl_mean_ms is not None],
            "ms",
            aggs=("mean", "p50", "p95", "p99"),
            definition="per-request mean gap between stream events carrying output "
            "(inter-chunk; servers may batch tokens per event)",
        )
    dist(
        "output_tokens_per_second",
        [o.output_tokens_per_second for o in ok if o.output_tokens_per_second is not None],
        "tokens/s",
        aggs=("mean", "p50", "p95", "min"),
        reason="output token counts unavailable",
        definition="(output_tokens - 1) / (last output - first output) per request"
        if streamed
        else "output_tokens / latency per request (includes prefill and network)",
    )

    token_sources: dict[str, int] = {}
    for o in ok:
        token_sources[o.token_source] = token_sources.get(o.token_source, 0) + 1
    for name, attr in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")):
        values = [float(getattr(o, attr)) for o in ok if getattr(o, attr) is not None]
        meta = {"sample": sample, "token_count_source": token_sources}
        add(name, "mean", mean(values), "tokens", len(values), **meta)
        add(name, "p50", percentile(values, 50), "tokens", len(values), **meta)
        add(name, "max", max(values) if values else None, "tokens", len(values), **meta)
        add(name, "sum", math.fsum(values) if values else None, "tokens", len(values), **meta)

    # --- Throughput over the measurement window ---
    first_pass = [o for o in observations if o.pass_number == 1]
    contiguous = bool(outcomes) and all(o.pass_number == 1 for o in outcomes)
    window_s: float | None = None
    reason: str | None = None
    if not outcomes:
        reason = "no measured requests completed"
    elif not contiguous:
        reason = "the run was resumed or retried, so the measurement window is not contiguous"
    else:
        start = min(o.started_at for o in first_pass)
        end = max(o.completed_at for o in first_pass)
        window_s = (end - start).total_seconds()
        if window_s <= 0:
            window_s, reason = None, "measurement window too short to time"
    window_meta: dict[str, Any] = {
        "window": "first measured request sent to last measured response, including "
        "client-side queueing at the configured concurrency",
    }
    if reason:
        window_meta["unavailable_reason"] = reason
    add("measurement_duration_s", "value", window_s, "s", attempted, **window_meta)
    add(
        "request_throughput",
        "value",
        len(ok) / window_s if window_s else None,
        "requests/s",
        len(ok),
        definition="successful requests / measurement window",
        **window_meta,
    )

    def token_throughput(name: str, attrs: Sequence[str], definition: str) -> None:
        meta = dict(window_meta, definition=definition, token_count_source=token_sources)
        value: float | None = None
        if window_s and ok:
            missing = [o for o in ok if any(getattr(o, a) is None for a in attrs)]
            if missing:
                meta["unavailable_reason"] = (
                    f"token counts unavailable for {len(missing)} of {len(ok)} requests"
                )
            else:
                value = math.fsum(sum(getattr(o, a) for a in attrs) for o in ok) / window_s
        add(name, "value", value, "tokens/s", len(ok), **meta)

    token_throughput(
        "output_token_throughput",
        ("output_tokens",),
        "sum of output tokens of successful requests / measurement window",
    )
    token_throughput(
        "total_token_throughput",
        ("input_tokens", "output_tokens"),
        "sum of input + output tokens of successful requests / measurement window",
    )
    return out


def as_lookup(metrics: Iterable[MetricValue]) -> dict[str, dict[str, float | None]]:
    """``{name: {aggregation: value}}`` for quick summaries."""
    result: dict[str, dict[str, float | None]] = {}
    for m in metrics:
        result.setdefault(m.name, {})[m.aggregation] = m.value
    return result
