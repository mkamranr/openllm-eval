"""Milestone 7 units: needle generator, needle evaluator, context metrics, error classification,
structured metadata, and the Prometheus telemetry parser."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.adapters.openai_compatible import classify_error_message
from app.datasets.generators import (
    MAX_TOTAL_TOKENS,
    NeedleParams,
    estimate_tokens,
    generate_needle_cases,
    to_jsonl,
)
from app.datasets.schema import LongContextCase
from app.evaluators.base import EvaluationContext, ModelOutput
from app.evaluators.context import NeedleRetrieval, position_bucket
from app.metrics import context as ctx
from app.schemas.endpoint import EndpointCreate
from app.schemas.model_metadata import (
    DEPLOYMENT_FIELDS,
    MODEL_FIELDS,
    TelemetryConfiguration,
    normalize_metadata,
    variant_fields,
)
from app.telemetry import collector, prometheus

# ------------------------------------------------------------------------------ generator


def test_generator_is_deterministic_and_sized():
    params = NeedleParams(context_lengths=[2048, 512], depths=[0.9, 0.1], repeats=2, seed=3)
    a, b = generate_needle_cases(params), generate_needle_cases(params)
    assert to_jsonl(a) == to_jsonl(b)
    assert to_jsonl(a) != to_jsonl(generate_needle_cases(params.model_copy(update={"seed": 4})))
    assert [c["id"] for c in a[:4]] == [
        "ctx-512-d0.1-1",
        "ctx-512-d0.1-2",
        "ctx-512-d0.9-1",
        "ctx-512-d0.9-2",
    ]
    for case in a:
        LongContextCase.model_validate(case)
        assert case["expected_answer"] in case["context"]
        est = case["context_estimate"]["tokens"]
        assert est == estimate_tokens(case["context"]) + estimate_tokens(case["input"])
        assert abs(est - case["context_length"]) / case["context_length"] < 0.2
    shallow = next(c for c in a if c["id"] == "ctx-2048-d0.1-1")
    deep = next(c for c in a if c["id"] == "ctx-2048-d0.9-1")
    assert shallow["answer_location"]["approximate_position"] < 0.25
    assert deep["answer_location"]["approximate_position"] > 0.75


def test_multi_fact_cases():
    cases = generate_needle_cases(NeedleParams(context_lengths=[1024], needles_per_case=4))
    assert len(cases) == 1
    case = cases[0]
    assert len(case["expected_answers"]) == 4
    assert "expected_answer" not in case
    assert all(code in case["context"] for code in case["expected_answers"])
    assert case["answer_location"]["requested_positions"] == [0.2, 0.4, 0.6, 0.8]


@pytest.mark.parametrize(
    "params",
    [
        {"context_lengths": [64]},
        {"context_lengths": [600_000]},
        {"context_lengths": [1024], "depths": [1.5]},
        {"context_lengths": [1024], "needles_per_case": 11},
    ],
)
def test_generator_rejects_bad_params(params):
    with pytest.raises(ValidationError):
        NeedleParams(**params)


def test_generator_total_size_limit():
    params = NeedleParams(context_lengths=[524_288], depths=[0.5], repeats=12)
    assert 524_288 * 12 > MAX_TOTAL_TOKENS
    with pytest.raises(ValueError, match="tokens"):
        generate_needle_cases(params)


def test_long_context_case_requires_an_answer():
    with pytest.raises(ValidationError, match="expected_answer"):
        LongContextCase.model_validate(
            {"id": "x", "type": "long_context", "input": "q?", "context": "text"}
        )


# ------------------------------------------------------------------------------ evaluator


async def _evaluate(case, answer):
    return await NeedleRetrieval().evaluate(case, ModelOutput(content=answer), EvaluationContext())


async def test_needle_retrieval_scores():
    case = {
        "type": "long_context",
        "expected_answers": ["4821-KQ", "1000-AB"],
        "context_length": 4096,
        "answer_location": {"approximate_position": 0.8},
    }
    full = await _evaluate(case, "The codes are 4821-kq and 1000-AB.")
    assert full.passed and full.score == 1 and full.label == "retrieved"
    assert full.metadata["position_bucket"] == "late"
    assert full.metadata["context_length"] == 4096
    half = await _evaluate(case, "4821-KQ")
    assert not half.passed and half.score == 0.5 and half.label == "partial"
    none = await _evaluate(case, "I do not know.")
    assert none.score == 0 and none.label == "missed"
    single = await _evaluate({"type": "long_context", "expected_answer": "Blue"}, "blue.")
    assert single.passed
    assert single.metadata["position_bucket"] is None


def test_position_buckets():
    assert position_bucket(0.1) == "early"
    assert position_bucket(0.5) == "middle"
    assert position_bucket(0.9) == "late"
    assert position_bucket(None) is None


# ------------------------------------------------------------------------------ metrics


def _obs(length, ok=True, **kw):
    base = {
        "context_length": length,
        "succeeded": ok,
        "error_type": None if ok else "context_overflow",
        "latency_ms": 100.0 if ok else None,
        "ttft_ms": 40.0 if ok else None,
        "input_tokens": length if ok else None,
        "token_source": "server_reported" if ok else "unavailable",
        "estimated_tokens": length,
    }
    return ctx.ContextObservation(**(base | kw))


def _index(rows):
    return {(n, m.name, m.aggregation): m for n, m in rows}


def test_context_metrics_per_length():
    rows = ctx.compute(
        [
            _obs(1000, retrieved=True, recall=1.0, position_bucket="early"),
            _obs(1000, retrieved=False, recall=0.0, position_bucket="late"),
            _obs(1000, input_tokens=300),  # far fewer tokens than sent: truncation suspected
            _obs(8000, ok=False),
            _obs(8000, ok=False, error_type="out_of_memory"),
        ]
    )
    idx = _index(rows)
    assert {n for n, _ in rows} == {1000, 8000}
    assert idx[(1000, "context.success_rate", "ratio")].value == 1
    assert idx[(1000, "context.truncation_suspected", "ratio")].value == pytest.approx(1 / 3)
    assert idx[(1000, "context.retrieval_accuracy", "pass_rate")].value == 0.5
    assert idx[(1000, "context.retrieval_accuracy", "early")].value == 1
    assert idx[(1000, "context.retrieval_accuracy", "late")].value == 0
    middle = idx[(1000, "context.retrieval_accuracy", "middle")]
    assert middle.value is None and "middle" in middle.metadata["unavailable_reason"]
    assert idx[(1000, "context.fact_recall", "mean")].value == 0.5
    assert idx[(8000, "context.overflow_rate", "ratio")].value == 0.5
    assert idx[(8000, "context.oom_rate", "ratio")].value == 0.5
    assert idx[(8000, "context.success_rate", "ratio")].value == 0
    for key in [
        (8000, "context.ttft_ms", "p50"),
        (8000, "context.input_tokens", "mean"),
        (8000, "context.actual_to_estimated", "mean"),
        (8000, "context.retrieval_accuracy", "pass_rate"),
    ]:
        assert idx[key].value is None, key
        assert idx[key].metadata["unavailable_reason"], key
    assert all(m.metadata["requested_context_length"] == n for n, m in rows)


def test_estimated_tokens_do_not_count_as_actual():
    rows = _index(ctx.compute([_obs(1000, token_source="estimated", input_tokens=900)]))
    assert rows[(1000, "context.input_tokens", "mean")].metadata["token_count_source"] == {
        "estimated": 1
    }
    ratio = rows[(1000, "context.actual_to_estimated", "mean")]
    assert ratio.value is None
    assert rows[(1000, "context.truncation_suspected", "ratio")].value is None


# ------------------------------------------------------------------------------ errors


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    [
        (
            "This model's maximum context length is 4096 tokens. However, you requested 5000",
            400,
            "context_overflow",
        ),
        ("the request exceeds the available context size (n_ctx)", 400, "context_overflow"),
        ("prompt is too long: 210000 tokens > 200000 maximum", 400, "context_overflow"),
        ("CUDA out of memory. Tried to allocate 2.00 GiB", 500, "out_of_memory"),
        ("Rate limit exceeded", 429, None),
        ("Internal server error", 500, None),
        ("maximum context length is 2048", 503, None),
    ],
)
def test_error_classification(message, status, expected):
    assert classify_error_message(message, status) == expected


# ------------------------------------------------------------------------------ metadata


def test_metadata_normalization_keeps_unknown_unknown():
    out = normalize_metadata(
        {"quantization_method": " AWQ ", "weight_bits": "4", "revision": "", "custom": "x"},
        MODEL_FIELDS,
    )
    assert out == {"quantization_method": "AWQ", "weight_bits": 4, "custom": "x"}
    with pytest.raises(ValueError, match="weight_bits"):
        normalize_metadata({"weight_bits": "four"}, MODEL_FIELDS)
    with pytest.raises(ValueError, match="gpu_count"):
        normalize_metadata({"gpu_count": 0}, DEPLOYMENT_FIELDS)
    rows = {r["key"]: r for r in variant_fields({"weight_bits": 4}, None)}
    assert rows["weight_bits"]["value"] == 4
    assert rows["kv_cache_dtype"]["value"] is None  # unknown, not inferred
    assert rows["gpu_type"]["group"] == "deployment"


def test_endpoint_schema_validates_metadata_and_telemetry():
    base = {"name": "e", "base_url": "http://h/v1", "model_identifier": "m"}
    ep = EndpointCreate.model_validate(
        base
        | {
            "model_metadata": {"weight_precision": "int4", "weight_bits": 4},
            "telemetry": {"url": "http://gpu-host:9400/metrics"},
        }
    )
    assert ep.telemetry is not None and ep.telemetry.interval_seconds == 2.0
    with pytest.raises(ValidationError):
        EndpointCreate.model_validate(base | {"model_metadata": {"weight_bits": -1}})
    with pytest.raises(ValidationError):
        TelemetryConfiguration.model_validate({"url": "http://h/metrics", "interval_seconds": 0.1})


# ------------------------------------------------------------------------------ telemetry

DCGM = """
# HELP DCGM_FI_DEV_FB_USED Framebuffer memory used (in MiB).
# TYPE DCGM_FI_DEV_FB_USED gauge
DCGM_FI_DEV_FB_USED{gpu="0",UUID="GPU-a",modelName="NVIDIA H100"} 30000
DCGM_FI_DEV_FB_USED{gpu="1",UUID="GPU-b",modelName="NVIDIA H100"} 20000
DCGM_FI_DEV_FB_FREE{gpu="0"} 50000
DCGM_FI_DEV_FB_FREE{gpu="1"} 60000
DCGM_FI_DEV_FB_RESERVED{gpu="0"} 1000
DCGM_FI_DEV_FB_RESERVED{gpu="1"} 1000
DCGM_FI_DEV_GPU_UTIL{gpu="0"} 90
DCGM_FI_DEV_GPU_UTIL{gpu="1"} 50
DCGM_FI_DEV_POWER_USAGE{gpu="0"} 300.5
DCGM_FI_DEV_POWER_USAGE{gpu="1"} 200
DCGM_FI_DEV_GPU_TEMP{gpu="0"} 70
DCGM_FI_DEV_GPU_TEMP{gpu="1"} 65
vllm:gpu_cache_usage_perc{model_name="a"} 0.25
vllm:gpu_cache_usage_perc{model_name="b"} 0.5
vllm:num_requests_running{model_name="a"} 3
bogus_line without value
weird_metric NaN
"""


def test_prometheus_dcgm_and_vllm():
    values = prometheus.extract(prometheus.parse(DCGM))
    assert values["gpu_memory_used_mb"] == 50000
    assert values["gpu_memory_total_mb"] == 162000
    assert values["gpu_utilization_pct"] == 70
    assert values["gpu_power_w"] == 500.5
    assert values["gpu_temperature_c"] == 70
    assert values["kv_cache_usage_pct"] == 50
    assert values["requests_running"] == 3
    assert [g["gpu"] for g in values["gpus"]] == ["0", "1"]
    only_one = prometheus.extract(prometheus.parse(DCGM), gpu_indices=[1])
    assert only_one["gpu_memory_used_mb"] == 20000
    assert len(only_one["gpus"]) == 1


def test_prometheus_nvidia_smi_exporter():
    text = (
        'nvidia_smi_memory_used_bytes{uuid="GPU-x"} 1073741824\n'
        'nvidia_smi_memory_total_bytes{uuid="GPU-x"} 25769803776\n'
        'nvidia_smi_utilization_gpu_ratio{uuid="GPU-x"} 0.42\n'
        'nvidia_smi_power_draw_watts{uuid="GPU-x"} 120\n'
    )
    values = prometheus.extract(prometheus.parse(text))
    assert values["gpu_memory_used_mb"] == 1024
    assert values["gpu_memory_total_mb"] == 24576
    assert values["gpu_utilization_pct"] == 42
    assert "gpu_temperature_c" not in values  # not reported, not guessed
    assert prometheus.extract(prometheus.parse("process_cpu_seconds_total 3\n")) == {}


def test_telemetry_metrics():
    assert collector.compute([], None) == []  # not configured: nothing, not an error
    failed = {m.name + "." + m.aggregation: m for m in collector.compute([], {"configured": True})}
    assert failed["telemetry.samples.count"].value == 0
    assert failed["gpu.memory_used_mb.max"].value is None
    samples = [
        {"gpu_memory_used_mb": 100, "gpu_utilization_pct": 10},
        {"gpu_memory_used_mb": 300, "gpu_utilization_pct": 30},
    ]
    got = {
        m.name + "." + m.aggregation: m for m in collector.compute(samples, {"configured": True})
    }
    assert got["gpu.memory_used_mb.max"].value == 300
    assert got["gpu.memory_used_mb.mean"].value == 200
    assert got["gpu.utilization_pct.mean"].value == 20
    power = got["gpu.power_w.mean"]
    assert power.value is None
    assert "did not report" in power.metadata["unavailable_reason"]
