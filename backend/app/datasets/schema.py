"""Declared schema for dataset cases (spec §5.3.1 to §5.3.4), schema version 1.

Every record has the common fields (``id``, ``type``, ``input`` or ``messages``, ``tags``,
``metadata``, ...) plus fields specific to its case type. Unknown fields are kept but reported as
warnings, so typos such as ``refrence_answer`` don't pass silently. Validation is strict about
things an evaluator relies on later: tool names referenced by expected calls must exist,
expected arguments must satisfy the tool's JSON Schema, citations must point at supplied
documents, and so on.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "1"

CASE_TYPES: dict[str, str] = {
    "chat": "General chat",
    "instruction": "Instruction following",
    "qa": "Reference-answer quality",
    "structured_output": "Structured output",
    "tool_call": "Tool calling",
    "rag": "RAG evaluation",
    "long_context": "Long-context retrieval",
    "safety": "Safety / refusal behavior",
}
CaseType = Literal[
    "chat", "instruction", "qa", "structured_output", "tool_call", "rag", "long_context", "safety"
]
# A dataset either declares one case type (records may omit "type") or is "mixed".
DATASET_TYPES = (*CASE_TYPES, "mixed")

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
CaseId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]


def _check_json_schema(schema: dict[str, Any], what: str) -> dict[str, Any]:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"{what} is not a valid JSON Schema: {exc.message}") from None
    return schema


def _schema_violation(schema: dict[str, Any], instance: Any) -> str | None:
    error = next(iter(Draft202012Validator(schema).iter_errors(instance)), None)
    if error is None:
        return None
    where = "/".join(str(p) for p in error.absolute_path)
    return f"{error.message}" + (f" (at {where})" if where else "")


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class CaseBase(BaseModel):
    """Common fields for every case type."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=False)

    id: CaseId
    type: CaseType
    input: str | None = None
    messages: list[Message] | None = None
    system_prompt: str | None = None
    reference_answer: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Optional train/dev/test-style partition label (spec §5.3).
    split: Literal["train", "dev", "test"] | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if v != v.strip():
            raise ValueError("must not start or end with whitespace")
        if _CONTROL.search(v):
            raise ValueError("must not contain control characters")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for tag in v:
            tag = tag.strip()
            if not tag or len(tag) > 64:
                raise ValueError("each tag must be 1-64 characters")
            if _CONTROL.search(tag):
                raise ValueError("tags must not contain control characters")
            if tag not in cleaned:
                cleaned.append(tag)
        if len(cleaned) > 50:
            raise ValueError("at most 50 tags")
        return cleaned

    @model_validator(mode="after")
    def _prompt(self) -> CaseBase:
        if (self.input is None) == (self.messages is None):
            raise ValueError("provide exactly one of 'input' or 'messages'")
        if self.input is not None and not self.input.strip():
            raise ValueError("'input' must not be empty")
        if self.messages is not None and not any(m.role == "user" for m in self.messages):
            raise ValueError("'messages' must contain at least one user message")
        return self


class ChatCase(CaseBase):
    type: Literal["chat"]


class InstructionCase(CaseBase):
    type: Literal["instruction"]
    # Human-readable constraints the answer must satisfy (checked by evaluators in M4).
    constraints: list[NonEmpty] = Field(default_factory=list)


class QACase(CaseBase):
    type: Literal["qa"]
    reference_answer: NonEmpty  # type: ignore[assignment]


class StructuredOutputCase(CaseBase):
    type: Literal["structured_output"]
    json_schema: dict[str, Any]
    expected_output: Any = None

    @field_validator("json_schema")
    @classmethod
    def _schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _check_json_schema(v, "json_schema")

    @model_validator(mode="after")
    def _expected(self) -> StructuredOutputCase:
        if self.expected_output is not None:
            problem = _schema_violation(self.json_schema, self.expected_output)
            if problem:
                raise ValueError(f"expected_output does not match json_schema: {problem}")
        return self


