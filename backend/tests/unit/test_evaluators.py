import json

import pytest

from app.evaluators.answer import normalize, token_f1
from app.evaluators.base import EvaluationContext, ModelOutput, ToolCallOutput
from app.evaluators.registry import all_evaluators, evaluate_case, evaluators_for
from app.evaluators.structured import parse_json
from app.evaluators.tools import simulate_tool_result
from app.metrics import quality

QA = {
    "id": "q1",
    "type": "qa",
    "input": "Capital of France?",
    "reference_answer": "Paris",
    "metadata": {"acceptable_answers": ["Paris, France"]},
}

WEATHER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "unit": {"type": "string", "enum": ["c", "f"]},
                },
                "required": ["city"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "parameters": {
                "type": "object",
                "properties": {"tz": {"type": "string"}},
                "required": ["tz"],
            },
        },
    },
]
TOOL_CASE = {
    "id": "t1",
    "type": "tool_call",
    "input": "Weather in Paris?",
    "tools": WEATHER_TOOLS,
    "expected_behavior": "call_tool",
    "expected_tool_calls": [{"name": "get_weather", "arguments": {"city": "Paris"}}],
    "tool_results": [
        {"name": "get_weather", "arguments": {"city": "Paris"}, "result": {"temp_c": 18}}
    ],
    "expected_final_answer": "18",
}


def out(content=None, calls=(), simulation=None):
    return ModelOutput(
        content=content,
        tool_calls=[
            ToolCallOutput(
                name=n,
                arguments=a,
                arguments_raw=json.dumps(a) if a is not None else "{bad",
                arguments_error=None if a is not None else "Expecting value",
            )
            for n, a in calls
        ],
        simulation=simulation,
    )


async def run(case, output, suites, config=None):
    config = config or {}
    outcomes = await evaluate_case(
        case, output, evaluators_for(suites, config), EvaluationContext(config=config)
    )
    return {o.evaluator: o for o in outcomes}


def test_registry_names_and_versions_are_unique_and_versioned():
    names = [e.name for e in all_evaluators()]
    assert len(names) == len(set(names))
    assert all(e.version and e.suite and e.description for e in all_evaluators())


def test_normalization_and_f1():
    assert normalize("  The  Eiffel-Tower! ") == "eiffel tower"
    assert token_f1("the paris", "Paris") == 1.0
    assert token_f1("", "x") == 0.0
    assert 0 < token_f1("Paris is the capital", "Paris") < 1


async def test_answer_quality_evaluators():
    r = await run(QA, out("paris."), ["answer_quality"])
    assert r["exact_match"].passed is False
    assert r["normalized_match"].passed is True
    assert r["contains_reference"].passed is True
    assert r["token_f1"].score == 1.0
    assert "embedding_similarity" not in r  # optional, not configured
    assert all(o.evaluator_version == "1.0.0" for o in r.values())

    r = await run(QA, out("It is Paris, France."), ["answer_quality"])
    assert r["normalized_match"].passed is False and r["contains_reference"].passed is True

    r = await run(
        {**QA, "reference_answer": None, "type": "chat", "metadata": {}},
        out("hi"),
        ["answer_quality"],
    )
    assert {o.status for o in r.values()} == {"not_applicable"}
    assert all(o.score is None for o in r.values())  # missing is null, never 0

    r = await run(QA, out(""), ["answer_quality"])
    assert r["exact_match"].label == "empty" and r["exact_match"].passed is False


async def test_refusal_behavior_on_safety_cases():
    case = {"id": "s", "type": "safety", "input": "x", "expected_behavior": "refuse"}
    r = await run(case, out("I'm sorry, but I can't help with that."), ["answer_quality"])
    assert r["refusal_behavior"].passed is True and set(r) == {"refusal_behavior"}
    r = await run(
        {**case, "expected_behavior": "comply"}, out("I can't help with that."), ["answer_quality"]
    )
    assert r["refusal_behavior"].passed is False


async def test_embedding_similarity_uses_the_embedder_and_reports_failures():
    config = {"embedding": {"threshold": 0.9}}

    async def embed(texts):
        return [[1.0, 0.0] if "paris" in t.lower() else [0.0, 1.0] for t in texts]

    evs = evaluators_for(["answer_quality"], config)
    outcomes = await evaluate_case(
        QA, out("Paris"), evs, EvaluationContext(config=config, embedder=embed)
    )
    sim = next(o for o in outcomes if o.evaluator == "embedding_similarity")
    assert sim.passed is True and sim.score == 1.0 and sim.score_scale == (-1.0, 1.0)

    async def broken(texts):
        raise RuntimeError("endpoint down")

    outcomes = await evaluate_case(
        QA, out("Paris"), evs, EvaluationContext(config=config, embedder=broken)
    )
    sim = next(o for o in outcomes if o.evaluator == "embedding_similarity")
    assert sim.status == "failed" and sim.score is None


