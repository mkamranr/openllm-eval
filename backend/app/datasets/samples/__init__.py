"""Bundled sample datasets (spec DATA-07, §15.6): original synthetic content, CC0-1.0."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SAMPLES_DIR = Path(__file__).parent
LICENSE = "CC0-1.0"
SOURCE = "Original synthetic examples written for OpenLLM-Eval"


@dataclass(frozen=True)
class Sample:
    sample_id: str
    name: str
    filename: str
    dataset_type: str
    description: str
    tags: tuple[str, ...]

    @property
    def path(self) -> Path:
        return SAMPLES_DIR / self.filename

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


SAMPLES: dict[str, Sample] = {
    s.sample_id: s
    for s in (
        Sample(
            "general-qa",
            "Sample: general Q&A",
            "general_qa.jsonl",
            "qa",
            "Short technical questions with reference answers, including one multi-turn case.",
            ("sample", "qa"),
        ),
        Sample(
            "tool-calling",
            "Sample: tool calling",
            "tool_calling.jsonl",
            "tool_call",
            "Tool selection, arguments, parallel calls, missing arguments, no-tool and refusal "
            "cases, with fixture results for simulated execution.",
            ("sample", "tool-use"),
        ),
        Sample(
            "rag-grounding",
            "Sample: RAG grounding",
            "rag_grounding.jsonl",
            "rag",
            "Answers from supplied contexts with expected citations, distractors, conflicting "
            "sources, and an unanswerable case.",
            ("sample", "rag"),
        ),
        Sample(
            "structured-output",
            "Sample: structured output",
            "structured_output.jsonl",
            "structured_output",
            "JSON extraction and classification against JSON Schemas.",
            ("sample", "json"),
        ),
        Sample(
            "long-context-needle",
            "Sample: long-context needle",
            "long_context_needle.jsonl",
            "long_context",
            "Find one fact placed at a known position in 12-23k characters of synthetic notes.",
            ("sample", "long-context"),
        ),
    )
}
