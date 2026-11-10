"""Quality and tool-call evaluation end to end (milestone 4 acceptance).

The mock model server is scripted, so every expected metric below can be worked out by hand.
"""

from __future__ import annotations

import pytest

from app.testing.mock_openai import create_mock_app
from tests.conftest import MOCK_API_KEY, ServerThread
from tests.integration.test_runs_api import create_run, execute, jsonl, make_endpoint, metrics_of

SCRIPT = [
    # answer quality
    {"match": "capital of France", "response": "Paris"},
    {"match": "largest planet", "response": "It is Jupiter."},
    {"match": "boiling point", "response": "50 degrees"},
    # structured output
    {"match": "user Ada", "response": '{"name": "Ada", "age": 36}'},
    {"match": "user Bob", "response": 'Here you go: {"name": "Bob"}'},
    # tool calling
    {
        "match": "Weather in Paris",
        "tool_calls": [{"name": "get_weather", "arguments": {"city": "Paris"}}],
        "after_tools": "It is 18 degrees in Paris.",
    },
    {
        "match": "Weather in Rome",
        "tool_calls": [{"name": "get_weather", "arguments": {"city": "Milan"}}],
        "after_tools": "Sorry, I could not find the weather.",
    },
    {
        "match": "Time in Tokyo",
        "tool_calls": [{"name": "get_weather", "arguments": {"city": "Tokyo"}}],
        "after_tools": "Done.",
    },
    {"match": "Tell me a joke", "response": "Why did the router cross the road?"},
    {"match": "Delete my files", "tool_calls": [{"name": "delete_files", "arguments": {}}]},
]

QA = [
    {
        "id": "qa-france",
        "type": "qa",
        "input": "What is the capital of France?",
        "reference_answer": "Paris",
    },
    {
        "id": "qa-planet",
        "type": "qa",
        "input": "What is the largest planet?",
        "reference_answer": "Jupiter",
    },
    {
        "id": "qa-boil",
        "type": "qa",
        "input": "What is the boiling point of water?",
        "reference_answer": "100 degrees Celsius",
    },
]
SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name", "age"],
}
STRUCTURED = [
    {
        "id": "so-ada",
        "type": "structured_output",
        "input": "Return the user Ada as JSON.",
        "json_schema": SCHEMA,
        "expected_output": {"name": "Ada", "age": 36},
    },
    {
        "id": "so-bob",
        "type": "structured_output",
        "input": "Return the user Bob as JSON.",
        "json_schema": SCHEMA,
        "expected_output": {"name": "Bob", "age": 40},
    },
]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
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
TOOL_CASES = [
    {
        "id": "t-paris",
        "type": "tool_call",
        "input": "Weather in Paris?",
        "tools": TOOLS,
        "expected_tool_calls": [{"name": "get_weather", "arguments": {"city": "Paris"}}],
        "tool_results": [
            {"name": "get_weather", "arguments": {"city": "Paris"}, "result": {"temp_c": 18}}
        ],
        "expected_final_answer": "18 degrees",
    },
    {
        "id": "t-rome",
        "type": "tool_call",
        "input": "Weather in Rome?",
        "tools": TOOLS,
        "expected_tool_calls": [{"name": "get_weather", "arguments": {"city": "Rome"}}],
        "tool_results": [
            {"name": "get_weather", "arguments": {"city": "Rome"}, "result": {"temp_c": 21}}
        ],
        "expected_final_answer": "21 degrees",
    },
    {
        "id": "t-tokyo",
        "type": "tool_call",
        "input": "Time in Tokyo?",
        "tools": TOOLS,
        "expected_tool_calls": [{"name": "get_time", "arguments": {"tz": "Asia/Tokyo"}}],
    },
    {
        "id": "t-joke",
        "type": "tool_call",
        "input": "Tell me a joke",
        "tools": TOOLS,
        "expected_behavior": "no_tool",
    },
    {
        "id": "t-delete",
        "type": "tool_call",
        "input": "Delete my files",
        "tools": TOOLS,
        "expected_behavior": "refuse",
    },
]


@pytest.fixture(autouse=True)
def fast_progress(settings):
    settings.run_progress_interval_seconds = 0.05


@pytest.fixture(scope="module")
def scripted_server():
    with ServerThread(
        create_mock_app(models=["mock-model"], api_key=MOCK_API_KEY, script=SCRIPT)
    ) as server:
        yield server


