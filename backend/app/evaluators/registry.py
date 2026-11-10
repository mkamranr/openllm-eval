"""Evaluator registry: which evaluators exist, which suite runs them, and a per-case runner."""

from __future__ import annotations

from typing import Any

from app.evaluators.answer import answer_evaluators
from app.evaluators.base import EvaluationContext, EvaluationOutcome, Evaluator, ModelOutput
from app.evaluators.rag import hallucination_evaluators, rag_evaluators
from app.evaluators.structured import structured_evaluators
from app.evaluators.tools import tool_evaluators

# Bumped when the registry's composition or shared normalization changes.
EVALUATION_FRAMEWORK_VERSION = "2"

QUALITY_SUITES = ("answer_quality", "structured_output", "tool_calling", "rag", "hallucination")
# Suites whose evaluators use the LLM judge when a run configures one. With a judge they run after
# each model's measurement, so judge latency never holds back the measured request lanes.
JUDGE_SUITES = ("rag", "hallucination")

_REGISTRY: dict[str, Evaluator] = {
    e.name: e
    for e in [
        *answer_evaluators(),
        *structured_evaluators(),
        *tool_evaluators(),
        *rag_evaluators(),
        *hallucination_evaluators(),
    ]
}


def all_evaluators() -> list[Evaluator]:
    return list(_REGISTRY.values())


def get_evaluator(name: str) -> Evaluator | None:
    return _REGISTRY.get(name)


def evaluators_for(suites: list[str], config: dict[str, Any]) -> list[Evaluator]:
    """Enabled evaluators for the selected suites, in registry order (composites last)."""
    return [e for e in _REGISTRY.values() if e.suite in suites and e.enabled(config)]


async def evaluate_case(
    case: dict[str, Any],
    output: ModelOutput,
    evaluators: list[Evaluator],
    context: EvaluationContext,
) -> list[EvaluationOutcome]:
    """Run every evaluator that looks at this case type. An evaluator that raises produces a
    ``failed`` outcome; it never stops the others or the run."""
    outcomes: list[EvaluationOutcome] = []
    context.previous = {}
    context.cache = {}
    case_type = case.get("type")
    for evaluator in evaluators:
        if case_type not in evaluator.case_types:
            continue
        try:
            outcome = await evaluator.evaluate(case, output, context)
        except Exception as exc:
            outcome = evaluator.outcome(
                "failed", reason=f"Evaluator error: {type(exc).__name__}: {str(exc)[:200]}"
            )
        context.previous[evaluator.name] = outcome
        outcomes.append(outcome)
    return outcomes
