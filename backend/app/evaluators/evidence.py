"""Evidence handling for RAG and hallucination evaluation.

This module defines the claim pipeline and its deterministic (lexical) implementations:

1. :func:`passages_for` turns a case's supplied evidence into labelled passages that keep their
   document, chunk, source, and page identifiers.
2. A :class:`ClaimExtractor` splits an answer into factual claims, keeping the citations attached
   to each claim.
3. A :class:`ClaimVerifier` classifies each claim against the passages as ``supported``,
   ``unsupported``, ``contradicted``, or ``unverifiable`` (ambiguous, for human review), with the
   evidence excerpt and its source identifiers.

The lexical implementations are conservative heuristics: word overlap decides support, and a
number or negation mismatch against otherwise matching evidence counts as a contradiction. They
are English-centric and miss paraphrases. An LLM judge (``app.evaluators.judge``) implements the
same interfaces. Neither can prove that a claim is true; they compare claims with the supplied
evidence only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.evaluators.answer import normalize

Classification = Literal["supported", "unsupported", "contradicted", "unverifiable"]
CLASSIFICATIONS: tuple[str, ...] = Classification.__args__  # type: ignore[attr-defined]

MAX_CLAIMS = 30
MAX_EXCERPT_CHARS = 400
LONG_CONTEXT_WINDOW_SENTENCES = 3
DEFAULT_SUPPORT_THRESHOLD = 0.65
# Below this word coverage a claim is unsupported; between it and the support threshold it is
# ambiguous (unverifiable) and flagged for review.
UNSUPPORTED_BELOW = 0.35
CONTRADICTION_MIN_COVERAGE = 0.6

# Function words plus words that describe the evidence rather than the facts ("according to the
# context"), so that framing does not lower a claim's coverage.
STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from
    further had has have having he her here hers herself him himself his how i if in into is it
    its itself just me more most my myself nor of off on once only or other our ours ourselves
    out over own same she should so some such than that the their theirs them themselves then
    there these they this those through to too under until up very was we were what when where
    which while who whom why will with would you your yours yourself yourselves may might must
    shall per via yes ok okay
    according based context contexts provided document documents source sources passage passages
    cited citation citations given supplied information stated states state says mentioned
    """.split()
)

_CITATION = re.compile(r"\[([^\[\]\n]{1,200})\]")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,3}[.)])\s+")
# Negation words, plus verbs that negate ("excludes"), so "excludes X" matches "does not cover X".
_NEGATION_TEXT = re.compile(
    r"\b(?:not|no|never|none|nothing|cannot|neither|nor|without|except|exclud(?:e|es|ed|ing)|"
    r"lacks?|prohibit(?:s|ed)?|forbid(?:s|den)?)\b|n't\b",
    re.I,
)

# Pleasantries and offers of help are not factual claims.
_FILLER = re.compile(
    r"^(?:thanks?\b|thank you|i hope|hope this|glad|happy to|sure\b|certainly\b|of course|"
    r"great question|you're welcome|let me know|feel free|please let)",
    re.IGNORECASE,
)
_ABSTENTION = re.compile(
    r"\b(?:"
    r"(?:does|do|did)\s*(?:not|n't)\s+(?:contain|include|mention|provide|say|specify|state|"
    r"cover|address|discuss|indicate)"
    r"|(?:no|not\s+enough|insufficient)\s+(?:relevant\s+)?(?:information|details|evidence|data)"
    r"|(?:is|are|was|were)\s*(?:not|n't)\s+(?:mentioned|specified|stated|provided|available|"
    r"covered|included|given)"
    r"|i\s+(?:do\s*not|don't)\s+know"
    r"|(?:cannot|can't|can\s+not|unable\s+to|not\s+able\s+to)\s+(?:find|determine|answer|tell|"
    r"say|confirm)"
    r")\b",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------------- text helpers


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def numbers(text: str) -> set[str]:
    """Numbers in the text, with thousands separators removed (``1,000`` -> ``1000``)."""
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in _NUMBER.findall(cleaned)}


