"""RAG and hallucination evaluators (spec §5.8, §5.9).

Every evaluator here reads only the evidence supplied with the case (retrieved contexts, or a
long-context document). Claim-based evaluators share one claim analysis per case: claims are
extracted, classified against the passages, and stored with their evidence excerpts and source
identifiers. Without a judge the analysis is lexical (``app.evaluators.evidence``); with a judge
(opt-in) the judge extracts and verifies claims. Missing reference data makes an evaluator
``not_applicable``; it never produces a score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.evaluators.answer import contains as contains_text
from app.evaluators.answer import references, token_f1
from app.evaluators.base import EvaluationContext, EvaluationOutcome, Evaluator, ModelOutput
from app.evaluators.evidence import (
    DEFAULT_SUPPORT_THRESHOLD,
    Claim,
    ClaimVerdict,
    LexicalClaimVerifier,
    Passage,
    SentenceClaimExtractor,
    citations_in,
    clip,
    content_words,
    counts,
    faithfulness,
    is_abstention,
    passages_for,
    question_of,
    resolve_citation,
    sentence_claims,
    sentences,
)
from app.evaluators.judge import JudgeClaimExtractor, JudgeClaimVerifier, JudgeError

RAG_CASE_TYPES = frozenset({"rag"})
EVIDENCE_CASE_TYPES = frozenset({"rag", "long_context"})
DEFAULT_FAITHFULNESS_THRESHOLD = 0.8
CONTEXT_RELEVANCE_COVERAGE = 0.4
PASS_RATIO = 0.5


@dataclass
class ClaimAnalysis:
    method: str
    extractor: dict[str, str]
    verifier: dict[str, str]
    passages: list[Passage]
    abstention: str  # abstained | partial | answered | empty
    claims: list[Claim]
    verdicts: list[ClaimVerdict]
    judge: dict[str, Any] | None = None
    counts: dict[str, int] = field(default_factory=dict)

    def metadata(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "extractor": self.extractor,
            "verifier": self.verifier,
            "judge": self.judge,
            "abstention": self.abstention,
            "claim_counts": self.counts,
            "passages": len(self.passages),
            "review_recommended": any(v.review_recommended for v in self.verdicts),
        }


def _pipeline(context: EvaluationContext) -> tuple[Any, Any, str]:
    if context.judge is not None:
        return JudgeClaimExtractor(context.judge), JudgeClaimVerifier(context.judge), "judge"
    threshold = float(context.config.get("support_threshold", DEFAULT_SUPPORT_THRESHOLD))
    return SentenceClaimExtractor(), LexicalClaimVerifier(threshold), "lexical"


def _abstention(answer: str, claims: list[Claim]) -> str:
    if not answer.strip():
        return "empty"
    if not any(is_abstention(s) for s in sentences(answer)):
        return "answered"
    return "partial" if claims else "abstained"


async def analyze(
    case: dict[str, Any], output: ModelOutput, context: EvaluationContext
) -> ClaimAnalysis:
    """The case's claim analysis, computed once and shared by every evaluator of the case. A
    judge failure is cached too, so a failing judge is not called again for the same case."""
    cached = context.cache.get("claims")
    if isinstance(cached, Exception):
        raise cached
    if cached is not None:
        return cached
    try:
        result = await _analyze(case, output, context)
    except JudgeError as exc:
        context.cache["claims"] = exc
        raise
    context.cache["claims"] = result
    return result


async def _analyze(
    case: dict[str, Any], output: ModelOutput, context: EvaluationContext
) -> ClaimAnalysis:
    answer = output.content or ""
    passages = passages_for(case)
    extractor, verifier, method = _pipeline(context)
    claims = await extractor.extract(question_of(case), answer) if answer.strip() else []
    verdicts = await verifier.verify(claims, passages) if claims else []
    return ClaimAnalysis(
        method=method,
        extractor={"name": extractor.name, "version": extractor.version},
        verifier={"name": verifier.name, "version": verifier.version},
        passages=passages,
        abstention=_abstention(answer, claims),
        claims=claims,
        verdicts=verdicts,
        judge=context.judge.describe() if context.judge is not None else None,
        counts=counts(verdicts),
    )


def _faith_threshold(context: EvaluationContext) -> float:
    return float(context.config.get("faithfulness_threshold", DEFAULT_FAITHFULNESS_THRESHOLD))


def _failed(evaluator: Evaluator, exc: JudgeError) -> EvaluationOutcome:
    return evaluator.outcome("failed", reason=str(exc), metadata={"method": "judge"})


def _summary(c: dict[str, int]) -> str:
    return (
        f"{c['supported']} supported, {c['unsupported']} unsupported, "
        f"{c['contradicted']} contradicted, {c['unverifiable']} unverifiable"
    )


class _RagEvaluator(Evaluator):
    suite = "rag"
    case_types = RAG_CASE_TYPES


class RagFaithfulness(_RagEvaluator):
    name = "rag_faithfulness"
    kind = "evidence"
    title = "Faithfulness"
    description = (
        "Share of the answer's decided claims that the retrieved contexts support: supported / "
        "(supported + unsupported + contradicted). Contradicted claims are reported separately "
        "and fail the case. Unverifiable claims are left out and flagged for review. Not "
        "applicable when the answer makes no factual claims."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        try:
            a = await analyze(case, output, context)
        except JudgeError as exc:
            return _failed(self, exc)
        meta = a.metadata()
        if a.abstention == "empty":
            return self.not_applicable("The model returned no text.", metadata=meta)
        if not a.verdicts:
            reason = (
                "The answer abstains; there are no claims to check."
                if a.abstention == "abstained"
                else "No factual claims were extracted from the answer."
            )
            return self.not_applicable(reason, metadata=meta)
        c = a.counts
        score = faithfulness(c)
        threshold = float(
            context.config.get("faithfulness_threshold", DEFAULT_FAITHFULNESS_THRESHOLD)
        )
        meta["threshold"] = threshold
        evidence = [v.as_dict() for v in a.verdicts]
        if score is None:
            return self.outcome(
                "requires_review",
                reason=f"No claim could be decided ({_summary(c)}).",
                evidence=evidence,
                metadata=meta,
            )
        passed = score >= threshold and c["contradicted"] == 0
        return self.result(
            passed,
            score=round(score, 6),
            label="contradicted" if c["contradicted"] else "faithful" if passed else "unfaithful",
            reason=f"Faithfulness {score:.2f} ({_summary(c)}; pass threshold {threshold}).",
            evidence=evidence,
            metadata=meta,
        )


class ClaimSupport(Evaluator):
    name = "claim_support"
    suite = "hallucination"
    kind = "evidence"
    title = "Claim support"
    case_types = EVIDENCE_CASE_TYPES
    description = (
        "Classifies every claim in the answer as supported, unsupported, contradicted, or "
        "unverifiable against the supplied evidence. Passes when no claim is unsupported or "
        "contradicted. It cannot prove a claim true or false; it compares claims with the "
        "evidence supplied for the case."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        try:
            a = await analyze(case, output, context)
        except JudgeError as exc:
            return _failed(self, exc)
        meta = a.metadata() | {"answerable": bool(case.get("answerable", True))}
        if a.abstention == "empty":
            return self.not_applicable("The model returned no text.", metadata=meta)
        if not a.verdicts:
            return self.not_applicable(
                "The answer abstains." if a.abstention == "abstained" else "No factual claims.",
                metadata=meta,
            )
        c = a.counts
        total = len(a.verdicts)
        worst = next(
            (k for k in ("contradicted", "unsupported", "unverifiable") if c[k]), "supported"
        )
        return self.result(
            c["unsupported"] == 0 and c["contradicted"] == 0,
            score=round(c["supported"] / total, 6),
            label=worst,
            reason=f"{total} claim(s): {_summary(c)}.",
            evidence=[v.as_dict() for v in a.verdicts],
            metadata=meta,
        )


class RagAbstention(_RagEvaluator):
    name = "rag_abstention"
    kind = "rule_based"
    title = "Abstention"
    description = (
        "Cases marked answerable: false should be declined ('the context does not say'); "
        "answerable cases should be answered. Detects abstention with English phrases, so it is "
        "a heuristic."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        answer = output.content or ""
        answerable = bool(case.get("answerable", True))
        state = _abstention(answer, sentence_claims(answer))
        meta = {"answerable": answerable, "abstention": state, "method": "phrase_heuristic"}
        if state == "empty":
            return self.result(False, label="empty", reason="The model returned no text.")
        if answerable:
            passed = state != "abstained"
            label = "unnecessary_abstention" if not passed else "answered"
            reason = (
                "The model declined although the contexts contain the answer."
                if not passed
                else "The model answered an answerable question."
            )
        else:
            passed = state == "abstained"
            label = "appropriate_abstention" if passed else "answered_without_evidence"
            reason = (
                "The model declined; the contexts do not contain the answer."
                if passed
                else "The model answered although the contexts do not contain the answer"
                + (" (it also hedged)." if state == "partial" else ".")
            )
        return self.result(passed, label=label, reason=reason, metadata=meta)


class RagAnswerRelevance(_RagEvaluator):
    name = "rag_answer_relevance"
    kind = "model_judged"
    title = "Answer relevance"
    description = (
        "Whether the answer addresses the question (judge score 1-5, passing at 4), regardless "
        "of correctness. Needs an LLM judge; skipped without one."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        if context.judge is None:
            return self.outcome("skipped", reason="Answer relevance needs an LLM judge.")
        answer = output.content or ""
        if not answer.strip():
            return self.result(False, label="empty", reason="The model returned no text.")
        try:
            score, rationale = await context.judge.answer_relevance(question_of(case), answer)
        except JudgeError as exc:
            return _failed(self, exc)
        return self.result(
            score >= 4,
            score=(score - 1) / 4,
            label=f"{score}/5",
            reason=rationale or f"Judge score {score}/5.",
            metadata={"method": "judge", "judge": context.judge.describe(), "raw_score": score},
        )


class RagContextRelevance(_RagEvaluator):
    name = "rag_context_relevance"
    kind = "evidence"
    title = "Context relevance"
    description = (
        "Share of retrieved passages that are relevant to the question (passing at half). This "
        "measures the retrieval, not the model. Without a judge a passage is relevant when it "
        "contains at least 40% of the question's content words."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        passages = passages_for(case)
        question = question_of(case)
        verdicts: dict[str, tuple[bool, str]]
        if context.judge is not None:
            try:
                verdicts = await context.judge.context_relevance(question, passages)
            except JudgeError as exc:
                return _failed(self, exc)
            method = "judge"
        else:
            q = content_words(question)
            if not q:
                return self.not_applicable("The question has no content words to compare.")
            verdicts = {}
            for p in passages:
                cov = len(q & content_words(p.text)) / len(q)
                verdicts[p.label] = (
                    cov >= CONTEXT_RELEVANCE_COVERAGE,
                    f"{cov:.0%} of the question's content words",
                )
            method = "lexical"
        judged = [p for p in passages if p.label in verdicts]
        if not judged:
            return self.outcome("failed", reason="The judge rated none of the passages.")
        relevant = sum(1 for p in judged if verdicts[p.label][0])
        score = relevant / len(judged)
        return self.result(
            score >= PASS_RATIO,
            score=round(score, 6),
            label="no_relevant_context" if relevant == 0 else None,
            reason=f"{relevant} of {len(judged)} passage(s) are relevant to the question.",
            evidence=[
                {
                    **p.reference(),
                    "relevant": verdicts[p.label][0],
                    "rationale": verdicts[p.label][1],
                }
                for p in judged
            ],
            metadata={
                "method": method,
                "judge": context.judge.describe() if context.judge is not None else None,
                "unrated_passages": len(passages) - len(judged),
            },
        )

    def result(self, passed: bool, score: float | None = None, **kwargs: Any) -> EvaluationOutcome:
        if kwargs.get("label") is None:
            kwargs["label"] = "pass" if passed else "fail"
        return super().result(passed, score, **kwargs)


class RagContextPrecision(_RagEvaluator):
    name = "rag_context_precision"
    title = "Context precision"
    description = (
        "Average precision of the retrieved passages, in retrieval order, using the dataset's "
        "'relevant' labels: 1.0 when every relevant passage is ranked above every irrelevant "
        "one. Not applicable without labels."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        labeled = [p for p in passages_for(case) if p.relevant is not None]
        if not labeled:
            return self.not_applicable("No retrieved context carries a 'relevant' label.")
        hits, total = 0, 0.0
        for rank, p in enumerate(labeled, start=1):
            if p.relevant:
                hits += 1
                total += hits / rank
        if hits == 0:
            return self.result(
                False,
                score=0.0,
                label="no_relevant_context",
                reason="No retrieved passage is labeled relevant.",
            )
        ap = total / hits
        return self.result(
            ap >= PASS_RATIO,
            score=round(ap, 6),
            reason=f"Average precision {ap:.2f} over {len(labeled)} labeled passage(s).",
            evidence=[{**p.reference(), "relevant": p.relevant} for p in labeled],
            metadata={"labeled_passages": len(labeled), "relevant_passages": hits},
        )


class RagContextRecall(_RagEvaluator):
    name = "rag_context_recall"
    kind = "evidence"
    title = "Context recall"
    description = (
        "Share of the reference answer's claims that the retrieved contexts support, i.e. "
        "whether retrieval found the information needed. Passes at the faithfulness threshold. "
        "Not applicable without a reference answer."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        if not case.get("answerable", True):
            return self.not_applicable("The case is unanswerable from its contexts.")
        reference = case.get("reference_answer")
        if not isinstance(reference, str) or not reference.strip():
            return self.not_applicable("The case has no reference answer.")
        extractor, verifier, method = _pipeline(context)
        try:
            claims = await extractor.extract(question_of(case), reference)
            if not claims and content_words(reference):
                claims = [Claim(text=reference.strip())]
            if not claims:
                return self.not_applicable("The reference answer has no checkable claims.")
            verdicts = await verifier.verify(claims, passages_for(case))
        except JudgeError as exc:
            return _failed(self, exc)
        c = counts(verdicts)
        score = c["supported"] / len(verdicts)
        threshold = float(
            context.config.get("faithfulness_threshold", DEFAULT_FAITHFULNESS_THRESHOLD)
        )
        return self.result(
            score >= threshold,
            score=round(score, 6),
            reason=f"{c['supported']} of {len(verdicts)} reference claim(s) are in the contexts.",
            evidence=[v.as_dict() for v in verdicts],
            metadata={
                "method": method,
                "claim_counts": c,
                "threshold": threshold,
                "judge": context.judge.describe() if context.judge is not None else None,
            },
        )


def _matches(passage: Passage, expected: dict[str, Any]) -> bool:
    if passage.document_id != expected.get("document_id"):
        return False
    if expected.get("chunk_id") and passage.chunk_id != expected["chunk_id"]:
        return False
    page = expected.get("page")
    return page is None or passage.page is None or passage.page == page


class RagCitationRecall(_RagEvaluator):
    name = "rag_citation_recall"
    kind = "evidence"
    title = "Citation recall"
    description = (
        "Share of the expected citations that the answer cites (bracketed passage, chunk, or "
        "document IDs). Not applicable when the case lists no expected citations."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        expected = case.get("expected_citations") or []
        if not expected:
            return self.not_applicable("The case lists no expected citations.")
        passages = passages_for(case)
        refs = citations_in(output.content or "")
        cited = [p for r in refs for p in resolve_citation(r, passages)]
        invented = [r for r in refs if not resolve_citation(r, passages)]
        found = [e for e in expected if any(_matches(p, e) for p in cited)]
        score = len(found) / len(expected)
        return self.result(
            len(found) == len(expected),
            score=round(score, 6),
            label="complete" if len(found) == len(expected) else "missing_citations",
            reason=f"The answer cites {len(found)} of {len(expected)} expected source(s).",
            evidence=[{**e, "cited": any(_matches(p, e) for p in cited)} for e in expected],
            metadata={"citations": refs, "unknown_citations": invented},
        )


class RagCitationPrecision(_RagEvaluator):
    name = "rag_citation_precision"
    kind = "evidence"
    title = "Citation precision"
    description = (
        "Share of (claim, cited source) pairs where the cited passage supports the claim. A "
        "citation of a source that was not supplied counts as unsupported. Citation presence "
        "alone proves nothing. Passes at the faithfulness threshold; not applicable when the "
        "answer cites nothing."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        answer = output.content or ""
        if not citations_in(answer):
            return self.not_applicable("The answer cites no sources.")
        try:
            a = await analyze(case, output, context)
            verdicts = a.verdicts
            if not any(v.citations for v in verdicts):
                # The judge dropped the markers; associate citations by sentence instead.
                cached = context.cache.get("citation_verdicts")
                if cached is None:
                    _, verifier, _ = _pipeline(context)
                    cached = await verifier.verify(sentence_claims(answer), a.passages)
                    context.cache["citation_verdicts"] = cached
                verdicts = cached
        except JudgeError as exc:
            return _failed(self, exc)
        pairs: list[dict[str, Any]] = []
        for v in verdicts:
            for ref in v.citations:
                resolved = resolve_citation(ref, a.passages)
                supported = any(p.label in v.supporting for p in resolved)
                pairs.append(
                    {
                        "claim": clip(v.claim),
                        "citation": ref,
                        "resolved": [p.label for p in resolved],
                        "supported": supported,
                        "reason": "The cited source was not supplied."
                        if not resolved
                        else (
                            "The cited passage supports the claim."
                            if supported
                            else "The cited passage does not support the claim."
                        ),
                    }
                )
        if not pairs:
            return self.not_applicable("No citation is attached to a factual claim.")
        good = sum(1 for p in pairs if p["supported"])
        score = good / len(pairs)
        threshold = float(
            context.config.get("faithfulness_threshold", DEFAULT_FAITHFULNESS_THRESHOLD)
        )
        return self.result(
            score >= threshold,
            score=round(score, 6),
            reason=f"{good} of {len(pairs)} citation(s) support the claim they are attached to.",
            evidence=pairs,
            metadata={
                "method": a.method,
                "threshold": threshold,
                "unknown_citations": sum(1 for p in pairs if not p["resolved"]),
            },
        )


class RagAnswerCorrectness(_RagEvaluator):
    name = "rag_answer_correctness"
    title = "Answer correctness"
    description = (
        "The answer contains the reference answer, or its token F1 against it reaches the F1 "
        "threshold. Word overlap is a rough proxy for correctness. Not applicable without a "
        "reference answer or for unanswerable cases."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        if not case.get("answerable", True):
            return self.not_applicable("The case is unanswerable; see Abstention.")
        refs = references(case)
        if not refs:
            return self.not_applicable("The case has no reference answer.")
        answer = output.content or ""
        if not answer.strip():
            return self.result(False, label="empty", reason="The model returned no text.")
        threshold = float(context.config.get("token_f1_threshold", 0.5))
        best, ref = max(((token_f1(answer, r), r) for r in refs), key=lambda s: s[0])
        contained = any(contains_text(answer, r) for r in refs)
        passed = contained or best >= threshold
        return self.result(
            passed,
            score=round(best, 6),
            label="contains_reference" if contained else ("pass" if passed else "fail"),
            reason=f"Token F1 {best:.2f} (threshold {threshold})"
            + ("; the answer contains the reference." if contained else "."),
            evidence=[{"reference": clip(ref)}],
            metadata={"threshold": threshold, "method": "lexical"},
        )


def rag_evaluators() -> list[Evaluator]:
    return [
        RagFaithfulness(),
        RagAnswerCorrectness(),
        RagAnswerRelevance(),
        RagContextRelevance(),
        RagContextPrecision(),
        RagContextRecall(),
        RagCitationPrecision(),
        RagCitationRecall(),
        RagAbstention(),
    ]


def hallucination_evaluators() -> list[Evaluator]:
    return [ClaimSupport()]