async def make_ds(api, name, dataset_type, records):
    resp = await api.post(
        "/api/v1/datasets",
        json={
            "name": name,
            "dataset_type": dataset_type,
            "content": jsonl(records),
            "filename": "d.jsonl",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def quality_setup(api, server):
    ep = await make_endpoint(
        api, server, headers={"X-Mock-First-Token-Delay-Ms": "0", "X-Mock-Chunk-Delay-Ms": "0"}
    )
    datasets = [
        await make_ds(api, "qa", "qa", QA),
        await make_ds(api, "structured", "structured_output", STRUCTURED),
        await make_ds(api, "tools", "tool_call", TOOL_CASES),
    ]
    return ep, datasets


QUALITY_BODY = {
    "suites": ["performance", "answer_quality", "structured_output", "tool_calling"],
    "load": {"profile": "sequential", "warmup_requests": 1},
}


async def all_results(api, run_id, **params):
    resp = await api.get(
        f"/api/v1/runs/{run_id}/results", params={"limit": 500, "phase": "measured", **params}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def verdicts(results):
    return {
        (r["case_identifier"], e["evaluator_name"]): (
            e["status"],
            e["score"],
            e["passed"],
            e["label"],
        )
        for r in results["items"]
        for e in r["evaluations"]
    }


async def test_quality_suite_end_to_end(api, migrated_app, scripted_server):
    ep, datasets = await quality_setup(api, scripted_server)
    run = await create_run(api, [ep], datasets, **QUALITY_BODY)
    snap = run["configuration_snapshot"]["evaluation"]
    assert snap["suites"] == ["answer_quality", "structured_output", "tool_calling"]
    assert snap["tool_execution"].startswith("simulated")
    assert {
        "name": "tool_selection",
        "version": "1.0.0",
        "suite": "tool_calling",
        "kind": "objective",
    } in snap["evaluators"]

    assert await execute(migrated_app, run["id"]) == "completed"
    results = await all_results(api, run["id"])
    assert results["total"] == 10
    v = verdicts(results)

    # Answer quality: exact vs normalized vs containment are distinct.
    assert v[("qa-france", "exact_match")][2] is True
    assert v[("qa-planet", "normalized_match")][2] is False
    assert v[("qa-planet", "contains_reference")][2] is True
    assert v[("qa-boil", "contains_reference")][2] is False
    assert 0 < v[("qa-boil", "token_f1")][1] < 0.5
    # Answer evaluators don't run on other case types (no rows), warm-up is never evaluated.
    assert ("so-ada", "exact_match") not in v
    warmup = await all_results(api, run["id"], phase="warmup")
    assert all(not r["evaluations"] for r in warmup["items"])

    # Structured output.
    assert v[("so-ada", "json_expected_match")][:3] == ("completed", 1.0, True)
    assert (
        v[("so-bob", "json_valid")][2] is False
        and v[("so-bob", "json_schema")][3] == "invalid_json"
    )

    # Tool calling: selection and argument correctness are separate.
    assert v[("t-rome", "tool_selection")][2] is True
    assert v[("t-rome", "tool_argument_values")][2] is False
    assert v[("t-tokyo", "tool_selection")][2] is False
    assert v[("t-tokyo", "tool_argument_values")][0] == "not_applicable"
    assert v[("t-delete", "tool_hallucination")][2] is False
    assert v[("t-paris", "tool_final_answer")][2] is True
    assert v[("t-paris", "tool_task_success")][2] is True

    _, m = await metrics_of(api, run["id"])
    assert m["tool.selection_accuracy"]["ratio"]["metric_value"] == pytest.approx(3 / 5)
    assert m["tool.argument_exact_match_accuracy"]["ratio"]["metric_value"] == pytest.approx(1 / 2)
    assert m["tool.argument_exact_match_accuracy"]["ratio"]["sample_count"] == 2
    assert m["tool.hallucinated_tool_rate"]["ratio"]["metric_value"] == pytest.approx(1 / 4)
    assert m["tool.unnecessary_call_rate"]["ratio"]["metric_value"] == pytest.approx(1 / 2)
    assert m["tool.missing_call_rate"]["ratio"]["metric_value"] == 0.0
    assert m["tool.final_answer_correctness"]["ratio"]["metric_value"] == pytest.approx(1 / 2)
    assert m["quality.exact_match"]["pass_rate"]["metric_value"] == pytest.approx(1 / 3)
    assert m["quality.contains_reference"]["pass_rate"]["metric_value"] == pytest.approx(2 / 3)
    assert m["quality.json_valid"]["pass_rate"]["metric_value"] == pytest.approx(1 / 2)
    assert m["quality.tool_sequence"]["pass_rate"]["metric_value"] is None  # no ordered cases
    assert m["quality.evaluated_requests"]["count"]["metric_value"] == 10
    # Performance metrics are still there.
    assert m["latency_ms"]["p50"]["metric_value"] is not None

    # Simulated tool execution: fixture results only, transcript stored, labeled as simulated.
    paris = next(r for r in results["items"] if r["case_identifier"] == "t-paris")
    detail = (await api.get(f"/api/v1/runs/{run['id']}/results/{paris['id']}")).json()
    sim = detail["response_data"]["simulation"]
    assert sim["executed"] is False and sim["mode"] == "simulated" and sim["completed"] is True
    assert sim["rounds"][0]["tool_results"][0]["fixture_matched"] is True
    assert '"temp_c": 18' in sim["rounds"][0]["tool_results"][0]["result"]
    assert sim["final_content"] == "It is 18 degrees in Paris."
    final = next(e for e in detail["evaluations"] if e["evaluator_name"] == "tool_final_answer")
    assert final["evaluator_version"] == "1.0.0" and final["rationale"]
    rome = next(r for r in results["items"] if r["case_identifier"] == "t-rome")
    rome_sim = (await api.get(f"/api/v1/runs/{run['id']}/results/{rome['id']}")).json()[
        "response_data"
    ]["simulation"]
    assert rome_sim["rounds"][0]["tool_results"][0]["fixture_matched"] is False
    assert "No simulated result" in rome_sim["rounds"][0]["tool_results"][0]["result"]

    # Find failures for one evaluator.
    failed = await all_results(api, run["id"], evaluator="tool_selection", evaluation="failed")
    assert sorted(r["case_identifier"] for r in failed["items"]) == ["t-delete", "t-tokyo"]


async def test_quality_results_are_repeatable(api, migrated_app, scripted_server):
    ep, datasets = await quality_setup(api, scripted_server)
    outcomes = []
    for name in ("first", "second"):
        run = await create_run(api, [ep], datasets, name=name, **QUALITY_BODY)
        assert await execute(migrated_app, run["id"]) == "completed"
        _, m = await metrics_of(api, run["id"])
        quality = {
            (k, agg): x["metric_value"]
            for k, aggs in m.items()
            if k.startswith(("quality.", "tool."))
            for agg, x in aggs.items()
        }
        outcomes.append((verdicts(await all_results(api, run["id"])), quality))
    assert outcomes[0] == outcomes[1]


async def test_generation_only_mode_sends_no_follow_ups(api, migrated_app, scripted_server):
    ep = await make_endpoint(api, scripted_server)
    ds = await make_ds(api, "tools", "tool_call", TOOL_CASES[:2])
    run = await create_run(
        api,
        [ep],
        [ds],
        suites=["tool_calling"],
        evaluation={"tool_mode": "generation_only"},
        load={"profile": "sequential"},
    )
    assert run["configuration_snapshot"]["evaluation"]["tool_execution"].startswith(
        "generation only"
    )
    assert await execute(migrated_app, run["id"]) == "completed"
    results = await all_results(api, run["id"])
    assert all("simulation" not in r["response_data"] for r in results["items"])
    v = verdicts(results)
    assert v[("t-paris", "tool_final_answer")][0] == "not_applicable"
    assert v[("t-paris", "tool_argument_values")][2] is True


async def test_failed_requests_are_not_evaluated(api, migrated_app, scripted_server):
    ep = await make_endpoint(
        api, scripted_server, headers={"X-Mock-Fail-If-Contains": "largest planet"}, max_retries=0
    )
    ds = await make_ds(api, "qa", "qa", QA)
    run = await create_run(
        api, [ep], [ds], suites=["answer_quality"], load={"profile": "sequential"}
    )
    assert await execute(migrated_app, run["id"]) == "completed_with_errors"
    _, m = await metrics_of(api, run["id"])
    assert m["quality.evaluated_requests"]["count"]["metric_value"] == 2
    assert m["quality.exact_match"]["pass_rate"]["sample_count"] == 2  # the failure is not a "fail"


async def test_embedding_similarity_through_an_embedding_endpoint(
    api, migrated_app, scripted_server
):
    ep = await make_endpoint(api, scripted_server)
    ds = await make_ds(api, "qa", "qa", QA)
    run = await create_run(
        api,
        [ep],
        [ds],
        suites=["answer_quality"],
        load={"profile": "sequential"},
        evaluation={"embedding": {"endpoint_id": ep["id"], "threshold": 0.9}},
    )
    emb_snap = run["configuration_snapshot"]["evaluation"]["embedding_endpoint"]
    assert emb_snap["name"] == "mock-ep" and "api_key" not in str(emb_snap).lower().replace(
        "secret_reference", ""
    )
    assert await execute(migrated_app, run["id"]) == "completed"
    v = verdicts(await all_results(api, run["id"]))
    assert v[("qa-france", "embedding_similarity")][:3] == ("completed", 1.0, True)
    assert v[("qa-boil", "embedding_similarity")][2] is False


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"suites": ["tool_calling"]}, "tool_call"),
        ({"suites": ["quantization"]}, "compared across runs"),
        ({"suites": ["answer_quality"], "evaluation": {"tool_mode": "integration"}}, "mode C"),
        (
            {"suites": ["answer_quality"], "evaluation": {"disabled_evaluators": ["nope"]}},
            "Unknown evaluator",
        ),
    ],
)
async def test_quality_run_validation(api, scripted_server, body, message):
    ep = await make_endpoint(api, scripted_server)
    ds = await make_ds(api, "qa", "qa", QA)
    resp = await api.post(
        "/api/v1/runs",
        json={
            "name": "x",
            "endpoint_ids": [ep["id"]],
            "datasets": [{"dataset_id": ds["id"]}],
            **body,
        },
    )
    assert resp.status_code == 422
    assert message in resp.text


