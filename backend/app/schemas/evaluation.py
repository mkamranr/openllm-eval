"""Evaluation configuration, profiles, and evaluator results (spec §5.6, §5.7, §9.4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmbeddingConfig(BaseModel):
    """Optional embedding-similarity evaluator. The endpoint must serve ``/embeddings``."""

    model_config = ConfigDict(extra="forbid")

    endpoint_id: uuid.UUID
    threshold: float = Field(default=0.8, ge=-1, le=1)


class JudgeConfig(BaseModel):
    """Optional LLM judge for the RAG and hallucination suites (opt-in; off by default). Case
    content (questions, contexts, and answers) is sent to this endpoint."""

    model_config = ConfigDict(extra="forbid")

    endpoint_id: uuid.UUID
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=64, le=8192)
    # Judge requests in flight at once, after each model's measurement.
    concurrency: int = Field(default=4, ge=1, le=32)


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Spec §5.7: mode A = generation_only, mode B = simulated, mode C = integration (tools run in
    # the sandboxed tool runner; needs TOOL_EXECUTION_ENABLED, checked at run creation).
    tool_mode: str = "simulated"
    max_tool_rounds: int = Field(default=3, ge=1, le=5)
    token_f1_threshold: float = Field(default=0.5, ge=0, le=1)
    allow_code_fence: bool = True
    disabled_evaluators: list[str] = Field(default_factory=list, max_length=50)
    embedding: EmbeddingConfig | None = None
    # RAG / hallucination (spec §5.8, §5.9).
    faithfulness_threshold: float = Field(default=0.8, ge=0, le=1)
    # Lexical claim check: share of a claim's content words that must appear in the evidence.
    support_threshold: float = Field(default=0.65, ge=0.3, le=1)
    judge: JudgeConfig | None = None

    @field_validator("tool_mode")
    @classmethod
    def _tool_mode(cls, v: str) -> str:
        if v not in ("simulated", "generation_only", "integration"):
            raise ValueError("tool_mode must be 'simulated', 'generation_only' or 'integration'.")
        return v

    @field_validator("disabled_evaluators")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        from app.evaluators.registry import get_evaluator

        unknown = [n for n in v if get_evaluator(n) is None]
        if unknown:
            raise ValueError(f"Unknown evaluator(s): {', '.join(unknown)}.")
        return list(dict.fromkeys(v))


class EvaluatorInfo(BaseModel):
    name: str
    version: str
    suite: str
    kind: str
    title: str
    description: str
    case_types: list[str]
    optional: bool


class EvaluationSummary(BaseModel):
    """Compact evaluator verdict, embedded in result listings."""

    evaluator_name: str
    suite: str
    status: str
    score: float | None
    passed: bool | None
    label: str | None


class EvaluationResultRead(EvaluationSummary):
    id: uuid.UUID
    evaluator_version: str
    rationale: str | None
    evidence: list[dict[str, Any]]
    metadata: dict[str, Any]
    created_at: datetime
