"""RAG and hallucination evaluation end to end (milestone 5 acceptance).

The mock server answers from a script, and its judge mode answers judge prompts, so every
expected verdict below can be worked out by hand.
"""

from __future__ import annotations

import pytest

from app.execution.engine import RunExecutor
from app.testing.mock_openai import create_mock_app
from tests.conftest import MOCK_API_KEY, ServerThread
from tests.integration.test_quality_runs import all_results, make_ds, verdicts
from tests.integration.test_runs_api import create_run, execute, make_endpoint, metrics_of

HANDBOOK = {
    "document_id": "policy-001",
    "chunk_id": "c02",
    "text": "Full-time employees receive 25 working days of paid annual leave per calendar year.",
    "source": "handbook.pdf",
    "page": 3,
    "relevant": True,
}
EXPENSES = {
    "document_id": "policy-002",
    "chunk_id": "c01",
    "text": "Expense claims must be submitted within 30 days of purchase with a receipt.",
    "page": 1,
    "relevant": False,
}
CASES = [
    {
        "id": "leave",
        "type": "rag",
        "input": "How many days of annual leave do full-time employees get?",
        "retrieved_contexts": [HANDBOOK, EXPENSES],
        "reference_answer": "Full-time employees receive 25 working days of paid annual leave.",
        "expected_citations": [{"document_id": "policy-001", "page": 3}],
    },
    {
        "id": "expenses",
        "type": "rag",
        "input": "What is the deadline for expense claims?",
        "retrieved_contexts": [EXPENSES, HANDBOOK],
        "reference_answer": "Within 30 days of purchase.",
        "expected_citations": [{"document_id": "policy-002"}],
    },
    {
        "id": "carry-over",
        "type": "rag",
        "input": "Can unused leave be carried over?",
        "retrieved_contexts": [HANDBOOK],
        "answerable": False,
    },
]
SCRIPT = [
    {
        "match": "annual leave do full-time",
        "response": "Full-time employees receive 25 working days of paid annual leave per "
        "calendar year [policy-001#c02].",
    },
    {
        "match": "deadline for expense claims",
        "response": "Expense claims must be submitted within 45 days of purchase [policy-002]. "
        "Late claims are paid by the regional director in gold coins.",
    },
    {
        "match": "carried over",
        "response": "The provided context does not contain this information.",
    },
]
SUITES = ["performance", "rag", "hallucination"]


@pytest.fixture(autouse=True)
def fast_progress(settings):
    settings.run_progress_interval_seconds = 0.05


@pytest.fixture(scope="module")
def rag_server():
    with ServerThread(
        create_mock_app(models=["mock-model"], api_key=MOCK_API_KEY, script=SCRIPT)
    ) as server:
        yield server


async def setup(api, server, judge=False):
    ep = await make_endpoint(
        api, server, headers={"X-Mock-First-Token-Delay-Ms": "0", "X-Mock-Chunk-Delay-Ms": "0"}
    )
    ds = await make_ds(api, "rag", "rag", CASES)
    judge_ep = await make_endpoint(api, server, name="judge-ep") if judge else None
    return ep, ds, judge_ep


async def detail(api, run_id, case_id):
    results = await all_results(api, run_id)
    rid = next(r["id"] for r in results["items"] if r["case_identifier"] == case_id)
    return (await api.get(f"/api/v1/runs/{run_id}/results/{rid}")).json()