def content_words(text: str) -> set[str]:
    """Normalized, stemmed, non-stopword, non-numeric words."""
    return {
        _stem(w)
        for w in normalize(text).split()
        if w not in STOPWORDS and not w.isdigit() and len(w) > 1
    }


def has_negation(text: str) -> bool:
    return bool(_NEGATION_TEXT.search(text))


def is_abstention(text: str) -> bool:
    return bool(_ABSTENTION.search(text))


def strip_citations(text: str) -> str:
    text = " ".join(_CITATION.sub(" ", text).split())
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def clip(text: str | None, limit: int = MAX_EXCERPT_CHARS) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def sentences(text: str) -> list[str]:
    """Split text into sentences and list items (one per line for bullets)."""
    out: list[str] = []
    for line in text.splitlines():
        line = _BULLET.sub("", line).strip()
        if not line:
            continue
        out += [s.strip() for s in _SENTENCE_END.split(line) if s.strip()]
    return out


# ----------------------------------------------------------------------------- passages


@dataclass(frozen=True)
class Passage:
    """One unit of supplied evidence, labelled the way the prompt builder labels it."""

    label: str
    text: str
    position: int
    document_id: str | None = None
    chunk_id: str | None = None
    source: str | None = None
    page: int | None = None
    relevant: bool | None = None

    def reference(self) -> dict[str, Any]:
        return {
            "passage": self.label,
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "source": self.source,
            "page": self.page,
        }


def passage_label(ctx: dict[str, Any], position: int) -> str:
    """Must match ``app.execution.prompting._context_block``."""
    label = ctx.get("document_id") or f"doc-{position}"
    if ctx.get("chunk_id"):
        label = f"{label}#{ctx['chunk_id']}"
    return str(label)


def passages_for(case: dict[str, Any]) -> list[Passage]:
    """The evidence supplied with a case: retrieved contexts (rag) or the context paragraphs
    (long_context). Other case types supply none."""
    if case.get("type") == "rag":
        out = []
        for i, ctx in enumerate(case.get("retrieved_contexts") or [], start=1):
            relevant = ctx.get("relevant")
            out.append(
                Passage(
                    label=passage_label(ctx, i),
                    text=str(ctx.get("text") or ""),
                    position=i,
                    document_id=ctx.get("document_id"),
                    chunk_id=ctx.get("chunk_id"),
                    source=ctx.get("source"),
                    page=ctx.get("page"),
                    relevant=relevant if isinstance(relevant, bool) else None,
                )
            )
        return out
    if case.get("type") == "long_context" and case.get("context"):
        paragraphs = [p for p in re.split(r"\n\s*\n", str(case["context"])) if p.strip()]
        return [
            Passage(label=f"context#p{i}", text=p, position=i, chunk_id=f"p{i}")
            for i, p in enumerate(paragraphs, start=1)
        ]
    return []


def question_of(case: dict[str, Any]) -> str:
    if case.get("input"):
        return str(case["input"])
    users = [m.get("content") or "" for m in case.get("messages") or [] if m.get("role") == "user"]
    return str(users[-1]) if users else ""


# ----------------------------------------------------------------------------- citations


def resolve_citation(ref: str, passages: list[Passage]) -> list[Passage]:
    """Passages a citation refers to: a passage label, a chunk id, a document id (all its
    chunks), or a 1-based passage number. Unknown references resolve to nothing."""
    ref = ref.strip().strip("\"'").strip()
    if not ref:
        return []
    for key in ("label", "chunk_id"):
        hit = [p for p in passages if getattr(p, key) == ref]
        if hit:
            return hit
    hit = [p for p in passages if p.document_id == ref]
    if hit:
        return hit
    if ref.isdigit() and 1 <= int(ref) <= len(passages):
        return [passages[int(ref) - 1]]
    return []


def citations_in(text: str) -> list[str]:
    """Bracketed citation references, e.g. ``[policy-001]`` or ``[doc-1; doc-2]``."""
    refs: list[str] = []
    for group in _CITATION.findall(text):
        for part in re.split(r"[,;]", group):
            part = part.strip()
            if part and part not in refs:
                refs.append(part)
    return refs


