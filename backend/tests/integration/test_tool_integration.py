"""Integration-mode tool execution (spec §5.7 mode C) end to end, milestone 9 acceptance.

A real tool runner (ASGI app on a local port) executes the example script tools from
``tools/``; the scripted mock model calls them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.testing.mock_openai import create_mock_app
from app.toolrunner.server import create_app as create_runner
from tests.conftest import MOCK_API_KEY, ServerThread
from tests.integration.test_quality_runs import all_results, make_ds, verdicts
from tests.integration.test_runs_api import create_run, execute, make_endpoint

REPO_TOOLS = Path(__file__).resolve().parents[3] / "tools"
RUNNER_TOKEN = "test-tool-runner-token-0123456789"

SCRIPT = [
    {
        "match": "6 times 7",
        "tool_calls": [{"name": "calculator", "arguments": {"expression": "6*7"}}],
    },
    {
        "match": "order A-1001",
        "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "A-1001"}}],
    },
    {
        "match": "divide by zero",
        "tool_calls": [{"name": "calculator", "arguments": {"expression": "1/0"}}],
    },
]

CALC = {
    "type": "function",
    "function": {
        "name": "calculator",
        "description": "Arithmetic",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
}
ORDER = {
    "type": "function",
    "function": {
        "name": "lookup_order",
        "description": "Order lookup",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
}
CASES = [
    {
        "id": "calc",
        "type": "tool_call",
        "input": "What is 6 times 7?",
        "tools": [CALC],
        "expected_tool_calls": [{"name": "calculator", "arguments": {"expression": "6*7"}}],
        "expected_final_answer": "42",
    },
    {
        "id": "order",
        "type": "tool_call",
        "input": "Where is order A-1001?",
        "tools": [ORDER],
        "expected_tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "A-1001"}}],
        "expected_final_answer": "shipped",
    },
    {
        "id": "zero",
        "type": "tool_call",
        "input": "Please divide by zero",
        "tools": [CALC],
        "expected_tool_calls": [{"name": "calculator", "arguments": {"expression": "1/0"}}],
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


@pytest.fixture
def tools_dir(tmp_path):
    target = tmp_path / "tools"
    shutil.copytree(REPO_TOOLS, target)
    shutil.copy(target / "tools.example.yaml", target / "tools.yaml")
    return target


@pytest.fixture
def runner(settings, tools_dir):
    settings.tool_runner_token = SecretStr(RUNNER_TOKEN)
    with ServerThread(create_runner(settings, tools_dir=tools_dir)) as server:
        settings.tool_execution_enabled = True
        settings.tool_runner_url = f"http://127.0.0.1:{server.port}"
        yield server
    settings.tool_execution_enabled = False


async def test_integration_mode_executes_tools(api, migrated_app, scripted_server, runner):
    status = (await api.get("/api/v1/tools/status")).json()
    assert status["enabled"] and status["reachable"], status
    assert {t["name"] for t in status["tools"]} == {"calculator", "lookup_order"}
    assert "url" not in str(status) and "script" not in status["tools"][0]

    ep = await make_endpoint(api, scripted_server)
    ds = await make_ds(api, "tools", "tool_call", CASES)
    run = await create_run(
        api,
        [ep],
        [ds],
        suites=["tool_calling"],
        evaluation={"tool_mode": "integration"},
        load={"profile": "sequential"},
    )
    evaluation = run["configuration_snapshot"]["evaluation"]
    assert evaluation["tool_execution"].startswith("integration")
    assert evaluation["framework_version"] == "4"
    assert evaluation["tool_registry"]["sha256"] == status["registry_sha256"]
    assert evaluation["tool_registry"]["tools"] == {"calculator": "1", "lookup_order": "1"}

    assert await execute(migrated_app, run["id"]) == "completed"
    results = await all_results(api, run["id"])
    by_case = {r["case_identifier"]: r for r in results["items"]}
    detail = (await api.get(f"/api/v1/runs/{run['id']}/results/{by_case['calc']['id']}")).json()
    sim = detail["response_data"]["simulation"]
    assert sim["mode"] == "integration" and sim["executed"] is True and sim["completed"]
    call = sim["rounds"][0]["tool_results"][0]
    assert call["executed"] is True and call["valid"] is True
    assert '"result": 42' in call["result"] and call["duration_ms"] is not None
    assert "42" in sim["final_content"]

    v = verdicts(results)
    assert v[("calc", "tool_final_answer")][2] is True
    assert v[("order", "tool_final_answer")][2] is True  # "shipped" came from the fixture file

    zero = (await api.get(f"/api/v1/runs/{run['id']}/results/{by_case['zero']['id']}")).json()
    zero_call = zero["response_data"]["simulation"]["rounds"][0]["tool_results"][0]
    assert "division by zero" in zero_call["result"]  # the tool's own error reached the model


async def test_integration_mode_is_rejected_when_disabled(api, scripted_server, settings):
    settings.tool_execution_enabled = False
    ep = await make_endpoint(api, scripted_server)
    ds = await make_ds(api, "tools", "tool_call", CASES[:1])
    body = await create_run(
        api,
        [ep],
        [ds],
        expect=422,
        suites=["tool_calling"],
        evaluation={"tool_mode": "integration"},
    )
    assert "TOOL_EXECUTION_ENABLED" in str(body)
    status = (await api.get("/api/v1/tools/status")).json()
    assert status == status | {"enabled": False, "reachable": False}


async def test_missing_tools_are_named(api, migrated_app, scripted_server, runner):
    unknown = {
        "type": "function",
        "function": {"name": "send_email", "parameters": {"type": "object"}},
    }
    ep = await make_endpoint(api, scripted_server)
    ds = await make_ds(
        api, "tools", "tool_call", [CASES[0] | {"tools": [CALC, unknown], "id": "x"}]
    )
    body = await create_run(
        api,
        [ep],
        [ds],
        expect=422,
        suites=["tool_calling"],
        evaluation={"tool_mode": "integration"},
    )
    assert body["error"]["code"] == "tools_not_registered"
    assert body["error"]["details"] == {"missing_tools": ["send_email"]}


async def test_runner_down_records_tool_errors(api, migrated_app, scripted_server, runner):
    ep = await make_endpoint(api, scripted_server)
    ds = await make_ds(api, "tools", "tool_call", CASES[:1])
    run = await create_run(
        api,
        [ep],
        [ds],
        suites=["tool_calling"],
        evaluation={"tool_mode": "integration"},
        load={"profile": "sequential"},
    )
    runner.__exit__()  # the runner disappears after the run was accepted
    assert await execute(migrated_app, run["id"]) == "completed"
    results = await all_results(api, run["id"])
    detail = (await api.get(f"/api/v1/runs/{run['id']}/results/{results['items'][0]['id']}")).json()
    call = detail["response_data"]["simulation"]["rounds"][0]["tool_results"][0]
    assert call["executed"] is False and call["valid"] is False
    assert "unreachable" in call["error"]
