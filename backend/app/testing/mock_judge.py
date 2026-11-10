"""Mock LLM judge used by the mock model server.

When a chat request carries the OpenLLM-Eval judge marker (``[openllm-eval-judge:<task>]`` in
its system message), the mock server answers like a judge would, in the judge's JSON format. The
verdicts come from the lexical evidence checks, so they are deterministic; this exercises the
judge code path end to end without a real judge model. It is not a meaningful judge.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.evaluators.evidence import (
    Claim,
    LexicalClaimVerifier,
    Passage,
    content_words,
    evidence_units,
    sentence_claims,
)

_MARKER = re.compile(r"\[openllm-eval-judge:([a-z_]+)\]")
_DATA = re.compile(r'<data name="([^"]*)">\n(.*?)\n</data>', re.S)


def judge_task(messages: list[dict[str, Any]]) -> str | None:
    for m in messages:
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            match = _MARKER.search(m["content"])
            if match:
                return match.group(1)
    return None


def _blocks(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "")
    return [(name, text.replace("<\\/data", "</data")) for name, text in _DATA.findall(user or "")]


def judge_reply(task: str, messages: list[dict[str, Any]]) -> str:
    blocks = _blocks(messages)
    named = dict(blocks)
    passages = [
        Passage(label=name, text=text, position=i)
        for i, (name, text) in enumerate(blocks, start=1)
        if name not in ("question", "answer", "claims")
    ]
    if task == "extract_claims":
        claims = sentence_claims(named.get("answer", ""))
        body: dict[str, Any] = {
            "claims": [{"text": c.text, "citations": c.citations} for c in claims]
        }
    elif task == "verify_claims":
        lines = [re.sub(r"^\d+\.\s*", "", line) for line in named.get("claims", "").splitlines()]
        claims = [Claim(text=line) for line in lines if line.strip()]
        verdicts = _verify(claims, passages)
        body = {
            "verdicts": [
                {
                    "claim": i,
                    "label": {"unsupported": "not_supported", "unverifiable": "not_supported"}.get(
                        v.classification, v.classification
                    ),
                    "passages": [v.passage.label] if v.passage else [],
                    "evidence": v.evidence if v.classification != "unsupported" else "",
                    "rationale": f"Mock judge: {v.rationale}",
                    "confidence": 0.5,
                }
                for i, v in enumerate(verdicts, start=1)
            ]
        }
    elif task == "answer_relevance":
        q, a = content_words(named.get("question", "")), content_words(named.get("answer", ""))
        overlap = len(q & a) / len(q) if q else 0.0
        body = {"score": 1 + round(4 * min(1.0, overlap * 2)), "rationale": "Mock judge."}
    elif task == "context_relevance":
        q = content_words(named.get("question", ""))
        body = {
            "passages": [
                {
                    "id": p.label,
                    "relevant": bool(q) and len(q & content_words(p.text)) / len(q) >= 0.4,
                    "rationale": "Mock judge.",
                }
                for p in passages
            ]
        }
    else:
        body = {"error": f"unknown task {task}"}
    return json.dumps(body)


def _verify(claims: list[Claim], passages: list[Passage]) -> list[Any]:
    verifier = LexicalClaimVerifier()
    units = evidence_units(passages)
    return [verifier.verify_one(c, units) for c in claims]
