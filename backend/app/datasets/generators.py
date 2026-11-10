"""Synthetic context-window datasets (spec §5.11): needle-in-a-haystack and multi-fact retrieval.

Deterministic: the same parameters and seed always produce the same JSONL, so the dataset hash
identifies the experiment. Each case records the *requested* context length and an estimate of
its size. The estimate uses a fixed characters-per-token heuristic, because model tokenizers
differ and none is downloaded; the actual input tokens come from the server at run time.
"""

from __future__ import annotations

import json
import random
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GENERATOR_VERSION = "1"
ESTIMATOR = {"method": "heuristic", "chars_per_token": 4.0, "version": "1"}
# The upper bound keeps one case below the default 4 MB record limit (DATASET_MAX_RECORD_BYTES).
MIN_CONTEXT, MAX_CONTEXT = 128, 524_288
MAX_CASES = 1000
# Total estimated tokens of a generated dataset (about 24 MB of text, under the upload limit).
MAX_TOTAL_TOKENS = 6_000_000

_TEAMS = ["support", "billing", "platform", "search", "data", "mobile", "infra", "security"]
_FILLER = [
    "The {team} team reviewed the backlog and closed {n} stale tickets.",
    "The {team} team reported that the error rate stayed below {f}% for the week.",
    "The {team} team shipped {n} small fixes to the internal dashboard.",
    "The {team} team asked for {n} more days to finish the migration plan.",
    "The {team} team confirmed that the staging environment was rebuilt.",
    "The {team} team scheduled a design review for the {team2} roadmap.",
    "The {team} team agreed to move the weekly sync to {day}.",
    "The {team} team noted that on-call pages dropped to {n} this week.",
]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
_PLACES = [
    "Lisbon",
    "Osaka",
    "Denver",
    "Nairobi",
    "Oslo",
    "Lima",
    "Hanoi",
    "Perth",
    "Quebec",
    "Tallinn",
    "Accra",
    "Bergen",
    "Cusco",
    "Dakar",
    "Fargo",
    "Galway",
    "Hobart",
    "Izmir",
]
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"


class NeedleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Requested context sizes in tokens (estimated with the heuristic above).
    context_lengths: list[int] = Field(min_length=1, max_length=16)
    # Needle positions as a fraction of the context (0 = start, 1 = end).
    depths: list[float] = Field(
        default_factory=lambda: [0.1, 0.5, 0.9], min_length=1, max_length=11
    )
    # 1 = single needle per case; more = multi-fact retrieval (needles spread over the context,
    # the question asks for all of them).
    needles_per_case: int = Field(default=1, ge=1, le=10)
    # Cases per (length, depth) cell, each with different needles.
    repeats: int = Field(default=1, ge=1, le=50)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)

    @field_validator("context_lengths")
    @classmethod
    def _lengths(cls, v: list[int]) -> list[int]:
        for n in v:
            if not MIN_CONTEXT <= n <= MAX_CONTEXT:
                raise ValueError(
                    f"Context lengths must be between {MIN_CONTEXT} and {MAX_CONTEXT}."
                )
        return sorted(set(v))

    @field_validator("depths")
    @classmethod
    def _depths(cls, v: list[float]) -> list[float]:
        if any(not 0 <= d <= 1 for d in v):
            raise ValueError("Depths must be between 0 and 1.")
        return sorted(set(v))


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    generator: Literal["needle"] = "needle"
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    project_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)
    params: NeedleParams


def _filler_paragraph(rng: random.Random, week: int) -> str:
    sentences = []
    for _ in range(4):
        team, team2 = rng.sample(_TEAMS, 2)
        sentences.append(
            rng.choice(_FILLER).format(
                team=team,
                team2=team2,
                n=rng.randint(2, 20),
                f=round(rng.uniform(0.1, 1.5), 1),
                day=rng.choice(_DAYS),
            )
        )
    return f"Week {week} notes. " + " ".join(sentences)


def _haystack(rng: random.Random, target_chars: int) -> list[str]:
    paragraphs: list[str] = []
    total, week = 0, 1
    while total < target_chars:
        p = _filler_paragraph(rng, week)
        paragraphs.append(p)
        total += len(p) + 2
        week += 1
    return paragraphs