# ----------------------------------------------------------------------------- claims


@dataclass
class Claim:
    text: str
    citations: list[str] = field(default_factory=list)


@dataclass
class ClaimVerdict:
    claim: str
    classification: Classification
    method: str
    rationale: str
    evidence: str | None = None
    passage: Passage | None = None
    supporting: list[str] = field(default_factory=list)  # passage labels that support it
    citations: list[str] = field(default_factory=list)
    confidence: float | None = None  # only when the evaluator reports one
    coverage: float | None = None  # lexical word coverage
    evidence_verified: bool | None = None  # judge quotes are checked against the passage
    review_recommended: bool = False

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "claim": clip(self.claim),
            "classification": self.classification,
            "evidence": clip(self.evidence),
            **(self.passage.reference() if self.passage else {"passage": None}),
            "supporting_passages": self.supporting,
            "citations": self.citations,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "method": self.method,
            "review_recommended": self.review_recommended,
            # Human review (spec §5.9) is recorded per claim; nothing has been reviewed yet.
            "review_status": "not_reviewed",
        }
        if self.coverage is not None:
            out["coverage"] = round(self.coverage, 3)
        if self.evidence_verified is not None:
            out["evidence_verified"] = self.evidence_verified
        return out


class ClaimExtractor(Protocol):
    name: str
    version: str

    async def extract(self, question: str, answer: str) -> list[Claim]: ...


class ClaimVerifier(Protocol):
    name: str
    version: str

    async def verify(self, claims: list[Claim], passages: list[Passage]) -> list[ClaimVerdict]: ...


class SentenceClaimExtractor:
    """Each sentence or list item with at least two content words is one claim. Abstaining
    sentences ("the context does not say") and questions are not claims."""

    name = "sentence_claims"
    version = "1.0.0"

    async def extract(self, question: str, answer: str) -> list[Claim]:
        return sentence_claims(answer)


def sentence_claims(answer: str) -> list[Claim]:
    claims: list[Claim] = []
    for sentence in sentences(answer):
        text = strip_citations(sentence)
        if (
            text.endswith("?")
            or _FILLER.match(text)
            or is_abstention(text)
            or len(content_words(text)) < 2
        ):
            continue
        claims.append(Claim(text=text, citations=citations_in(sentence)))
        if len(claims) >= MAX_CLAIMS:
            break
    return claims


@dataclass
class EvidenceUnit:
    passage: Passage
    text: str
    words: set[str]
    numbers: set[str]


def evidence_units(passages: list[Passage]) -> list[EvidenceUnit]:
    """Windows of one to three consecutive sentences per passage, so that long passages do
    not support claims by accidental word overlap."""
    units: list[EvidenceUnit] = []
    for p in passages:
        sents = sentences(p.text) or [p.text]
        for size in range(1, LONG_CONTEXT_WINDOW_SENTENCES + 1):
            for start in range(0, max(1, len(sents) - size + 1)):
                text = " ".join(sents[start : start + size])
                units.append(EvidenceUnit(p, text, content_words(text), numbers(text)))
            if len(sents) <= size:
                break
    return units


