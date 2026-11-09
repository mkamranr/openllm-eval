"""Turn a normalized dataset case into a provider-neutral chat request.

The builder is versioned and the version is recorded in every run's snapshot: changing how
prompts are built changes what is measured. Dataset content is untrusted data. It is placed in
messages verbatim and never interpreted as a template.
"""

from __future__ import annotations

import json
from typing import Any

from app.adapters.types import GenerationRequest

PROMPT_BUILDER_VERSION = "1"


def _context_block(contexts: list[dict[str, Any]]) -> str:
    parts = []
    for i, ctx in enumerate(contexts, start=1):
        label = ctx.get("document_id") or f"doc-{i}"
        if ctx.get("chunk_id"):
            label = f"{label}#{ctx['chunk_id']}"
        parts.append(f"[{label}]\n{ctx.get('text', '')}")
    return "Context documents:\n\n" + "\n\n".join(parts)


def build_messages(case: dict[str, Any]) -> list[dict[str, Any]]:
    case_type = case.get("type")
    system_parts: list[str] = []
    if case.get("system_prompt"):
        system_parts.append(case["system_prompt"])
    if case_type == "structured_output" and case.get("json_schema") is not None:
        system_parts.append(
            "Respond only with JSON that conforms to this JSON Schema:\n"
            + json.dumps(case["json_schema"], ensure_ascii=False, sort_keys=True)
        )
    if case_type == "rag" and case.get("messages"):
        system_parts.append(_context_block(case.get("retrieved_contexts") or []))

    if case.get("messages"):
        messages = [dict(m) for m in case["messages"]]
    else:
        text = case.get("input") or ""
        if case_type == "rag":
            text = f"{_context_block(case.get('retrieved_contexts') or [])}\n\nQuestion: {text}"
        elif case_type == "long_context" and case.get("context"):
            text = f"{case['context']}\n\n{text}"
        messages = [{"role": "user", "content": text}]

    if system_parts:
        if messages and messages[0].get("role") == "system":
            merged = "\n\n".join([messages[0].get("content") or "", *system_parts]).strip()
            messages[0] = {**messages[0], "content": merged}
        else:
            messages.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    return messages


def build_request(case: dict[str, Any], generation: dict[str, Any]) -> GenerationRequest:
    """``generation`` holds run-level overrides (temperature, top_p, max_tokens, seed)."""
    extra: dict[str, Any] = {}
    if generation.get("seed") is not None:
        extra["seed"] = generation["seed"]
    return GenerationRequest(
        messages=build_messages(case),
        tools=case.get("tools") if case.get("type") == "tool_call" else None,
        temperature=generation.get("temperature"),
        top_p=generation.get("top_p"),
        max_tokens=generation.get("max_tokens"),
        extra_body=extra,
    )
