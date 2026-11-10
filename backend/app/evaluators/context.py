"""Context-window evaluators (spec §5.11): did the model find the facts placed in its context?"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.evaluators.base import EvaluationContext, EvaluationOutcome, Evaluator, ModelOutput

CONTEXT_CASE_TYPES = frozenset({"long_context"})


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", text).strip(" .,;:!?\"'")


def position_bucket(position: float | None) -> str | None:
    """early: first third, middle, late: last third of the context."""
    if position is None:
        return None
    return "early" if position < 1 / 3 else "middle" if position <= 2 / 3 else "late"


class NeedleRetrieval(Evaluator):
    name = "needle_retrieval"
    version = "1.0.0"
    suite = "context_window"
    kind = "objective"
    title = "Needle retrieval"
    description = (
        "The expected value(s) placed in the context appear in the answer (case- and "
        "whitespace-insensitive). Score = share of expected values found; passes when all are."
    )
    case_types = CONTEXT_CASE_TYPES

    async def evaluate(
        self, case: dict[str, Any], output: ModelOutput, context: EvaluationContext
    ) -> EvaluationOutcome:
        expected = case.get("expected_answers") or (
            [case["expected_answer"]] if case.get("expected_answer") else []
        )
        meta: dict[str, Any] = {
            "context_length": case.get("context_length"),
            "position": (case.get("answer_location") or {}).get("approximate_position"),
        }
        meta["position_bucket"] = position_bucket(meta["position"])
        if not expected:
            return self.not_applicable("The case has no expected answer.", metadata=meta)
        answer = _norm(output.content or "")
        found = [e for e in expected if _norm(e) and _norm(e) in answer]
        missing = [e for e in expected if e not in found]
        score = len(found) / len(expected)
        label = "retrieved" if not missing else "partial" if found else "missed"
        return self.result(
            not missing,
            score=score,
            label=label,
            reason=f"Found {len(found)} of {len(expected)} expected value(s)."
            + (f" Missing: {', '.join(missing[:5])}." if missing else ""),
            metadata=meta | {"expected": len(expected), "found": len(found)},
        )


def context_evaluators() -> list[Evaluator]:
    return [NeedleRetrieval()]