async def test_rag_suite_lexical_end_to_end(api, migrated_app, rag_server):
    ep, ds, _ = await setup(api, rag_server)
    run = await create_run(api, [ep], [ds], suites=SUITES, load={"profile": "sequential"})
    snap = run["configuration_snapshot"]["evaluation"]
    assert snap["suites"] == ["rag", "hallucination"]
    assert snap["evidence_method"] == "lexical"
    assert snap["judge_endpoint"] is None
    assert await execute(migrated_app, run["id"]) == "completed"

    v = verdicts(await all_results(api, run["id"]))
    assert v[("leave", "rag_faithfulness")][:4] == ("completed", 1.0, True, "faithful")
    assert v[("leave", "rag_citation_recall")][1] == 1.0
    assert v[("leave", "rag_citation_precision")][1] == 1.0
    assert v[("leave", "rag_context_precision")][1] == 1.0
    # 45 days is contradicted by the evidence (30); the gold coins are unsupported.
    assert v[("expenses", "rag_faithfulness")][3] == "contradicted"
    assert v[("expenses", "claim_support")][3] == "contradicted"
    assert v[("expenses", "rag_context_precision")][1] == 0.5  # relevant passage ranked 2nd
    assert v[("carry-over", "rag_abstention")][3] == "appropriate_abstention"
    assert v[("carry-over", "rag_faithfulness")][0] == "not_applicable"
    # No judge: answer relevance is skipped, never scored.
    assert v[("leave", "rag_answer_relevance")][:2] == ("skipped", None)

    # Claims are stored with evidence and source identifiers.
    d = await detail(api, run["id"], "expenses")
    faith = next(e for e in d["evaluations"] if e["evaluator_name"] == "rag_faithfulness")
    claims = {c["classification"]: c for c in faith["evidence"]}
    assert set(claims) == {"contradicted", "unsupported"}
    assert claims["contradicted"]["document_id"] == "policy-002"
    assert claims["contradicted"]["chunk_id"] == "c01"
    assert "30" in claims["contradicted"]["evidence"]
    assert claims["unsupported"]["review_status"] == "not_reviewed"

    _, m = await metrics_of(api, run["id"])
    assert m["quality.rag_faithfulness"]["pass_rate"]["metric_value"] == 0.5
    assert m["quality.evaluated_requests"]["count"]["metric_value"] == 3
    # 3 claims in total: 1 supported, 1 contradicted, 1 unsupported.
    assert m["hallucination.contradiction_rate"]["ratio"]["metric_value"] == pytest.approx(1 / 3)
    assert m["hallucination.unsupported_claim_rate"]["ratio"]["metric_value"] == pytest.approx(
        1 / 3
    )
    assert m["hallucination.abstention_recall"]["ratio"]["metric_value"] == 1.0
    assert m["hallucination.answer_despite_insufficient_evidence"]["ratio"]["metric_value"] == 0
    assert m["rag.claim_faithfulness"]["ratio"]["metric_value"] == pytest.approx(1 / 3)
    # Answer relevance had no judge: no pass rate, with a reason.
    relevance = m["quality.rag_answer_relevance"]["pass_rate"]
    assert relevance["metric_value"] is None and relevance["metadata"]["unavailable_reason"]


async def test_rag_suite_with_judge_runs_after_measurement(api, migrated_app, rag_server):
    ep, ds, judge = await setup(api, rag_server, judge=True)
    run = await create_run(
        api,
        [ep],
        [ds],
        suites=SUITES,
        load={"profile": "sequential"},
        evaluation={"judge": {"endpoint_id": judge["id"], "concurrency": 2}},
    )
    snap = run["configuration_snapshot"]["evaluation"]
    assert snap["evidence_method"] == "judge"
    assert snap["judge_endpoint"]["name"] == "judge-ep"
    assert snap["judge_endpoint"]["judges_tested_model"] is False
    assert snap["judge_prompt_version"] == "1"
    assert snap["config"]["judge"]["concurrency"] == 2
    assert await execute(migrated_app, run["id"]) == "completed"

    detail_run = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    progress = detail_run["models"][0]["progress"]
    assert (progress["judge_planned"], progress["judge_completed"]) == (3, 3)

    results = await all_results(api, run["id"])
    v = verdicts(results)
    assert v[("leave", "rag_faithfulness")][3] == "faithful"
    assert v[("expenses", "claim_support")][3] == "contradicted"
    assert v[("leave", "rag_answer_relevance")][0] == "completed"
    d = await detail(api, run["id"], "leave")
    for e in d["evaluations"]:
        if e["evaluator_name"] in ("rag_faithfulness", "claim_support", "rag_answer_relevance"):
            assert e["metadata"]["method"] == "judge", e["evaluator_name"]
            assert e["metadata"]["judge"]["endpoint"] == "judge-ep"
    # Judge requests are not measured results of the model under test.
    assert results["total"] == 3


async def test_judge_resumes_from_stored_responses(api, migrated_app, rag_server, monkeypatch):
    ep, ds, judge = await setup(api, rag_server, judge=True)
    run = await create_run(
        api,
        [ep],
        [ds],
        suites=["rag"],
        load={"profile": "sequential"},
        evaluation={"judge": {"endpoint_id": judge["id"]}},
    )
    original = RunExecutor._judge_phase
    calls = {"n": 0}

    async def stop_before_judging(self, state):
        calls["n"] += 1
        if calls["n"] == 1:  # the worker shuts down after measuring, before judging
            self.stop("shutdown")
            return None
        return await original(self, state)

    monkeypatch.setattr(RunExecutor, "_judge_phase", stop_before_judging)
    assert await execute(migrated_app, run["id"]) is None
    assert verdicts(await all_results(api, run["id"])) == {}
    assert await execute(migrated_app, run["id"], worker_id="worker-b") == "completed"
    v = verdicts(await all_results(api, run["id"]))
    assert v[("leave", "rag_faithfulness")][3] == "faithful"
    assert v[("expenses", "rag_faithfulness")][3] == "contradicted"