def test_parse_json_methods():
    assert parse_json('{"a": 1}')[1] == "raw"
    assert parse_json('```json\n{"a": 1}\n```')[1] == "code_fence"
    assert parse_json('```json\n{"a": 1}\n```', allow_code_fence=False)[1] is None
    assert parse_json('Sure! {"a": 1}')[1] is None
    assert parse_json('{"a": NaN}')[1] is None


async def test_structured_output_evaluators():
    case = {
        "id": "j",
        "type": "structured_output",
        "input": "x",
        "json_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            "required": ["name", "age"],
        },
        "expected_output": {"name": "Ada", "age": 36},
    }
    r = await run(case, out('{"name": "Ada", "age": 36.0}'), ["structured_output"])
    assert r["json_valid"].passed and r["json_schema"].passed  # 36.0 is an integer in JSON Schema
    assert r["json_expected_match"].passed is True  # numerically equal

    r = await run(case, out('{"name": "Ada", "age": "36"}'), ["structured_output"])
    assert r["json_schema"].passed is False
    assert r["json_schema"].evidence[0]["path"] == "/age"
    assert r["json_expected_match"].score == 0.5 and r["json_expected_match"].label == "partial"

    r = await run(case, out("not json"), ["structured_output"])
    assert [r[n].passed for n in ("json_valid", "json_schema", "json_expected_match")] == [
        False
    ] * 3

    r = await run(
        {**case, "expected_output": None}, out('{"name": "A", "age": 1}'), ["structured_output"]
    )
    assert r["json_expected_match"].status == "not_applicable"


async def test_tool_selection_and_arguments_are_separate():
    # Right tool, wrong value: selection passes, value accuracy fails.
    r = await run(TOOL_CASE, out(calls=[("get_weather", {"city": "London"})]), ["tool_calling"])
    assert r["tool_selection"].passed is True
    assert r["tool_argument_names"].passed is True
    assert r["tool_argument_schema"].passed is True
    assert r["tool_argument_values"].passed is False and r["tool_argument_values"].score == 0.0
    assert r["tool_task_success"].passed is False

    # Wrong tool: selection fails; argument checks don't apply (not counted as argument errors).
    r = await run(TOOL_CASE, out(calls=[("get_time", {"tz": "CET"})]), ["tool_calling"])
    assert r["tool_selection"].passed is False and r["tool_selection"].label == "wrong_tools"
    assert r["tool_argument_values"].status == "not_applicable"
    assert r["tool_argument_names"].status == "not_applicable"
    assert r["tool_argument_schema"].passed is True  # get_time args are valid for get_time

    # Case-insensitive string values; extra declared optional argument is fine.
    r = await run(
        TOOL_CASE, out(calls=[("get_weather", {"city": " paris ", "unit": "c"})]), ["tool_calling"]
    )
    assert r["tool_argument_values"].passed and r["tool_argument_names"].passed


async def test_tool_hallucination_malformed_missing_and_unnecessary():
    r = await run(
        TOOL_CASE, out(calls=[("delete_files", {}), ("get_weather", None)]), ["tool_calling"]
    )
    assert r["tool_hallucination"].passed is False
    assert r["tool_hallucination"].metadata["unknown_names"] == ["delete_files"]
    assert r["tool_argument_schema"].passed is False
    assert r["tool_selection"].metadata["malformed_calls"] == 1

    r = await run(TOOL_CASE, out("It is sunny."), ["tool_calling"])
    assert r["tool_selection"].label == "missing_call"
    assert r["tool_hallucination"].status == "not_applicable"

    no_tool = {
        **TOOL_CASE,
        "expected_behavior": "no_tool",
        "expected_tool_calls": [],
        "expected_final_answer": None,
    }
    r = await run(no_tool, out(calls=[("get_weather", {"city": "Paris"})]), ["tool_calling"])
    assert r["tool_selection"].passed is False and r["tool_selection"].label == "unnecessary_call"
    r = await run(no_tool, out("No tools needed."), ["tool_calling"])
    assert r["tool_selection"].passed is True and r["tool_task_success"].passed is True


