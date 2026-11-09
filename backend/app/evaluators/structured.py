"""Structured-output evaluators: JSON validity, schema conformance, expected-output match."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from jsonschema import Draft202012Validator

from app.evaluators.base import Evaluator, ModelOutput

_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*\n(.*?)\n?```\s*$", re.DOTALL)
MAX_VIOLATIONS = 10


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def parse_json(
    text: str | None, allow_code_fence: bool = True
) -> tuple[Any, str | None, str | None]:
    """Parse model output as JSON. Returns (value, method, error).

    ``method`` is ``raw`` (the whole output is JSON) or ``code_fence`` (the whole output is one
    fenced block). Nothing else is extracted: text around the JSON makes it invalid.
    """
    if text is None or not text.strip():
        return None, None, "The output is empty."
    try:
        return json.loads(text, parse_constant=_reject_constant), "raw", None
    except ValueError as exc:
        error = str(exc)
    match = _FENCE.match(text) if allow_code_fence else None
    if match:
        try:
            return json.loads(match.group(1), parse_constant=_reject_constant), "code_fence", None
        except ValueError as exc:
            error = str(exc)
    return None, None, error[:300]


def json_equal(a: Any, b: Any) -> bool:
    """JSON equality with numeric comparison (1 == 1.0) and no bool/number confusion."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b, strict=True))
    return type(a) is type(b) and a == b


def leaves(value: Any, path: str = "") -> dict[str, Any]:
    """Flatten JSON into {json_pointer: leaf}. Empty containers count as leaves."""
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for k, v in value.items():
            out |= leaves(v, f"{path}/{str(k).replace('~', '~0').replace('/', '~1')}")
        return out
    if isinstance(value, list) and value:
        out = {}
        for i, v in enumerate(value):
            out |= leaves(v, f"{path}/{i}")
        return out
    return {path or "/": value}


def schema_violations(schema: dict[str, Any], instance: Any) -> list[dict[str, str]]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance), key=lambda e: list(e.absolute_path)
    )
    return [
        {"path": "/" + "/".join(str(p) for p in e.absolute_path), "message": e.message[:300]}
        for e in errors[:MAX_VIOLATIONS]
    ]


class _StructuredEvaluator(Evaluator):
    suite = "structured_output"
    case_types = frozenset({"structured_output"})

    def _parse(
        self, output: ModelOutput, config: dict[str, Any]
    ) -> tuple[Any, str | None, str | None]:
        return parse_json(output.content, bool(config.get("allow_code_fence", True)))


class JsonValid(_StructuredEvaluator):
    name = "json_valid"
    title = "JSON validity"
    description = (
        "The whole answer parses as JSON. A single ```json fenced block is accepted and labeled "
        "(turn off allow_code_fence to require bare JSON)."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        _value, method, error = self._parse(output, context.config)
        if method is None:
            return self.result(False, label="invalid", reason=f"Not valid JSON: {error}")
        return self.result(
            True,
            label="valid" if method == "raw" else "valid_in_code_fence",
            reason="Valid JSON." if method == "raw" else "Valid JSON inside a code fence.",
            metadata={"parse_method": method},
        )


class JsonSchemaConformance(_StructuredEvaluator):
    name = "json_schema"
    title = "Schema conformance"
    description = "The parsed answer validates against the case's JSON Schema (draft 2020-12)."

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        schema = case.get("json_schema")
        if not isinstance(schema, dict):
            return self.not_applicable("The case has no JSON Schema.")
        value, method, error = self._parse(output, context.config)
        if method is None:
            return self.result(
                False, label="invalid_json", reason=f"Not valid JSON, so it cannot conform: {error}"
            )
        violations = schema_violations(schema, value)
        return self.result(
            not violations,
            label="conforms" if not violations else "violates",
            reason="Conforms to the schema."
            if not violations
            else f"{len(violations)} schema violation(s).",
            evidence=violations,
            metadata={"parse_method": method, "violation_count": len(violations)},
        )


class JsonExpectedMatch(_StructuredEvaluator):
    name = "json_expected_match"
    title = "Expected output match"
    description = (
        "The parsed answer equals the case's expected_output. The score is the fraction of "
        "expected fields (leaf values) the answer reproduces exactly."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        if case.get("expected_output") is None:
            return self.not_applicable("The case has no expected_output.")
        expected = case["expected_output"]
        value, method, error = self._parse(output, context.config)
        if method is None:
            return self.result(
                False, score=0.0, label="invalid_json", reason=f"Not valid JSON: {error}"
            )
        exp_leaves, got_leaves = leaves(expected), leaves(value)
        matched = [
            p for p, v in exp_leaves.items() if p in got_leaves and json_equal(got_leaves[p], v)
        ]
        mismatched = [p for p in exp_leaves if p not in matched]
        extra = [p for p in got_leaves if p not in exp_leaves]
        equal = json_equal(value, expected)
        score = len(matched) / len(exp_leaves) if exp_leaves else float(equal)
        return self.result(
            equal,
            score=round(score, 6),
            label="exact" if equal else "partial" if matched else "mismatch",
            reason=(
                "Equals the expected output."
                if equal
                else f"{len(matched)} of {len(exp_leaves)} expected field(s) match"
                + (f"; {len(extra)} unexpected field(s)" if extra else "")
                + "."
            ),
            evidence=[
                {
                    "path": p,
                    "expected": json.dumps(exp_leaves[p])[:200],
                    "actual": json.dumps(got_leaves[p])[:200] if p in got_leaves else None,
                }
                for p in mismatched[:MAX_VIOLATIONS]
            ],
            metadata={
                "fields_expected": len(exp_leaves),
                "fields_matched": len(matched),
                "unexpected_fields": len(extra),
                "parse_method": method,
            },
        )


def structured_evaluators() -> list[Evaluator]:
    return [JsonValid(), JsonSchemaConformance(), JsonExpectedMatch()]
