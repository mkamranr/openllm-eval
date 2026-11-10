"""Metric families and which direction is better, for reports and comparisons."""

from __future__ import annotations

from typing import Literal

Direction = Literal["higher", "lower"] | None

_LOWER = {
    "latency_ms",
    "ttft_ms",
    "generation_ms",
    "inter_token_latency_ms",
    "error_rate",
    "timeout_rate",
    "retries",
    "rag.contradicted_claim_rate",
    "hallucination.unsupported_claim_rate",
    "hallucination.contradiction_rate",
    "hallucination.unverifiable_claim_rate",
    "hallucination.answers_with_contradictions",
    "hallucination.answer_despite_insufficient_evidence",
    "tool.missing_call_rate",
    "tool.unnecessary_call_rate",
    "tool.malformed_arguments_rate",
    "tool.hallucinated_tool_rate",
}
_HIGHER = {
    "request_throughput",
    "output_tokens_per_second",
    "output_token_throughput",
    "total_token_throughput",
}
# Counts whose direction is clear.
_LOWER_COUNTS = {("requests", "failed"), ("requests", "timed_out"), ("requests", "not_completed")}
_HIGHER_COUNTS = {("requests", "succeeded")}

FAMILIES = ("performance", "quality", "tool", "rag", "hallucination")


def family(metric_name: str) -> str:
    prefix = metric_name.split(".", 1)[0]
    return prefix if prefix in FAMILIES[1:] and "." in metric_name else "performance"


def direction(metric_name: str, aggregation: str) -> Direction:
    if (metric_name, aggregation) in _LOWER_COUNTS:
        return "lower"
    if (metric_name, aggregation) in _HIGHER_COUNTS:
        return "higher"
    if metric_name in _LOWER:
        return "lower"
    if metric_name in _HIGHER:
        return "higher" if aggregation != "max" else None
    if metric_name.startswith(("quality.", "rag.", "hallucination.", "tool.")):
        if aggregation in ("pass_rate", "mean_score", "ratio", "value"):
            return "higher"
    return None


def evaluator_of(metric_name: str) -> str | None:
    """``quality.<evaluator>`` metrics belong to one evaluator."""
    if metric_name.startswith("quality.") and metric_name != "quality.output_consistency":
        return metric_name.split(".", 1)[1]
    return None
