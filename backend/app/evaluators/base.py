"""Evaluator interface (spec §7.7).

Every evaluator is a small, versioned, side-effect-free class. Given a dataset case and the
model's output it returns an :class:`EvaluationOutcome`. A missing score is ``None``, never 0.
Evaluators never execute tool calls, never run model-generated code, and treat dataset content
and model output as untrusted text.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

Status = Literal["completed", "failed", "skipped", "not_applicable", "requires_review"]
STATUSES: tuple[str, ...] = Status.__args__  # type: ignore[attr-defined]
Kind = Literal["objective", "similarity", "rule_based", "composite", "evidence", "model_judged"]


@dataclass
class EvaluationOutcome:
    evaluator: str
    evaluator_version: str
    suite: str
    status: Status
    score: float | None = None
    passed: bool | None = None
    label: str | None = None
    reason: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    score_scale: tuple[float, float] | None = (0.0, 1.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluator": self.evaluator,
            "evaluator_version": self.evaluator_version,
            "suite": self.suite,
            "status": self.status,
            "score": self.score,
            "score_scale": {"min": self.score_scale[0], "max": self.score_scale[1]}
            if self.score_scale
            else None,
            "passed": self.passed,
            "label": self.label,
            "reason": self.reason,
            "evidence": self.evidence,
            "metadata": self.metadata,
        }


@dataclass
class ToolCallOutput:
    """A tool call as returned by the model (never executed)."""

    name: str | None
    arguments: dict[str, Any] | None
    arguments_raw: str = ""
    arguments_error: str | None = None
    id: str | None = None


@dataclass
class ModelOutput:
    content: str | None
    tool_calls: list[ToolCallOutput] = field(default_factory=list)
    finish_reason: str | None = None
    # Simulated tool execution transcript (spec §5.7 mode B), if it ran.
    simulation: dict[str, Any] | None = None


Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


@dataclass
class EvaluationContext:
    config: dict[str, Any] = field(default_factory=dict)
    embedder: Embedder | None = None
    # Optional LLM judge (app.evaluators.judge.Judge); None unless the run opts in.
    judge: Any = None
    # Outcomes of evaluators already run for this case (composites read them).
    previous: dict[str, EvaluationOutcome] = field(default_factory=dict)
    # Per-case scratch space shared by evaluators (e.g. one claim analysis for several).
    cache: dict[str, Any] = field(default_factory=dict)


class Evaluator:
    name: ClassVar[str]
    version: ClassVar[str] = "1.0.0"
    suite: ClassVar[str]
    kind: ClassVar[Kind] = "objective"
    title: ClassVar[str]
    description: ClassVar[str]
    # Case types this evaluator looks at; other case types are skipped without a result row.
    case_types: ClassVar[frozenset[str]]
    # Optional evaluators run only when configured (e.g. embedding similarity).
    optional: ClassVar[bool] = False

    def enabled(self, config: dict[str, Any]) -> bool:
        return self.name not in (config.get("disabled_evaluators") or [])

    async def evaluate(
        self, case: dict[str, Any], output: ModelOutput, context: EvaluationContext
    ) -> EvaluationOutcome:
        raise NotImplementedError

    # Helpers that stamp name, version, and suite on every outcome.
    def outcome(self, status: Status, **kwargs: Any) -> EvaluationOutcome:
        return EvaluationOutcome(
            evaluator=self.name,
            evaluator_version=self.version,
            suite=self.suite,
            status=status,
            **kwargs,
        )

    def not_applicable(self, reason: str, **kwargs: Any) -> EvaluationOutcome:
        return self.outcome("not_applicable", reason=reason, **kwargs)

    def result(self, passed: bool, score: float | None = None, **kwargs: Any) -> EvaluationOutcome:
        return self.outcome(
            "completed",
            passed=passed,
            score=(1.0 if passed else 0.0) if score is None else score,
            label=kwargs.pop("label", "pass" if passed else "fail"),
            **kwargs,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "suite": self.suite,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "case_types": sorted(self.case_types),
            "optional": self.optional,
        }