def _code(rng: random.Random) -> str:
    return f"{rng.randint(1000, 9999)}-{rng.choice(_ALPHABET)}{rng.choice(_ALPHABET)}"


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / ESTIMATOR["chars_per_token"]))


def generate_needle_cases(params: NeedleParams) -> list[dict[str, Any]]:
    rng = random.Random(params.seed)  # noqa: S311  (deterministic test data, not security)
    multi = params.needles_per_case > 1
    cells = [(n, d) for n in params.context_lengths for d in ([None] if multi else params.depths)]
    if len(cells) * params.repeats > MAX_CASES:
        raise ValueError(f"At most {MAX_CASES} cases (got {len(cells) * params.repeats}).")
    total = sum(n for n, _ in cells) * params.repeats
    if total > MAX_TOTAL_TOKENS:
        raise ValueError(
            f"The dataset would hold about {total:,} tokens; the limit is {MAX_TOTAL_TOKENS:,}. "
            "Use fewer lengths, depths or repeats."
        )
    cases = []
    for length, depth in cells:
        for r in range(params.repeats):
            places = rng.sample(_PLACES, params.needles_per_case)
            codes = [_code(rng) for _ in places]
            needles = [
                f"The access code for the {p} vault is {c}."
                for p, c in zip(places, codes, strict=True)
            ]
            if multi:
                names = ", ".join(places[:-1]) + f" and {places[-1]}"
                question = (
                    f"According to the notes above, what are the access codes for the {names} "
                    "vaults? Answer with the codes only, separated by commas."
                )
                positions = [(i + 1) / (len(needles) + 1) for i in range(len(needles))]
            else:
                question = (
                    f"According to the notes above, what is the access code for the {places[0]} "
                    "vault? Answer with the code only."
                )
                positions = [depth or 0.0]
            target_chars = int(length * ESTIMATOR["chars_per_token"]) - len(question) - 2
            paragraphs = _haystack(rng, max(target_chars - sum(len(n) + 2 for n in needles), 200))
            inserted: list[float] = []
            # Insert from the deepest position so earlier indices stay valid.
            for needle, pos in sorted(zip(needles, positions, strict=True), key=lambda x: -x[1]):
                index = min(round(pos * len(paragraphs)), len(paragraphs))
                paragraphs.insert(index, needle)
                inserted.append(pos)
            context = "\n\n".join(paragraphs)
            first = context.find(needles[0])
            case: dict[str, Any] = {
                "id": f"ctx-{length}-" + ("multi" if multi else f"d{depth:g}") + f"-{r + 1}",
                "type": "long_context",
                "input": question,
                "context": context,
                "context_length": length,
                "context_estimate": {
                    "tokens": estimate_tokens(context) + estimate_tokens(question),
                    **ESTIMATOR,
                },
                "answer_location": {
                    "approximate_position": round(first / len(context), 4) if first >= 0 else None,
                    "requested_positions": sorted(inserted),
                },
                "tags": [f"ctx-{length}", "multi-fact" if multi else f"depth-{depth:g}"],
                "metadata": {
                    "generator": "needle",
                    "generator_version": GENERATOR_VERSION,
                    "seed": params.seed,
                },
            }
            if multi:
                case["expected_answers"] = codes
            else:
                case["expected_answer"] = codes[0]
            cases.append(case)
    return cases


def describe(params: NeedleParams) -> str:
    kind = (
        f"multi-fact retrieval ({params.needles_per_case} facts per case)"
        if params.needles_per_case > 1
        else "needle retrieval at depths " + ", ".join(f"{d:g}" for d in params.depths)
    )
    return (
        f"Generated {kind}; requested context lengths "
        + ", ".join(f"{n:,}" for n in params.context_lengths)
        + f" tokens; {params.repeats} repeat(s); seed {params.seed}; generator needle v"
        + f"{GENERATOR_VERSION}. Sizes are estimated at {ESTIMATOR['chars_per_token']:g} "
        "characters per token; the actual input tokens are reported by the server at run time."
    )


def to_jsonl(cases: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(c, ensure_ascii=False, sort_keys=True) for c in cases) + "\n"
