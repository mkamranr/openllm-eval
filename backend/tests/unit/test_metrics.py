from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.metrics import performance
from app.metrics.performance import Observation, compute, final_outcomes, per_request_rates
from app.metrics.stats import distribution, mean, percentile, stdev

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def obs(idx, *, ok=True, attempt=1, final=True, pass_no=1, start=0.0, end=1.0, **kw):
    return Observation(
        request_index=idx,
        attempt_number=attempt,
        pass_number=pass_no,
        is_final=final,
        succeeded=ok,
        started_at=T0 + timedelta(seconds=start),
        completed_at=T0 + timedelta(seconds=end),
        **kw,
    )


def lookup(metrics):
    return performance.as_lookup(metrics)


# ------------------------------------------------------------------------------------ stats


def test_percentiles_use_linear_interpolation():
    values = [10, 20, 30, 40]
    assert percentile(values, 50) == 25
    assert percentile(values, 0) == 10 and percentile(values, 100) == 40
    assert percentile(values, 90) == pytest.approx(37)  # numpy.percentile(..., 90)
    assert percentile([5], 99) == 5
    assert percentile([], 50) is None
    with pytest.raises(ValueError):
        percentile(values, 101)


def test_mean_stdev_distribution_never_invent_zero():
    assert mean([]) is None and stdev([1.0]) is None
    assert stdev([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.138, abs=1e-3)
    assert all(v is None for v in distribution([]).values())
    d = distribution([1, 2, 3])
    assert d["min"] == 1 and d["max"] == 3 and d["p50"] == 2


# ---------------------------------------------------------------------------- per request


def test_per_request_rates_streaming_and_not():
    s = per_request_rates(
        streamed=True,
        latency_ms=500,
        generation_ms=300,
        output_tokens=31,
        output_event_offsets_ms=[100, 200, 400],
    )
    assert s["itl_mean_ms"] == 150
    assert s["output_tokens_per_second"] == pytest.approx(30 / 0.3)  # after the first token
    assert s["output_chunks"] == 3
    n = per_request_rates(
        streamed=False,
        latency_ms=2000,
        generation_ms=None,
        output_tokens=100,
        output_event_offsets_ms=[],
    )
    assert n["output_tokens_per_second"] == 50 and n["itl_mean_ms"] is None
    unknown = per_request_rates(
        streamed=True,
        latency_ms=100,
        generation_ms=50,
        output_tokens=None,
        output_event_offsets_ms=[1, 2],
    )
    assert unknown["output_tokens_per_second"] is None
    single = per_request_rates(
        streamed=True,
        latency_ms=100,
        generation_ms=0,
        output_tokens=1,
        output_event_offsets_ms=[50],
    )
    assert single["output_tokens_per_second"] is None and single["itl_mean_ms"] is None


# ---------------------------------------------------------------------------- aggregation


def test_final_outcome_is_latest_final_attempt():
    finals = final_outcomes(
        [
            obs(0, ok=False, attempt=1, final=False),
            obs(0, ok=True, attempt=2),
            obs(1, ok=False, attempt=1, final=True),
            obs(1, ok=True, attempt=2, pass_no=2),  # retry-failed pass
        ]
    )
    assert finals[0].attempt_number == 2 and finals[1].pass_number == 2


def test_failures_stay_in_denominators_and_latency_uses_successes():
    observations = [
        obs(
            0,
            latency_ms=100,
            ttft_ms=10,
            output_tokens=10,
            input_tokens=5,
            token_source="server_reported",
        ),
        obs(
            1,
            latency_ms=300,
            ttft_ms=30,
            output_tokens=20,
            input_tokens=5,
            token_source="server_reported",
            start=0.5,
            end=2.0,
        ),
        obs(2, ok=False, attempt=1, final=False, error_type="timeout"),
        obs(2, ok=False, attempt=2, error_type="timeout", end=2.0),
        obs(3, ok=False, error_type="http_status", latency_ms=5),
    ]
    m = lookup(compute(observations, planned_requests=5, streamed=True))
    assert m["requests"] == {
        "planned": 5,
        "attempted": 4,
        "succeeded": 2,
        "failed": 2,
        "timed_out": 1,
        "not_completed": 1,
    }
    assert m["error_rate"]["ratio"] == 0.5
    assert m["timeout_rate"]["ratio"] == 0.25
    assert m["retries"]["count"] == 1
    assert m["latency_ms"]["p50"] == 200 and m["latency_ms"]["max"] == 300  # failures excluded
    assert m["ttft_ms"]["mean"] == 20
    assert m["measurement_duration_s"]["value"] == 2.0
    assert m["request_throughput"]["value"] == 1.0  # 2 successes / 2 s
    assert m["output_token_throughput"]["value"] == 15.0
    assert m["total_token_throughput"]["value"] == 20.0
    assert m["output_tokens"]["sum"] == 30


def test_unavailable_values_are_none_with_reasons():
    observations = [
        obs(0, latency_ms=100, output_tokens=None),
        obs(1, latency_ms=120, output_tokens=4),
    ]
    metrics = compute(observations, planned_requests=2, streamed=False)
    by = {(x.name, x.aggregation): x for x in metrics}
    assert by[("ttft_ms", "p50")].value is None
    assert "not observable" in by[("ttft_ms", "p50")].metadata["unavailable_reason"]
    tok = by[("output_token_throughput", "value")]
    assert tok.value is None and "1 of 2" in tok.metadata["unavailable_reason"]
    assert ("inter_token_latency_ms", "p50") not in by

    empty = lookup(compute([], planned_requests=3, streamed=True))
    assert empty["error_rate"]["ratio"] is None
    assert empty["request_throughput"]["value"] is None
    assert empty["requests"]["not_completed"] == 3


def test_throughput_needs_a_contiguous_window():
    observations = [obs(0, latency_ms=10), obs(1, latency_ms=10, pass_no=2)]
    metrics = compute(observations, planned_requests=2, streamed=True)
    by = {(x.name, x.aggregation): x for x in metrics}
    assert by[("request_throughput", "value")].value is None
    assert "not contiguous" in by[("request_throughput", "value")].metadata["unavailable_reason"]
    assert by[("latency_ms", "p50")].value == 10  # latency is still reported
