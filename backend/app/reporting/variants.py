"""Deployment-variant comparison (spec §5.12): the same model under different quantization,
precision or serving settings.

Quantization is never inferred from model names: every field comes from what the endpoint
declared, and undeclared fields are shown as unknown. Warnings say when a difference in metrics
cannot be attributed to the declared variant (different model family, unknown quantization,
different datasets or settings).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.schemas.model_metadata import variant_fields

# Metrics shown side by side for variants (those present in the comparison, in this order).
KEY_METRICS: list[tuple[str, str, str]] = [
    ("Error rate", "error_rate", "ratio"),
    ("TTFT p50", "ttft_ms", "p50"),
    ("TTFT p95", "ttft_ms", "p95"),
    ("Latency p50", "latency_ms", "p50"),
    ("Output tok/s (mean)", "output_tokens_per_second", "mean"),
    ("Output token throughput", "output_token_throughput", "value"),
    ("Request throughput", "request_throughput", "value"),
    ("GPU memory peak", "gpu.memory_used_mb", "max"),
    ("GPU utilization (mean)", "gpu.utilization_pct", "mean"),
    ("GPU power (mean)", "gpu.power_w", "mean"),
    ("KV cache peak", "gpu.kv_cache_usage_pct", "max"),
]
# Context-window metrics compared per requested length.
CONTEXT_METRICS: list[tuple[str, str, str]] = [
    ("Retrieval accuracy", "context.retrieval_accuracy", "pass_rate"),
    ("Success rate", "context.success_rate", "ratio"),
    ("Overflow rate", "context.overflow_rate", "ratio"),
    ("OOM rate", "context.oom_rate", "ratio"),
    ("TTFT p50", "context.ttft_ms", "p50"),
    ("Input tokens (server)", "context.input_tokens", "mean"),
]
QUANT_KEYS = ("quantization_method", "weight_precision", "weight_bits")


def _quality_keys(metrics: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    out = []
    for m in metrics:
        if m["family"] in ("quality", "tool", "rag", "hallucination") and m["aggregation"] in (
            "pass_rate",
            "ratio",
        ):
            if m["metric_name"] == "quality.output_consistency":
                continue
            out.append((m["metric_name"], m["metric_name"], m["aggregation"]))
    return out


def build(
    subjects: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    context_rows: list[dict[str, Any]],
    baseline_id: str,
) -> dict[str, Any]:
    """``context_rows``: {"subject_id", "context_length", "metric_name", "aggregation",
    "metric_value", "unavailable_reason"}."""
    fields_by_subject = {
        s["id"]: {
            r["key"]: r
            for r in variant_fields(
                s["config"].get("declared_model_metadata"), s["config"].get("hardware_metadata")
            )
        }
        for s in subjects
    }
    template = next(iter(fields_by_subject.values()))
    fields = []
    for key, row in template.items():
        values = {sid: per[key]["value"] for sid, per in fields_by_subject.items()}
        if all(v is None for v in values.values()) and key not in QUANT_KEYS:
            continue
        known = [v for v in values.values() if v is not None]
        fields.append(
            {
                "key": key,
                "label": row["label"],
                "group": row["group"],
                "values": values,
                "differs": len({repr(v) for v in values.values()}) > 1,
                "unknown_for": [sid for sid, v in values.items() if v is None],
                "all_known": len(known) == len(values),
            }
        )

    labels = {s["id"]: s["label"] for s in subjects}
    warnings: list[dict[str, str]] = []

    def warn(level: str, message: str) -> None:
        warnings.append({"level": level, "message": message})

    by_key = {f["key"]: f for f in fields}
    families = by_key.get("family")
    if families and families["differs"] and not families["unknown_for"]:
        warn(
            "warning",
            "The compared endpoints declare different model families; differences are not "
            "attributable to quantization alone.",
        )
    elif families is None or families["unknown_for"]:
        missing = families["unknown_for"] if families else list(labels)
        warn(
            "info",
            "Model family is unknown for "
            + ", ".join(labels[s] for s in missing)
            + "; declare it in the endpoint's model metadata to confirm the variants share "
            "base weights.",
        )
    unknown_quant = [
        sid
        for sid, per in fields_by_subject.items()
        if all(per[k]["value"] is None for k in QUANT_KEYS)
    ]
    if unknown_quant:
        warn(
            "warning",
            "Quantization is unknown for "
            + ", ".join(labels[s] for s in unknown_quant)
            + " (not declared; it is never guessed from the model name).",
        )
    if len(subjects) > 1 and not any(by_key[k]["differs"] for k in QUANT_KEYS if k in by_key):
        warn(
            "info",
            "The compared endpoints declare the same (or no) quantization; this is not a "
            "quantization comparison.",
        )
    base = next(s for s in subjects if s["id"] == baseline_id)
    if any(s["config"]["dataset_hashes"] != base["config"]["dataset_hashes"] for s in subjects):
        warn("warning", "The runs used different dataset versions; quality is not comparable.")
    if not any(m["metric_name"].startswith("gpu.") for m in metrics):
        warn(
            "info",
            "No GPU telemetry was collected; memory savings cannot be shown. Configure a "
            "telemetry exporter on the endpoints to measure them.",
        )

    index = {(m["metric_name"], m["aggregation"]): m for m in metrics}
    key_metrics = []
    for label, name, agg in [*_quality_keys(metrics), *KEY_METRICS]:
        m = index.get((name, agg))
        if m is not None:
            key_metrics.append(m | {"label": label})

    grouped: dict[tuple[int, str, str], dict[str, Any]] = defaultdict(dict)
    for r in context_rows:
        grouped[(r["context_length"], r["metric_name"], r["aggregation"])][r["subject_id"]] = r
    context = []
    lengths = sorted({r["context_length"] for r in context_rows})
    for length in lengths:
        for label, name, agg in CONTEXT_METRICS:
            per = grouped.get((length, name, agg))
            if not per:
                continue
            context.append(
                {
                    "context_length": length,
                    "label": label,
                    "metric_name": name,
                    "aggregation": agg,
                    "values": {
                        s["id"]: {
                            "value": (per.get(s["id"]) or {}).get("metric_value"),
                            "unavailable_reason": (per.get(s["id"]) or {}).get("unavailable_reason")
                            or (None if s["id"] in per else "not measured in this run"),
                        }
                        for s in subjects
                    },
                }
            )
    return {
        "fields": fields,
        "warnings": warnings,
        "key_metrics": key_metrics,
        "context_lengths": lengths,
        "context": context,
    }
