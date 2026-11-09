"""Tool-calling evaluators (spec §5.7) and fixture lookup for simulated execution (mode B).

Nothing here executes a tool. Simulated execution only returns the dataset's fixture results
(``tool_results``) for calls that validate against the supplied tool definitions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from app.evaluators.answer import contains, normalize
from app.evaluators.base import EvaluationOutcome, Evaluator, ModelOutput, ToolCallOutput
from app.evaluators.structured import json_equal, schema_violations

NO_TOOL_BEHAVIORS = ("no_tool", "ask_clarification", "refuse")


def values_equal(expected: Any, actual: Any) -> bool:
    """Argument value comparison: strings ignore case and surrounding whitespace; numbers
    compare numerically; everything else is JSON equality."""
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().casefold() == actual.strip().casefold()
    return json_equal(expected, actual)


@dataclass
class ToolAnalysis:
    behavior: str
    tools: dict[str, dict[str, Any]]
    expected: list[dict[str, Any]]
    calls: list[ToolCallOutput]
    known: list[ToolCallOutput] = field(default_factory=list)
    unknown: list[ToolCallOutput] = field(default_factory=list)
    malformed: list[ToolCallOutput] = field(default_factory=list)
    pairs: list[tuple[dict[str, Any], ToolCallOutput]] = field(default_factory=list)
    unmatched_expected: list[dict[str, Any]] = field(default_factory=list)
    unmatched_calls: list[ToolCallOutput] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, Any]:
        return {
            "expected_behavior": self.behavior,
            "expected_calls": len(self.expected),
            "calls": len(self.calls),
            "true_positive_calls": len(self.pairs),
            "false_positive_calls": len(self.unmatched_calls),
            "false_negative_calls": len(self.unmatched_expected),
            "unknown_tool_calls": len(self.unknown),
            "malformed_calls": len(self.malformed),
        }


def _matching_args(expected: dict[str, Any], call: ToolCallOutput) -> int:
    args = call.arguments or {}
    return sum(
        1
        for k, v in (expected.get("arguments") or {}).items()
        if k in args and values_equal(v, args[k])
    )


def analyze(case: dict[str, Any], output: ModelOutput) -> ToolAnalysis:
    tools = {
        (t.get("function") or {}).get("name"): (t.get("function") or {}).get("parameters") or {}
        for t in case.get("tools") or []
    }
    a = ToolAnalysis(
        behavior=case.get("expected_behavior") or "call_tool",
        tools=tools,
        expected=list(case.get("expected_tool_calls") or []),
        calls=list(output.tool_calls),
    )
    for call in a.calls:
        (a.known if call.name in tools else a.unknown).append(call)
        if call.arguments_error or call.arguments is None:
            a.malformed.append(call)
    unused = list(a.known)
    for exp in a.expected:
        candidates = [c for c in unused if c.name == exp.get("name")]
        if not candidates:
            a.unmatched_expected.append(exp)
            continue
        best = max(candidates, key=lambda c: _matching_args(exp, c))  # first wins ties
        unused.remove(best)
        a.pairs.append((exp, best))
    a.unmatched_calls = unused + a.unknown
    return a


def _call_repr(call: ToolCallOutput) -> dict[str, Any]:
    return {
        "name": call.name,
        "arguments": call.arguments if call.arguments is not None else call.arguments_raw[:300],
        **({"arguments_error": call.arguments_error} if call.arguments_error else {}),
    }


class _ToolEvaluator(Evaluator):
    suite = "tool_calling"
    case_types = frozenset({"tool_call"})


class ToolSelection(_ToolEvaluator):
    name = "tool_selection"
    title = "Tool selection"
    description = (
        "The model called exactly the expected tools (by name, any order), or no tool when the "
        "case expects no tool, a clarifying question, or a refusal."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        a = analyze(case, output)
        if a.behavior in NO_TOOL_BEHAVIORS:
            passed = not a.calls
            reason = (
                f"Expected {a.behavior.replace('_', ' ')}; no tool was called."
                if passed
                else f"Expected {a.behavior.replace('_', ' ')}, but "
                f"{len(a.calls)} tool call(s) were made."
            )
        else:
            expected_names = sorted(e.get("name") or "" for e in a.expected)
            called = sorted(c.name or "" for c in a.calls)
            passed = called == expected_names
            if not a.calls:
                reason = "No tool was called."
            elif passed:
                reason = "Called exactly the expected tools."
            else:
                reason = f"Expected {expected_names}, called {called}."
        return self.result(
            passed,
            label="correct"
            if passed
            else "missing_call"
            if a.behavior == "call_tool" and not a.calls
            else "unnecessary_call"
            if a.behavior in NO_TOOL_BEHAVIORS
            else "wrong_tools",
            reason=reason,
            evidence=[_call_repr(c) for c in a.calls[:10]],
            metadata=a.counts,
        )


class ToolArgumentNames(_ToolEvaluator):
    name = "tool_argument_names"
    title = "Argument names"
    description = (
        "For each correctly selected call: every expected argument is present and no argument "
        "name is outside the tool's declared parameters."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        a = analyze(case, output)
        if not a.pairs:
            return self.not_applicable(
                "No expected tool was called, so argument names can't be checked."
            )
        problems: list[dict[str, Any]] = []
        ok = 0
        for exp, call in a.pairs:
            args = call.arguments or {}
            declared = set((a.tools.get(call.name or "") or {}).get("properties") or {})
            unknown = sorted(k for k in args if declared and k not in declared)
            missing = sorted(k for k in (exp.get("arguments") or {}) if k not in args)
            if call.arguments is None:
                problems.append({"tool": call.name, "problem": "arguments are not a JSON object"})
            elif unknown or missing:
                problems.append({"tool": call.name, "unknown": unknown, "missing": missing})
            else:
                ok += 1
        return self.result(
            ok == len(a.pairs),
            score=round(ok / len(a.pairs), 6),
            reason=f"{ok} of {len(a.pairs)} selected call(s) use the right argument names.",
            evidence=problems,
            metadata={"calls_checked": len(a.pairs), "calls_ok": ok},
        )


class ToolArgumentSchema(_ToolEvaluator):
    name = "tool_argument_schema"
    title = "Argument schema validity"
    description = (
        "Every call to a supplied tool has JSON-object arguments that validate against that "
        "tool's parameter schema (types, enums, required fields)."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        a = analyze(case, output)
        if not a.known:
            return self.not_applicable("No supplied tool was called.")
        problems: list[dict[str, Any]] = []
        ok = 0
        for call in a.known:
            if call.arguments is None:
                problems.append(
                    {
                        "tool": call.name,
                        "problem": "malformed arguments: "
                        + (call.arguments_error or "not an object"),
                    }
                )
                continue
            violations = schema_violations(a.tools[call.name or ""], call.arguments)
            if violations:
                problems.append({"tool": call.name, "violations": violations})
            else:
                ok += 1
        return self.result(
            ok == len(a.known),
            score=round(ok / len(a.known), 6),
            reason=f"{ok} of {len(a.known)} call(s) have schema-valid arguments.",
            evidence=problems,
            metadata={
                "calls_checked": len(a.known),
                "calls_ok": ok,
                "malformed_calls": len(a.malformed),
            },
        )


class ToolRequiredArguments(_ToolEvaluator):
    name = "tool_required_arguments"
    title = "Required arguments"
    description = "Every call to a supplied tool includes all arguments the tool marks as required."

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        a = analyze(case, output)
        if not a.known:
            return self.not_applicable("No supplied tool was called.")
        problems: list[dict[str, Any]] = []
        ok = 0
        for call in a.known:
            required = list((a.tools.get(call.name or "") or {}).get("required") or [])
            missing = [k for k in required if k not in (call.arguments or {})]
            if missing:
                problems.append({"tool": call.name, "missing": missing})
            else:
                ok += 1
        return self.result(
            ok == len(a.known),
            score=round(ok / len(a.known), 6),
            reason=f"{ok} of {len(a.known)} call(s) include every required argument.",
            evidence=problems,
            metadata={"calls_checked": len(a.known), "calls_ok": ok},
        )


class ToolArgumentValues(_ToolEvaluator):
    name = "tool_argument_values"
    title = "Argument values"
    description = (
        "For each correctly selected call: each expected argument value matches (strings ignore "
        "case and surrounding whitespace; numbers compare numerically). Score = matched values / "
        "expected values."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        a = analyze(case, output)
        if not a.pairs:
            return self.not_applicable("No expected tool was called, so values can't be compared.")
        total = matched = 0
        problems: list[dict[str, Any]] = []
        for exp, call in a.pairs:
            args = call.arguments or {}
            for key, value in (exp.get("arguments") or {}).items():
                total += 1
                if key in args and values_equal(value, args[key]):
                    matched += 1
                else:
                    problems.append(
                        {
                            "tool": call.name,
                            "argument": key,
                            "expected": json.dumps(value)[:200],
                            "actual": json.dumps(args[key])[:200] if key in args else None,
                        }
                    )
        if total == 0:
            return self.not_applicable("The expected calls specify no argument values.")
        return self.result(
            matched == total,
            score=round(matched / total, 6),
            reason=f"{matched} of {total} expected argument value(s) match.",
            evidence=problems[:20],
            metadata={"values_expected": total, "values_matched": matched},
        )


class ToolSequence(_ToolEvaluator):
    name = "tool_sequence"
    title = "Call order"
    description = (
        "For cases that require an order (ordered_calls): the tools were called in the "
        "expected order."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        a = analyze(case, output)
        if not case.get("ordered_calls") or len(a.expected) < 2:
            return self.not_applicable("The case does not require a call order.")
        expected = [e.get("name") for e in a.expected]
        actual = [c.name for c in a.known]
        passed = actual == expected
        return self.result(
            passed,
            reason="Called in the expected order."
            if passed
            else f"Expected {expected}, got {actual}.",
            metadata={"expected_order": expected, "actual_order": actual},
        )


class ToolHallucination(_ToolEvaluator):
    name = "tool_hallucination"
    title = "No invented tools"
    description = "The model did not try to call a tool that was not supplied."

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        a = analyze(case, output)
        if not a.calls:
            return self.not_applicable("No tool was called.")
        names = sorted({c.name or "(no name)" for c in a.unknown})
        return self.result(
            not a.unknown,
            label="none" if not a.unknown else "invented_tool",
            reason="Every call used a supplied tool."
            if not a.unknown
            else f"Called unsupplied tool(s): {', '.join(names)}.",
            evidence=[_call_repr(c) for c in a.unknown[:10]],
            metadata={
                "calls": len(a.calls),
                "unknown_tool_calls": len(a.unknown),
                "unknown_names": names,
            },
        )


def final_text(case: dict[str, Any], output: ModelOutput) -> tuple[str | None, str]:
    """The answer the user would see: after simulated tool execution if it ran, else the
    direct answer. Returns (text, source)."""
    if output.simulation:
        return output.simulation.get("final_content"), "after_simulated_tools"
    return output.content, "direct"


class ToolFinalAnswer(_ToolEvaluator):
    name = "tool_final_answer"
    title = "Final answer"
    description = (
        "The final answer (after simulated tool results, when tools were called) contains the "
        "case's expected_final_answer (normalized, whole words)."
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        expected = case.get("expected_final_answer")
        if not expected:
            return self.not_applicable("The case has no expected_final_answer.")
        if output.tool_calls and not output.simulation:
            reason = (
                "The model called tools; judging the final answer needs simulated tool execution "
                "(tool_mode 'simulated' and tool_results fixtures)."
            )
            return self.not_applicable(reason)
        text, source = final_text(case, output)
        if output.simulation and not output.simulation.get("completed"):
            if output.simulation.get("error_kind") == "request_failed":
                # A transport/server failure, not the model's fault: no verdict.
                return self.outcome("failed", reason=output.simulation.get("stop_reason"))
            return self.result(
                False,
                label="incomplete",
                reason=output.simulation.get("stop_reason") or "Simulation did not finish.",
                metadata={"source": source},
            )
        passed = bool(text) and contains(text or "", expected)
        return self.result(
            passed,
            reason="The final answer contains the expected answer."
            if passed
            else "The expected answer is missing from the final answer.",
            evidence=[{"expected": expected[:300], "final_answer": (text or "")[:300]}],
            metadata={"source": source, "expected_normalized": normalize(expected)[:200]},
        )


class ToolTaskSuccess(_ToolEvaluator):
    name = "tool_task_success"
    kind = "composite"
    title = "Task success"
    description = (
        "End to end: right tool selection, and wherever they apply, schema-valid arguments, "
        "matching argument values, call order, and final answer. Shown next to its parts, never "
        "instead of them."
    )
    parts = (
        "tool_selection",
        "tool_argument_schema",
        "tool_argument_values",
        "tool_sequence",
        "tool_final_answer",
    )

    async def evaluate(self, case, output, context):  # type: ignore[no-untyped-def]
        considered = {n: context.previous[n] for n in self.parts if n in context.previous}
        if "tool_selection" not in considered:
            return self.outcome("skipped", reason="Tool selection was not evaluated.")
        applicable = {n: o for n, o in considered.items() if o.status == "completed"}
        failed = [n for n, o in applicable.items() if not o.passed]
        return self.result(
            not failed,
            reason="Every applicable check passed."
            if not failed
            else f"Failed: {', '.join(failed)}.",
            metadata={"checks": sorted(applicable), "failed_checks": failed},
        )


def tool_evaluators() -> list[Evaluator]:
    return [
        ToolSelection(),
        ToolArgumentNames(),
        ToolArgumentSchema(),
        ToolRequiredArguments(),
        ToolArgumentValues(),
        ToolSequence(),
        ToolHallucination(),
        ToolFinalAnswer(),
        ToolTaskSuccess(),
    ]


# ------------------------------------------------------------------- simulated execution


def simulate_tool_result(case: dict[str, Any], call: ToolCallOutput) -> dict[str, Any]:
    """The fixture result for one tool call, or an error message for the model.

    Order: unknown tool → error; malformed or schema-invalid arguments → error; a fixture whose
    ``arguments`` equal the call's (by :func:`values_equal`) → its result; otherwise a fixture
    for the tool with ``arguments: null`` (matches any call) → its result; otherwise an error.
    """
    tools = {
        (t.get("function") or {}).get("name"): (t.get("function") or {}).get("parameters") or {}
        for t in case.get("tools") or []
    }
    if call.name not in tools:
        return {"ok": False, "fixture": False, "result": {"error": f"Unknown tool '{call.name}'."}}
    if call.arguments is None:
        return {
            "ok": False,
            "fixture": False,
            "result": {
                "error": (
                    "Arguments are not a valid JSON object: " + (call.arguments_error or "")
                ).strip()
            },
        }
    errors = sorted(Draft202012Validator(tools[call.name]).iter_errors(call.arguments), key=str)
    if errors:
        return {
            "ok": False,
            "fixture": False,
            "result": {"error": f"Invalid arguments: {errors[0].message[:200]}"},
        }
    fixtures = [f for f in case.get("tool_results") or [] if f.get("name") == call.name]
    for f in fixtures:
        expected = f.get("arguments")
        if (
            expected is not None
            and expected.keys() == call.arguments.keys()
            and all(values_equal(v, call.arguments[k]) for k, v in expected.items())
        ):
            return {"ok": True, "fixture": True, "result": f.get("result")}
    for f in fixtures:
        if f.get("arguments") is None:
            return {"ok": True, "fixture": True, "result": f.get("result")}
    return {
        "ok": False,
        "fixture": False,
        "result": {"error": "No simulated result is available for these arguments."},
    }


def needs_simulation(case: dict[str, Any], output: ModelOutput) -> bool:
    return (
        case.get("type") == "tool_call"
        and bool(output.tool_calls)
        and bool(case.get("tool_results"))
    )


__all__ = ["EvaluationOutcome", "analyze", "simulate_tool_result", "tool_evaluators"]
