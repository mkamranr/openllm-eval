"""Aggregate quality metrics from stored evaluation results (one model at a time).

Rates are computed over *completed* evaluations only; ``not_applicable``, ``skipped``, and
``failed`` evaluations are counted in metadata but never treated as passes or fails. A rate with
no completed evaluations is ``None`` with a reason. Requests that failed at the model are not
evaluated and are reported separately (``quality.evaluated_requests``).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.evaluators.registry import get_evaluator
from app.metrics.performance import MetricValue

QUALITY_METRICS_VERSION = "1"


@dataclass
class EvalRow:
    evaluator: str
    suite: str
    status: str
    score: float | None
    passed: bool | None
    label: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


def _ratio(num: int, den: int, reason: str) -> tuple[float | None, dict[str, Any]]:
    if den == 0:
        return None, {"unavailable_reason": reason}
    return num / den, {}


def evaluator_metrics(rows: list[EvalRow]) -> list[MetricValue]:
    by_eval: dict[str, list[EvalRow]] = defaultdict(list)
    for r in rows:
        by_eval[r.evaluator].append(r)
    out: list[MetricValue] = []
    for name, items in by_eval.items():
        ev = get_evaluator(name)
        statuses = Counter(r.status for r in items)
        completed = [r for r in items if r.status == "completed"]
        passed = sum(1 for r in completed if r.passed)
        scores = [r.score for r in completed if r.score is not None]
        base = {
            "suite": items[0].suite,
            "statuses": dict(statuses),
            "labels": dict(Counter(r.label for r in completed if r.label)),
            "definition": ev.description if ev else None,
            "kind": ev.kind if ev else None,
        }
        reason = "No case was applicable or every evaluation was skipped."
        rate, extra = _ratio(passed, len(completed), reason)
        out.append(
            MetricValue(f"quality.{name}", "pass_rate", rate, "ratio", len(completed), base | extra)
        )
        mean = sum(scores) / len(scores) if scores else None
        out.append(
            MetricValue(
                f"quality.{name}",
                "mean_score",
                mean,
                "score",
                len(scores),
                base | ({} if scores else {"unavailable_reason": reason}),
            )
        )
    return out


def tool_metrics(rows: list[EvalRow]) -> list[MetricValue]:
    """Spec §5.7 tool-call metrics. Selection and argument correctness stay separate: argument
    metrics are computed only over calls where the expected tool was selected."""
    by_eval: dict[str, list[EvalRow]] = defaultdict(list)
    for r in rows:
        if r.suite == "tool_calling":
            by_eval[r.evaluator].append(r)
    selection = [r for r in by_eval.get("tool_selection", []) if r.status == "completed"]
    if not selection:
        return []
    out: list[MetricValue] = []

    def rate(metric: str, evaluator: str, definition: str) -> None:
        done = [r for r in by_eval.get(evaluator, []) if r.status == "completed"]
        value, extra = _ratio(sum(1 for r in done if r.passed), len(done), "No applicable cases.")
        out.append(
            MetricValue(
                f"tool.{metric}",
                "ratio",
                value,
                "ratio",
                len(done),
                {"definition": definition, "evaluator": evaluator} | extra,
            )
        )

    rate(
        "selection_accuracy",
        "tool_selection",
        "Cases where exactly the expected tools were called (or none, when none were expected).",
    )
    rate(
        "argument_name_accuracy",
        "tool_argument_names",
        "Cases with correct argument names, among cases where an expected tool was called.",
    )
    rate(
        "argument_schema_validity",
        "tool_argument_schema",
        "Cases whose calls all have schema-valid arguments, among cases with calls to "
        "supplied tools.",
    )
    rate(
        "required_field_completeness",
        "tool_required_arguments",
        "Cases whose calls include every required argument.",
    )
    rate(
        "argument_exact_match_accuracy",
        "tool_argument_values",
        "Cases where every expected argument value matches, among cases where an expected "
        "tool was called.",
    )
    rate(
        "sequence_accuracy",
        "tool_sequence",
        "Cases called in the required order, among cases that require one.",
    )
    rate(
        "final_answer_correctness",
        "tool_final_answer",
        "Cases whose final answer contains the expected answer.",
    )
    rate("task_success", "tool_task_success", "Cases where every applicable tool check passed.")

    totals = Counter()
    call_cases = no_tool_cases = missing = unnecessary = 0
    for r in selection:
        m = r.metadata
        for k in (
            "calls",
            "true_positive_calls",
            "false_positive_calls",
            "false_negative_calls",
            "unknown_tool_calls",
            "malformed_calls",
        ):
            totals[k] += int(m.get(k) or 0)
        if m.get("expected_behavior") == "call_tool":
            call_cases += 1
            missing += int(not m.get("calls"))
        else:
            no_tool_cases += 1
            unnecessary += int(bool(m.get("calls")))
    tp, fp, fn, calls = (
        totals["true_positive_calls"],
        totals["false_positive_calls"],
        totals["false_negative_calls"],
        totals["calls"],
    )

    def add(metric: str, num: int, den: int, definition: str, reason: str) -> None:
        value, extra = _ratio(num, den, reason)
        out.append(
            MetricValue(
                f"tool.{metric}",
                "ratio",
                value,
                "ratio",
                den,
                {"definition": definition, "numerator": num, "denominator": den} | extra,
            )
        )

    add(
        "call_precision", tp, tp + fp, "Matched calls / all calls made.", "No tool calls were made."
    )
    add(
        "call_recall", tp, tp + fn, "Matched calls / all expected calls.", "No calls were expected."
    )
    add(
        "hallucinated_tool_rate",
        totals["unknown_tool_calls"],
        calls,
        "Calls to tools that were not supplied / all calls.",
        "No tool calls were made.",
    )
    add(
        "malformed_arguments_rate",
        totals["malformed_calls"],
        calls,
        "Calls whose arguments are not a valid JSON object / all calls.",
        "No tool calls were made.",
    )
    add(
        "missing_call_rate",
        missing,
        call_cases,
        "Cases expecting a tool call where none was made.",
        "No case expected a tool call.",
    )
    add(
        "unnecessary_call_rate",
        unnecessary,
        no_tool_cases,
        "Cases expecting no tool call where one was made.",
        "No case expected the model to avoid tools.",
    )
    return out


def consistency_metric(hashes_by_case: dict[str, list[str]]) -> MetricValue:
    """Share of repeated cases whose responses were byte-identical every time (spec §5.6)."""
    repeated = {k: v for k, v in hashes_by_case.items() if len(v) > 1}
    same = sum(1 for v in repeated.values() if len(set(v)) == 1)
    value, extra = _ratio(same, len(repeated), "No case was sent more than once.")
    return MetricValue(
        "quality.output_consistency",
        "ratio",
        value,
        "ratio",
        len(repeated),
        {
            "definition": "Cases sent more than once whose responses were identical every time.",
            "repeated_cases": len(repeated),
        }
        | extra,
    )


def compute(
    rows: Iterable[EvalRow],
    *,
    evaluated_requests: int,
    successful_requests: int,
    hashes_by_case: dict[str, list[str]],
) -> list[MetricValue]:
    rows = list(rows)
    out = [
        MetricValue(
            "quality.evaluated_requests",
            "count",
            float(evaluated_requests),
            "requests",
            evaluated_requests,
            {
                "definition": "Successful measured requests that were evaluated. Failed "
                "requests are not evaluated.",
                "successful_requests": successful_requests,
            },
        ),
        consistency_metric(hashes_by_case),
    ]
    out += evaluator_metrics(rows)
    out += tool_metrics(rows)
    return out