class LexicalClaimVerifier:
    """Word-overlap support check. A claim stating a number that no passage mentions, where
    the closest evidence states a different one, is contradicted; a polarity mismatch is
    flagged for review rather than called a contradiction."""

    name = "lexical_overlap"
    version = "1.0.0"

    def __init__(self, support_threshold: float = DEFAULT_SUPPORT_THRESHOLD) -> None:
        self.support_threshold = support_threshold

    async def verify(self, claims: list[Claim], passages: list[Passage]) -> list[ClaimVerdict]:
        units = evidence_units(passages)
        return [self.verify_one(c, units) for c in claims]

    def verify_one(self, claim: Claim, units: list[EvidenceUnit]) -> ClaimVerdict:
        words, nums = content_words(claim.text), numbers(claim.text)
        negated = has_negation(claim.text)
        base = {"claim": claim.text, "method": self.name, "citations": claim.citations}
        if not units or not words:
            return ClaimVerdict(
                classification="unverifiable",
                rationale="There is no evidence text to compare the claim with.",
                review_recommended=True,
                **base,
            )
        th = self.support_threshold

        def coverage(u: EvidenceUnit) -> float:
            return len(words & u.words) / len(words)

        def supports(u: EvidenceUnit) -> bool:
            return coverage(u) >= th and nums <= u.numbers and has_negation(u.text) == negated

        # Prefer the unit that covers the most words, then the shortest (most specific) one.
        best = max(units, key=lambda u: (coverage(u), -len(u.text)))
        cov = coverage(best)
        supporting = sorted({u.passage.label for u in units if supports(u)})
        details: dict[str, Any] = {
            "evidence": best.text,
            "passage": best.passage,
            "coverage": cov,
            "supporting": supporting,
        }
        if supports(best):
            return ClaimVerdict(
                classification="supported",
                rationale=f"{cov:.0%} of the claim's content words appear in the evidence.",
                **details,
                **base,
            )
        # A claim that combines two passages ("A opened in 2014; B opened in 2019").
        rest = words - best.words
        second = max(
            (u for u in units if u is not best),
            key=lambda u: (len(rest & u.words), -len(u.text)),
            default=None,
        )
        if second is not None and rest & second.words:
            combined = len(words & (best.words | second.words)) / len(words)
            if (
                combined >= th
                and nums <= best.numbers | second.numbers
                and (has_negation(best.text) or has_negation(second.text)) == negated
            ):
                labels = sorted({best.passage.label, second.passage.label})
                return ClaimVerdict(
                    classification="supported",
                    rationale=f"{combined:.0%} of the claim's content words appear across "
                    f"{len(labels)} evidence excerpts.",
                    review_recommended=len(labels) > 1 and second.passage is not best.passage,
                    **(
                        details
                        | {
                            "evidence": f"{best.text} … {second.text}",
                            "coverage": combined,
                            "supporting": labels,
                        }
                    ),
                    **base,
                )
        everywhere = set().union(*(u.numbers for u in units))
        missing = nums - best.numbers
        if (
            cov >= CONTRADICTION_MIN_COVERAGE
            and missing
            and best.numbers
            and not (missing & everywhere)
        ):
            return ClaimVerdict(
                classification="contradicted",
                rationale=(
                    f"The closest evidence states {', '.join(sorted(best.numbers))}, but the "
                    f"claim states {', '.join(sorted(missing))}, which no passage mentions."
                ),
                **details,
                **base,
            )
        if cov < UNSUPPORTED_BELOW:
            return ClaimVerdict(
                classification="unsupported",
                rationale=(
                    f"No evidence covers the claim (best word coverage {cov:.0%}); it may be "
                    "added knowledge or a hallucination."
                ),
                **details,
                **base,
            )
        if cov >= th and has_negation(best.text) != negated:
            reason = "The closest evidence has the opposite polarity (negation)."
        elif cov >= th:
            reason = (
                f"The claim's numbers ({', '.join(sorted(missing))}) are not all in the closest "
                "evidence."
            )
        else:
            reason = f"Partial word coverage ({cov:.0%}); a paraphrase or a partly supported claim."
        return ClaimVerdict(
            classification="unverifiable",
            rationale=reason + " Needs review.",
            review_recommended=True,
            **details,
            **base,
        )


def counts(verdicts: list[ClaimVerdict]) -> dict[str, int]:
    out = dict.fromkeys(CLASSIFICATIONS, 0)
    for v in verdicts:
        out[v.classification] += 1
    return out


def faithfulness(c: dict[str, int]) -> float | None:
    """Supported / (supported + unsupported + contradicted); unverifiable claims are left out.
    ``None`` when no claim could be decided."""
    decided = c["supported"] + c["unsupported"] + c["contradicted"]
    return c["supported"] / decided if decided else None