async def test_evaluators_endpoint(api):
    items = (await api.get("/api/v1/evaluators")).json()
    names = {e["name"]: e for e in items}
    assert names["tool_selection"]["suite"] == "tool_calling" and names["tool_selection"]["version"]
    assert names["embedding_similarity"]["optional"] is True


async def test_profiles_crud_and_runs_from_a_profile(api, migrated_app, scripted_server):
    body = {
        "name": "QA sequential",
        "suites": ["answer_quality"],
        "evaluation": {"token_f1_threshold": 0.3},
        "generation": {"temperature": 0, "max_tokens": 64},
        "load": {"profile": "sequential"},
    }
    resp = await api.post("/api/v1/profiles", json=body)
    assert resp.status_code == 201, resp.text
    profile = resp.json()
    assert profile["evaluation"]["token_f1_threshold"] == 0.3
    assert (await api.post("/api/v1/profiles", json=body)).status_code == 409
    bad = await api.post("/api/v1/profiles", json={**body, "name": "x", "suites": ["quantization"]})
    assert bad.status_code == 422
    resp = await api.patch(f"/api/v1/profiles/{profile['id']}", json={"description": "For QA sets"})
    assert resp.json()["description"] == "For QA sets"
    assert len((await api.get("/api/v1/profiles")).json()["items"]) == 1

    ep = await make_endpoint(api, scripted_server)
    ds = await make_ds(api, "qa", "qa", QA)
    run = await create_run(api, [ep], [ds], profile_id=profile["id"])
    snap = run["configuration_snapshot"]
    assert snap["suites"] == ["answer_quality"] and snap["generation"]["max_tokens"] == 64
    assert snap["load"]["profile"] == "sequential"
    assert snap["profile"] == {"id": profile["id"], "name": "QA sequential"}
    assert snap["evaluation"]["config"]["token_f1_threshold"] == 0.3
    # Explicit fields win over the profile.
    run2 = await create_run(
        api,
        [ep],
        [ds],
        profile_id=profile["id"],
        load={"profile": "fixed_concurrency", "concurrency": 2},
    )
    assert run2["configuration_snapshot"]["load"]["profile"] == "fixed_concurrency"
    assert run2["configuration_snapshot"]["suites"] == ["answer_quality"]

    assert (await api.delete(f"/api/v1/profiles/{profile['id']}")).status_code == 204
    kept = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    assert kept["configuration_snapshot"]["profile"]["name"] == "QA sequential"
    missing = await api.post(
        "/api/v1/runs",
        json={
            "name": "x",
            "endpoint_ids": [ep["id"]],
            "datasets": [{"dataset_id": ds["id"]}],
            "profile_id": profile["id"],
        },
    )
    assert missing.status_code == 422