class ToolFunction(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})

    @field_validator("parameters")
    @classmethod
    def _params(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _check_json_schema(v, "parameters")


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["function"] = "function"
    function: ToolFunction


class ExpectedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonEmpty
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultFixture(BaseModel):
    """Deterministic tool output for simulated execution (spec §5.7, mode B)."""

    model_config = ConfigDict(extra="forbid")

    name: NonEmpty
    arguments: dict[str, Any] | None = None  # None = matches any call of this tool
    result: Any


class ToolCallCase(CaseBase):
    type: Literal["tool_call"]
    tools: list[ToolDefinition] = Field(min_length=1)
    expected_behavior: Literal["call_tool", "no_tool", "ask_clarification", "refuse"] = "call_tool"
    expected_tool_calls: list[ExpectedToolCall] = Field(default_factory=list)
    ordered_calls: bool = False
    tool_results: list[ToolResultFixture] = Field(default_factory=list)
    expected_final_answer: str | None = None

    @model_validator(mode="after")
    def _consistency(self) -> ToolCallCase:
        tools = {t.function.name: t.function for t in self.tools}
        if len(tools) != len(self.tools):
            raise ValueError("tool names must be unique")
        if self.expected_behavior == "call_tool" and not self.expected_tool_calls:
            raise ValueError("expected_behavior 'call_tool' needs at least one expected_tool_calls")
        if self.expected_behavior != "call_tool" and self.expected_tool_calls:
            raise ValueError(
                f"expected_tool_calls must be empty when expected_behavior is "
                f"'{self.expected_behavior}'"
            )
        for i, call in enumerate(self.expected_tool_calls):
            if call.name not in tools:
                raise ValueError(
                    f"expected_tool_calls[{i}] uses unknown tool '{call.name}' "
                    f"(available: {', '.join(tools)})"
                )
            problem = _schema_violation(tools[call.name].parameters, call.arguments)
            if problem:
                raise ValueError(
                    f"expected_tool_calls[{i}] arguments do not match the "
                    f"'{call.name}' parameters: {problem}"
                )
        for i, fixture in enumerate(self.tool_results):
            if fixture.name not in tools:
                raise ValueError(f"tool_results[{i}] uses unknown tool '{fixture.name}'")
        return self


class RetrievedContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: NonEmpty
    document_id: str | None = None
    chunk_id: str | None = None
    source: str | None = None
    page: int | None = Field(default=None, ge=0)
    # Optional relevance label for context precision (spec §5.8).
    relevant: bool | None = None


class ExpectedCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: NonEmpty
    chunk_id: str | None = None
    page: int | None = Field(default=None, ge=0)


class RAGCase(CaseBase):
    type: Literal["rag"]
    retrieved_contexts: list[RetrievedContext] = Field(min_length=1)
    expected_citations: list[ExpectedCitation] = Field(default_factory=list)
    # False = the contexts do not contain the answer; the model should abstain (spec §5.9).
    answerable: bool = True

    @model_validator(mode="after")
    def _citations(self) -> RAGCase:
        docs = {c.document_id for c in self.retrieved_contexts if c.document_id}
        chunks = {c.chunk_id for c in self.retrieved_contexts if c.chunk_id}
        for i, cite in enumerate(self.expected_citations):
            if cite.document_id not in docs:
                raise ValueError(
                    f"expected_citations[{i}] cites document '{cite.document_id}', which is not "
                    "in retrieved_contexts"
                )
            if cite.chunk_id and cite.chunk_id not in chunks:
                raise ValueError(
                    f"expected_citations[{i}] cites chunk '{cite.chunk_id}', which is not in "
                    "retrieved_contexts"
                )
        if not self.answerable and self.expected_citations:
            raise ValueError("an unanswerable case cannot have expected_citations")
        return self


class AnswerLocation(BaseModel):
    model_config = ConfigDict(extra="allow")

    approximate_position: float | None = Field(default=None, ge=0, le=1)


class LongContextCase(CaseBase):
    type: Literal["long_context"]
    context: NonEmpty
    expected_answer: NonEmpty | None = None
    # Multi-fact retrieval: every value must appear in the answer.
    expected_answers: list[NonEmpty] | None = Field(default=None, min_length=1, max_length=50)
    answer_location: AnswerLocation | None = None
    # Requested context size in tokens (context-window experiments group results by it). The
    # actual input tokens are whatever the server reports at run time.
    context_length: int | None = Field(default=None, ge=1, le=100_000_000)
    # How the case's size was estimated: {"tokens", "method", "chars_per_token", "version"}.
    context_estimate: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _expected(self) -> LongContextCase:
        if self.expected_answer is None and not self.expected_answers:
            raise ValueError("provide 'expected_answer' or 'expected_answers'")
        return self


class SafetyCase(CaseBase):
    type: Literal["safety"]
    expected_behavior: Literal["refuse", "comply"]


CASE_MODELS: dict[str, type[CaseBase]] = {
    "chat": ChatCase,
    "instruction": InstructionCase,
    "qa": QACase,
    "structured_output": StructuredOutputCase,
    "tool_call": ToolCallCase,
    "rag": RAGCase,
    "long_context": LongContextCase,
    "safety": SafetyCase,
}


def known_fields(case_type: str) -> set[str]:
    return set(CASE_MODELS[case_type].model_fields)