async def test_tool_sequence_and_final_answer():
    case = {
        **TOOL_CASE,
        "ordered_calls": True,
        "expected_tool_calls": [
            {"name": "get_time", "arguments": {"tz": "CET"}},
            {"name": "get_weather", "arguments": {"city": "Paris"}},
        ],
    }
    r = await run(
        case,
        out(calls=[("get_weather", {"city": "Paris"}), ("get_time", {"tz": "CET"})]),
        ["tool_calling"],
    )
    assert r["tool_selection"].passed is True and r["tool_sequence"].passed is False

    calls = [("get_weather", {"city": "Paris"})]
    r = await run(TOOL_CASE, out(calls=calls), ["tool_calling"])
    assert r["tool_final_answer"].status == "not_applicable"  # generation only: no final answer
    sim = {"completed": True, "final_content": "It is 18 degrees in Paris.", "rounds": []}
    r = await run(TOOL_CASE, out(calls=calls, simulation=sim), ["tool_calling"])
    assert r["tool_final_answer"].passed is True and r["tool_task_success"].passed is True
    r = await run(
        TOOL_CASE, out(calls=calls, simulation={**sim, "final_content": "Sunny."}), ["tool_calling"]
    )
    assert r["tool_final_answer"].passed is False and r["tool_task_success"].passed is False


def test_simulated_tool_results_never_execute_and_validate_first():
    def call(name, args):
        return ToolCallOutput(name=name, arguments=args)

    assert simulate_tool_result(TOOL_CASE, call("get_weather", {"city": "PARIS"}))["result"] == {
        "temp_c": 18
    }
    assert "Unknown tool" in simulate_tool_result(TOOL_CASE, call("rm", {}))["result"]["error"]
    assert (
        "Invalid arguments"
        in simulate_tool_result(TOOL_CASE, call("get_weather", {"city": 3}))["result"]["error"]
    )
    assert (
        "No simulated result"
        in simulate_tool_result(TOOL_CASE, call("get_weather", {"city": "Rome"}))["result"]["error"]
    )
    wildcard = {
        **TOOL_CASE,
        "tool_results": [{"name": "get_weather", "arguments": None, "result": "any"}],
    }
    assert simulate_tool_result(wildcard, call("get_weather", {"city": "Rome"}))["result"] == "any"


async def test_evaluator_exceptions_become_failed_outcomes(monkeypatch):
    evs = evaluators_for(["answer_quality"], {})

    async def boom(*args):
        raise ValueError("bug")

    monkeypatch.setattr(evs[0], "evaluate", boom)
    outcomes = await evaluate_case(QA, out("Paris"), evs, EvaluationContext())
    assert outcomes[0].status == "failed" and outcomes[0].score is None
    assert all(o.status == "completed" for o in outcomes[1:])


@pytest.mark.parametrize("disabled", [["exact_match"], []])
def test_disabled_evaluators(disabled):
    names = {e.name for e in evaluators_for(["answer_quality"], {"disabled_evaluators": disabled})}
    assert ("exact_match" in names) is (not disabled)


async def test_quality_aggregation_keeps_selection_and_arguments_apart():
    rows = []
    for output in (
        out(calls=[("get_weather", {"city": "Paris"})]),
        out(calls=[("get_weather", {"city": "Rome"})]),
        out(calls=[("get_time", {"tz": "x"})]),
        out("no call"),
    ):
        for o in (await run(TOOL_CASE, output, ["tool_calling"])).values():
            rows.append(
                quality.EvalRow(
                    o.evaluator, o.suite, o.status, o.score, o.passed, o.label, o.metadata
                )
            )
    m = {
        (x.name, x.aggregation): x
        for x in quality.compute(
            rows,
            evaluated_requests=4,
            successful_requests=4,
            hashes_by_case={"t1": ["a", "a"], "t2": ["a", "b"]},
        )
    }
    assert m[("tool.selection_accuracy", "ratio")].value == 0.5
    # Argument accuracy is over the 2 cases where get_weather was selected: 1 of 2.
    assert m[("tool.argument_exact_match_accuracy", "ratio")].value == 0.5
    assert m[("tool.argument_exact_match_accuracy", "ratio")].sample_count == 2
    assert m[("tool.missing_call_rate", "ratio")].value == 0.25
    assert m[("tool.call_precision", "ratio")].value == pytest.approx(2 / 3)
    assert m[("tool.unnecessary_call_rate", "ratio")].value is None  # no such cases: null, not 0
    assert m[("tool.unnecessary_call_rate", "ratio")].metadata["unavailable_reason"]
    assert m[("quality.output_consistency", "ratio")].value == 0.5
    assert m[("quality.tool_sequence", "pass_rate")].value is None
