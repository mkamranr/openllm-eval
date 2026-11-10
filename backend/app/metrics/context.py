"""Context-window metrics (spec §5.11), one group per *requested* context length.

The requested length (from the case), the estimated size (fixed heuristic), and the actual input
tokens (as reported by the server) are kept apart, because tokenizers differ. A request that
succeeded at a length is not proof the model *uses* that much context: retrieval accuracy is
reported next to it, and suspected truncation is flagged when the server reports far fewer input
tokens than were sent.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from app.metrics.performance import MetricValue
from app.metrics.stats import mean, percentile

CONTEXT_METRICS_VERSION = "1"
# Reported input tokens below this share of the estimate suggest the server truncated the input.
TRUNCATION_RATIO = 0.5
SERVER_REPORTED = "server_reported"


@dataclass
class ContextObservation:
    context_length: int
    succeeded: bool
    error_type: str | None
    latency_ms: float | None
    ttft_ms: float | None
    input_tokens: int | None
    token_source: str
    estimated_tokens: int | None
    # needle_retrieval outcome (None when the evaluator did not complete)
    retrieved: bool | None = None
    recall: float | None = None
    position_bucket: str | None = None


def _ratio(num: int, den: int, reason: str) -> tuple[float | None, dict[str, Any]]:
    if den == 0:
        return None, {"unavailable_reason": reason, "numerator": num, "denominator": den}
    return num / den, {"numerator": num, "denominator": den}


def compute(observations: list[ContextObservation]) -> list[tuple[int, MetricValue]]:
    """``(context_length, metric)`` pairs."""
    by_length: dict[int, list[ContextObservation]] = defaultdict(list)
    for o in observations:
        by_length[o.context_length].append(o)
    out: list[tuple[int, MetricValue]] = []
    for length in sorted(by_length):
        obs = by_length[length]
        n = len(obs)
        ok = [o for o in obs if o.succeeded]

        def add(
            name: str,
            agg: str,
            value: float | None,
            unit: str,
            count: int,
            _length: int = length,
            **meta: Any,
        ) -> None:
            meta = {"requested_context_length": _length, **meta}
            out.append((_length, MetricValue(name, agg, value, unit, count, meta)))

        add("context.requests", "count", float(n), "count", n)
        value, meta = _ratio(len(ok), n, "No requests.")
        add("context.success_rate", "ratio", value, "ratio", n, **meta)
        errors = Counter(o.error_type or "unknown" for o in obs if not o.succeeded)
        value, meta = _ratio(errors.get("context_overflow", 0), n, "No requests.")
        add(
            "context.overflow_rate",
            "ratio",
            value,
            "ratio",
            n,
            definition="Requests the server rejected as too long for its context window.",
            errors_by_type=dict(errors),
            **meta,
        )
        value, meta = _ratio(errors.get("out_of_memory", 0), n, "No requests.")
        add(
            "context.oom_rate",
            "ratio",
            value,
            "ratio",
            n,
            definition="Requests that failed with an out-of-memory error.",
            **meta,
        )
        for name, attr in (("context.latency_ms", "latency_ms"), ("context.ttft_ms", "ttft_ms")):
            values = [getattr(o, attr) for o in ok if getattr(o, attr) is not None]
            reason = (
                {}
                if values
                else {"unavailable_reason": "no successful request with this measurement"}
            )
            for q in (50, 95):
                add(name, f"p{q}", percentile(values, q), "ms", len(values), **reason)

        reported = [o for o in ok if o.input_tokens is not None]
        sources = dict(Counter(o.token_source for o in ok))
        add(
            "context.input_tokens",
            "mean",
            mean([float(o.input_tokens) for o in reported]) if reported else None,  # type: ignore[arg-type]
            "tokens",
            len(reported),
            definition=(
                "Actual input tokens as counted by the server "
                "(or estimated, see token_count_source)."
            ),
            token_count_source=sources,
            **(
                {}
                if reported
                else {"unavailable_reason": "the server reported no input token counts"}
            ),
        )
        estimated = [o.estimated_tokens for o in obs if o.estimated_tokens is not None]
        add(
            "context.estimated_input_tokens",
            "mean",
            mean([float(e) for e in estimated]) if estimated else None,
            "tokens",
            len(estimated),
            definition="Size estimate of the prompt (heuristic: 4 characters per token).",
        )
        pairs = [o for o in reported if o.token_source == SERVER_REPORTED and o.estimated_tokens]
        add(
            "context.actual_to_estimated",
            "mean",
            mean([o.input_tokens / o.estimated_tokens for o in pairs]) if pairs else None,  # type: ignore[operator]
            "ratio",
            len(pairs),
            definition=(
                "Server-reported input tokens / estimated tokens. Tokenizers differ, so "
                "values near 1 are not expected exactly."
            ),
            **({} if pairs else {"unavailable_reason": "needs server-reported input token counts"}),
        )
        suspected = sum(1 for o in pairs if o.input_tokens < TRUNCATION_RATIO * o.estimated_tokens)  # type: ignore[operator]
        value, meta = _ratio(suspected, len(pairs), "needs server-reported input token counts")
        add(
            "context.truncation_suspected",
            "ratio",
            value,
            "ratio",
            len(pairs),
            definition=(
                "Successful requests whose reported input tokens are below "
                f"{TRUNCATION_RATIO:.0%} of the estimate (the server may have cut the prompt)."
            ),
            **meta,
        )
        evaluated = [o for o in obs if o.retrieved is not None]
        value, meta = _ratio(
            sum(1 for o in evaluated if o.retrieved),
            len(evaluated),
            "no completed needle_retrieval evaluation",
        )
        add(
            "context.retrieval_accuracy",
            "pass_rate",
            value,
            "ratio",
            len(evaluated),
            definition="Answers containing every expected value (needle_retrieval).",
            **meta,
        )
        for bucket in ("early", "middle", "late"):
            items = [o for o in evaluated if o.position_bucket == bucket]
            value, meta = _ratio(
                sum(1 for o in items if o.retrieved),
                len(items),
                f"no {bucket}-position needle at this length",
            )
            add("context.retrieval_accuracy", bucket, value, "ratio", len(items), **meta)
        recalls = [o.recall for o in evaluated if o.recall is not None]
        add(
            "context.fact_recall",
            "mean",
            mean(recalls) if recalls else None,
            "ratio",
            len(recalls),
            definition="Mean share of expected values found per answer (multi-fact retrieval).",
            **(
                {}
                if recalls
                else {"unavailable_reason": "no completed needle_retrieval evaluation"}
            ),
        )
    return out