async def test_judge_cannot_resume_without_stored_text(
    api, migrated_app, rag_server, monkeypatch, settings
):
    settings.run_store_responses = False
    ep, ds, judge = await setup(api, rag_server, judge=True)
    run = await create_run(
        api,
        [ep],
        [ds],
        suites=["rag"],
        load={"profile": "sequential"},
        evaluation={"judge": {"endpoint_id": judge["id"]}},
    )
    original = RunExecutor._judge_phase
    calls = {"n": 0}

    async def stop_before_judging(self, state):
        calls["n"] += 1
        if calls["n"] == 1:
            self.stop("shutdown")
            return None
        return await original(self, state)

    monkeypatch.setattr(RunExecutor, "_judge_phase", stop_before_judging)
    assert await execute(migrated_app, run["id"]) is None
    assert await execute(migrated_app, run["id"], worker_id="worker-b") == "completed"
    v = verdicts(await all_results(api, run["id"]))
    assert v[("leave", "rag_faithfulness")][:3] == ("failed", None, None)


async def test_judge_validation(api, rag_server):
    ep, ds, _ = await setup(api, rag_server)
    resp = await api.post(
        "/api/v1/runs",
        json={
            "name": "x",
            "endpoint_ids": [ep["id"]],
            "datasets": [{"dataset_id": ds["id"]}],
            "suites": ["performance"],
            "evaluation": {"judge": {"endpoint_id": ep["id"]}},
        },
    )
    assert resp.status_code == 422 and "rag and hallucination" in resp.text
    resp = await api.post(
        "/api/v1/runs",
        json={
            "name": "x",
            "endpoint_ids": [ep["id"]],
            "datasets": [{"dataset_id": ds["id"]}],
            "suites": ["rag"],
            "evaluation": {"judge": {"endpoint_id": "00000000-0000-0000-0000-000000000009"}},
        },
    )
    assert resp.status_code == 422 and "judge endpoint" in resp.text
    # Self-judging is allowed but recorded.
    run = await create_run(
        api, [ep], [ds], suites=["rag"], evaluation={"judge": {"endpoint_id": ep["id"]}}
    )
    assert run["configuration_snapshot"]["evaluation"]["judge_endpoint"]["judges_tested_model"]


async def test_rag_suite_needs_rag_or_long_context_cases(api, rag_server):
    ep = await make_endpoint(api, rag_server)
    qa = await make_ds(
        api, "qa", "qa", [{"id": "q", "type": "qa", "input": "Hi?", "reference_answer": "Hi"}]
    )
    resp = await api.post(
        "/api/v1/runs",
        json={
            "name": "x",
            "endpoint_ids": [ep["id"]],
            "datasets": [{"dataset_id": qa["id"]}],
            "suites": ["hallucination"],
        },
    )
    assert resp.status_code == 422
    assert "long_context, rag" in resp.text


async def test_smoke_scenario_self_judging_echo_model(api, migrated_app, rag_server):
    """Mirrors scripts/smoke-test.sh: an echo model that also judges its own answers."""
    ep = await make_endpoint(api, rag_server)
    case = {
        "id": "k1",
        "type": "rag",
        "input": "When does the office open?",
        "retrieved_contexts": [
            {
                "document_id": "kb-1",
                "chunk_id": "c1",
                "text": "The office opens at 9 am on weekdays.",
            }
        ],
        "reference_answer": "At 9 am on weekdays.",
    }
    ds = await make_ds(api, "smoke-rag", "rag", [case])
    run = await create_run(
        api,
        [ep],
        [ds],
        suites=SUITES,
        load={"profile": "smoke", "requests": 2, "concurrency": 1},
        evaluation={"judge": {"endpoint_id": ep["id"], "concurrency": 2}},
    )
    assert run["configuration_snapshot"]["evaluation"]["evidence_method"] == "judge"
    assert await execute(migrated_app, run["id"]) == "completed"
    _, m = await metrics_of(api, run["id"])
    assert m["quality.rag_context_relevance"]["pass_rate"]["metric_value"] == 1.0
    assert "ratio" in m["hallucination.contradiction_rate"]
