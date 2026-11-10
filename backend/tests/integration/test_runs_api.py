"""Benchmark runs end to end against the mock model server (milestone 3 acceptance)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.db.models.run import BenchmarkRun
from app.execution.engine import RunExecutor, claim_next_run, claim_run, utcnow
from app.execution.worker import Worker
from app.metrics.stats import percentile
from tests.conftest import MOCK_API_KEY, MOCK_SECRET_REF


@pytest.fixture(autouse=True)
def fast_progress(settings):
    settings.run_progress_interval_seconds = 0.05


def jsonl(records: list[dict]) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


async def make_endpoint(api, mock_server, name="mock-ep", headers=None, **rc):
    request_configuration = {
        "max_tokens": 32,
        "retry_backoff_seconds": 0,
        "headers": {"X-Mock-First-Token-Delay-Ms": "20", "X-Mock-Chunk-Delay-Ms": "2"}
        | (headers or {}),
        **rc,
    }
    resp = await api.post(
        "/api/v1/endpoints",
        json={
            "name": name,
            "base_url": mock_server.base_url,
            "model_identifier": "mock-model",
            "secret_reference": MOCK_SECRET_REF,
            "request_configuration": request_configuration,
            "deployment_metadata": {"backend": "mock", "gpu": "none"},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def make_dataset(api, name="perf-set", n=6, prefix=None, texts=None):
    prefix = prefix or uuid.uuid4().hex[:8]
    texts = texts or [f"{prefix} question number {i} about networking" for i in range(n)]
    records = [
        {"id": f"c{i}", "type": "qa", "input": t, "reference_answer": "x"}
        for i, t in enumerate(texts)
    ]
    resp = await api.post(
        "/api/v1/datasets",
        json={"name": name, "dataset_type": "qa", "content": jsonl(records), "filename": "d.jsonl"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_run(api, endpoints, datasets, expect=201, **body):
    payload = {
        "name": body.pop("name", "bench"),
        "endpoint_ids": [e["id"] for e in endpoints],
        "datasets": [{"dataset_id": d["id"]} for d in datasets],
        **body,
    }
    resp = await api.post("/api/v1/runs", json=payload)
    assert resp.status_code == expect, resp.text
    return resp.json()


def executor(app, run_id, worker_id="worker-a") -> RunExecutor:
    return RunExecutor(
        uuid.UUID(run_id),
        app.state.session_factory,
        app.state.adapter_context,
        app.state.settings,
        worker_id,
    )


async def execute(app, run_id, worker_id="worker-a"):
    assert await claim_run(app.state.session_factory, uuid.UUID(run_id), worker_id, 30)
    return await executor(app, run_id, worker_id).execute()


async def metrics_of(api, run_id, model_index=0, step=None):
    run = (await api.get(f"/api/v1/runs/{run_id}")).json()
    rm_id = run["models"][model_index]["id"]
    data = (await api.get(f"/api/v1/runs/{run_id}/metrics")).json()
    out: dict[str, dict] = {}
    for m in data["items"]:
        if (
            m["run_model_id"] == rm_id
            and m["load_step"] == step
            and m.get("context_length") is None
        ):
            out.setdefault(m["metric_name"], {})[m["aggregation"]] = m
    return data, out


async def all_results(api, run_id, **params):
    resp = await api.get(f"/api/v1/runs/{run_id}/results", params={"limit": 500, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def wait_until(predicate, limit_s=10.0):
    deadline = asyncio.get_running_loop().time() + limit_s
    while not await predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


# ------------------------------------------------------------------------------------- tests


async def test_fixed_concurrency_run_end_to_end(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server)
    ds = await make_dataset(api, n=6)
    run = await create_run(
        api,
        [ep],
        [ds],
        name="fixed",
        generation={"temperature": 0, "max_tokens": 16},
        load={
            "profile": "fixed_concurrency",
            "requests": 12,
            "concurrency": 3,
            "warmup_requests": 2,
        },
    )
    assert run["status"] == "queued"
    snap = run["configuration_snapshot"]
    assert snap["sampling"] == "greedy"
    assert snap["datasets"][0]["content_hash"] == ds["current_version"]["content_hash"]
    assert snap["load"]["steps"] == [
        {"step": None, "concurrency": 3, "start": 0, "count": 12, "duration_seconds": None}
    ]
    assert snap["endpoints"][0]["secret_reference"] == MOCK_SECRET_REF
    assert MOCK_API_KEY not in json.dumps(run)

    assert await execute(migrated_app, run["id"]) == "completed"

    done = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    assert done["status"] == "completed"
    assert done["started_at"] and done["completed_at"]
    assert done["pass_count"] == 1
    model = done["models"][0]
    assert model["status"] == "completed"
    assert model["progress"]["completed"] == 12
    assert model["progress"]["warmup_completed"] == 2
    assert model["hardware_metadata"] == {"backend": "mock", "gpu": "none"}

    results = await all_results(api, run["id"])
    assert results["total"] == 14
    warmups = [r for r in results["items"] if r["is_warmup"]]
    measured = [r for r in results["items"] if not r["is_warmup"]]
    assert len(warmups) == 2 and len(measured) == 12
    assert sorted(r["request_index"] for r in measured) == list(range(12))
    assert {r["case_identifier"] for r in measured} == {f"c{i}" for i in range(6)}
    assert all(r["status"] == "succeeded" and r["is_final"] for r in results["items"])
    first = measured[0]
    assert first["request_metadata"]["concurrency"] == 3
    assert first["request_metadata"]["max_tokens"] == 16
    assert first["performance_metrics"]["token_count_source"] == "server_reported"
    assert first["response_data"]["content"].startswith("Mock response to:")

    data, m = await metrics_of(api, run["id"])
    assert data["live"] is False
    assert m["requests"]["attempted"]["metric_value"] == 12
    assert m["requests"]["succeeded"]["metric_value"] == 12
    assert m["error_rate"]["ratio"]["metric_value"] == 0
    # Warm-up requests are excluded: 12 samples, not 14.
    assert m["latency_ms"]["p50"]["sample_count"] == 12
    assert m["ttft_ms"]["p50"]["metric_value"] >= 15  # mock first-token delay 20 ms
    # Metrics come from the per-request observations.
    latencies = [r["latency_ms"] for r in measured]
    assert m["latency_ms"]["p95"]["metric_value"] == pytest.approx(percentile(latencies, 95))
    assert m["latency_ms"]["max"]["metric_value"] == pytest.approx(max(latencies))
    assert m["request_throughput"]["value"]["metric_value"] > 0
    assert m["output_token_throughput"]["value"]["metric_value"] > 0
    assert m["output_tokens_per_second"]["p50"]["metric_value"] > 0
    assert m["retries"]["count"]["metric_value"] == 0
    assert MOCK_API_KEY not in json.dumps(data)


async def test_failures_stay_visible_and_retry_failed(api, migrated_app, mock_server):
    ep = await make_endpoint(
        api, mock_server, headers={"X-Mock-Fail-If-Contains": "BROKEN"}, max_retries=1
    )
    ds = await make_dataset(api, texts=["first ok", "second BROKEN case", "third ok", "fourth ok"])
    run = await create_run(api, [ep], [ds], load={"profile": "sequential"})
    assert await execute(migrated_app, run["id"]) == "completed_with_errors"

    done = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    assert done["error_summary"]["failed_requests"] == 1
    assert done["error_summary"]["errors"] == {"http_status": 1}
    assert "1 measured request(s) failed" in done["error_summary"]["message"]

    failed = await all_results(api, run["id"], status="failed")
    # The original attempt and its retry are separate rows; only the last one is final.
    assert [(r["attempt_number"], r["is_final"]) for r in failed["items"]] == [
        (1, False),
        (2, True),
    ]
    assert failed["items"][0]["http_status"] == 503
    assert failed["items"][0]["case_identifier"] == "c1"

    _, m = await metrics_of(api, run["id"])
    assert m["requests"]["failed"]["metric_value"] == 1
    assert m["error_rate"]["ratio"]["metric_value"] == 0.25  # failures stay in the denominator
    assert m["retries"]["count"]["metric_value"] == 1
    assert m["latency_ms"]["p50"]["sample_count"] == 3  # latency from successes only
    assert m["errors"]["by_type"]["metadata"]["by_type"] == {"http_status": 1}

    retry = await api.post(f"/api/v1/runs/{run['id']}/retry-failed")
    assert retry.status_code == 200 and retry.json()["status"] == "queued"
    assert await execute(migrated_app, run["id"]) == "completed_with_errors"
    again = await all_results(api, run["id"], status="failed")
    assert [(r["attempt_number"], r["pass_number"]) for r in again["items"]][-2:] == [
        (3, 2),
        (4, 2),
    ]
    ok = await all_results(api, run["id"], status="succeeded")
    assert ok["total"] == 3  # successful requests were not re-sent
    _, m = await metrics_of(api, run["id"])
    assert m["requests"]["attempted"]["metric_value"] == 4
    assert m["request_throughput"]["value"]["metric_value"] is None
    assert "not contiguous" in m["request_throughput"]["value"]["metadata"]["unavailable_reason"]

    # Finished runs can't be retried unless they had errors; completed runs refuse.
    second = await create_run(
        api, [ep], [await make_dataset(api, name="ok-set", n=2)], load={"profile": "sequential"}
    )
    assert await execute(migrated_app, second["id"]) == "completed"
    refused = await api.post(f"/api/v1/runs/{second['id']}/retry-failed")
    assert refused.status_code == 409


async def test_transient_errors_succeed_on_retry(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server, headers={"X-Mock-Fail-First": "1"}, max_retries=2)
    ds = await make_dataset(api, n=3)
    run = await create_run(api, [ep], [ds], load={"profile": "fixed_concurrency", "concurrency": 3})
    assert await execute(migrated_app, run["id"]) == "completed"
    results = await all_results(api, run["id"])
    assert results["total"] == 6
    for idx in range(3):
        rows = [r for r in results["items"] if r["request_index"] == idx]
        assert [(r["attempt_number"], r["status"], r["is_final"]) for r in rows] == [
            (1, "failed", False),
            (2, "succeeded", True),
        ]
    _, m = await metrics_of(api, run["id"])
    assert m["error_rate"]["ratio"]["metric_value"] == 0
    assert m["retries"]["count"]["metric_value"] == 3


async def test_non_streaming_reports_ttft_unavailable(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server)
    ds = await make_dataset(api, n=3)
    run = await create_run(api, [ep], [ds], load={"profile": "sequential", "stream": False})
    assert run["configuration_snapshot"]["endpoints"][0]["request_configuration"]["stream"] is False
    assert await execute(migrated_app, run["id"]) == "completed"
    results = await all_results(api, run["id"])
    assert all(r["ttft_ms"] is None and r["latency_ms"] > 0 for r in results["items"])
    _, m = await metrics_of(api, run["id"])
    ttft = m["ttft_ms"]["p50"]
    assert ttft["metric_value"] is None and ttft["sample_count"] == 0
    assert "not observable without streaming" in ttft["metadata"]["unavailable_reason"]
    assert "inter_token_latency_ms" not in m
    assert m["latency_ms"]["p50"]["metric_value"] > 0


async def test_ramp_profile_reports_each_step(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server)
    ds = await make_dataset(api, n=4)
    run = await create_run(
        api,
        [ep],
        [ds],
        load={"profile": "ramp", "ramp_concurrency": [1, 2, 4], "requests_per_step": 4},
    )
    assert run["load"]["requests"] == 12
    assert await execute(migrated_app, run["id"]) == "completed"
    results = await all_results(api, run["id"])
    steps = {(r["load_step"], r["request_metadata"]["concurrency"]) for r in results["items"]}
    assert steps == {(0, 1), (1, 2), (2, 4)}
    for step, conc in ((0, 1), (1, 2), (2, 4)):
        _, m = await metrics_of(api, run["id"], step=step)
        assert m["requests"]["attempted"]["metric_value"] == 4
        assert m["latency_ms"]["p50"]["metadata"]["concurrency"] == conc
    _, overall = await metrics_of(api, run["id"])
    assert overall["requests"]["attempted"]["metric_value"] == 12


async def test_two_models_run_one_after_another(api, migrated_app, mock_server):
    a = await make_endpoint(api, mock_server, name="model-a")
    b = await make_endpoint(api, mock_server, name="model-b")
    ds = await make_dataset(api, n=2)
    run = await create_run(api, [a, b], [ds], load={"profile": "sequential"})
    assert await execute(migrated_app, run["id"]) == "completed"
    done = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    assert [m["name"] for m in done["models"]] == ["model-a", "model-b"]
    assert done["models"][0]["completed_at"] <= done["models"][1]["started_at"]
    for i in (0, 1):
        _, m = await metrics_of(api, run["id"], model_index=i)
        assert m["requests"]["succeeded"]["metric_value"] == 2


async def test_cancel_queued_and_running(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server, headers={"X-Mock-First-Token-Delay-Ms": "150"})
    ds = await make_dataset(api, n=3)

    queued = await create_run(api, [ep], [ds], load={"profile": "sequential"})
    cancelled = (await api.post(f"/api/v1/runs/{queued['id']}/cancel")).json()
    assert cancelled["status"] == "cancelled"
    assert (
        await claim_run(migrated_app.state.session_factory, uuid.UUID(queued["id"]), "w", 30)
        is False
    )
    assert (await api.post(f"/api/v1/runs/{queued['id']}/cancel")).status_code == 409

    run = await create_run(api, [ep], [ds], load={"profile": "sequential", "requests": 30})
    assert await claim_run(migrated_app.state.session_factory, uuid.UUID(run["id"]), "w", 30)
    task = asyncio.create_task(executor(migrated_app, run["id"], "w").execute())

    async def some_results():
        return (await all_results(api, run["id"]))["total"] >= 2

    await wait_until(some_results)
    resp = await api.post(f"/api/v1/runs/{run['id']}/cancel")
    assert resp.json()["status"] == "cancel_requested"
    assert (await api.delete(f"/api/v1/runs/{run['id']}")).status_code == 409
    assert await asyncio.wait_for(task, 10) == "cancelled"

    done = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    assert done["status"] == "cancelled" and done["cancel_requested_at"]
    assert done["models"][0]["status"] == "cancelled"
    _, m = await metrics_of(api, run["id"])
    attempted = m["requests"]["attempted"]["metric_value"]
    assert 2 <= attempted < 30
    assert m["requests"]["not_completed"]["metric_value"] == 30 - attempted
    assert (await api.delete(f"/api/v1/runs/{run['id']}")).status_code == 204
    assert (await api.get(f"/api/v1/runs/{run['id']}")).status_code == 404


async def test_resume_after_worker_shutdown_and_expired_lease(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server, headers={"X-Mock-First-Token-Delay-Ms": "400"})
    ds = await make_dataset(api, n=4)
    run = await create_run(
        api, [ep], [ds], load={"profile": "fixed_concurrency", "requests": 16, "concurrency": 2}
    )
    factory = migrated_app.state.session_factory
    assert await claim_run(factory, uuid.UUID(run["id"]), "worker-a", 30)
    first = executor(migrated_app, run["id"], "worker-a")
    task = asyncio.create_task(first.execute())

    async def some_results():
        return (await all_results(api, run["id"]))["total"] >= 4

    await wait_until(some_results)
    first.stop("shutdown")  # graceful worker exit: hand the run back
    assert await task is None
    handed_back = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    assert handed_back["status"] == "queued"
    partial = (await all_results(api, run["id"]))["total"]
    assert 4 <= partial < 16

    # Simulate a crashed worker: the run looks active, but the lease has expired.
    async with factory() as s:
        await s.execute(
            update(BenchmarkRun)
            .where(BenchmarkRun.id == uuid.UUID(run["id"]))
            .values(
                status="running",
                worker_id="dead-worker",
                lease_expires_at=utcnow() - timedelta(seconds=5),
            )
        )
        await s.commit()
    claimed = await claim_next_run(factory, "worker-b", 30)
    assert str(claimed) == run["id"]
    assert await executor(migrated_app, run["id"], "worker-b").execute() == "completed"

    done = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    assert done["status"] == "completed" and done["pass_count"] == 2
    results = await all_results(api, run["id"])
    finals = [r for r in results["items"] if r["is_final"]]
    assert sorted(r["request_index"] for r in finals) == list(range(16))  # no duplicates
    sequences = [r["sequence"] for r in results["items"]]
    assert sorted(sequences) == list(range(1, results["total"] + 1))
    _, m = await metrics_of(api, run["id"])
    assert m["requests"]["succeeded"]["metric_value"] == 16
    assert m["request_throughput"]["value"]["metric_value"] is None  # window interrupted


async def test_worker_process_executes_queued_runs(api, migrated_app, mock_server, redis):
    ep = await make_endpoint(api, mock_server)
    ds = await make_dataset(api, n=2)
    run = await create_run(api, [ep], [ds], load={"profile": "sequential"})
    settings = migrated_app.state.settings
    settings.worker_poll_interval_seconds = 0.05
    settings.worker_heartbeat_interval_seconds = 0.05
    worker = Worker(
        settings,
        redis,
        worker_id="loop-worker",
        session_factory=migrated_app.state.session_factory,
        adapter_context=migrated_app.state.adapter_context,
    )
    task = asyncio.create_task(worker.run())

    async def finished():
        r = (await api.get(f"/api/v1/runs/{run['id']}")).json()
        return r["status"] in ("completed", "completed_with_errors", "failed")

    await wait_until(finished)
    worker.request_stop()
    await asyncio.wait_for(task, 5)
    assert (await api.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "completed"
    summary = (await api.get("/api/v1/runs/summary")).json()
    assert summary["by_status"]["completed"] == 1 and summary["queued"] == 0


async def test_events_stream(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server)
    ds = await make_dataset(api, n=3)
    run = await create_run(api, [ep], [ds], load={"profile": "sequential"})
    await execute(migrated_app, run["id"])
    resp = await api.get(f"/api/v1/runs/{run['id']}/events")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-accel-buffering"] == "no"
    events = [
        (block.split("event: ")[1].split("\n")[0], block)
        for block in resp.text.split("\n\n")
        if "event: " in block
    ]
    names = [n for n, _ in events]
    assert names.count("case.completed") == 3
    assert names[-2:] == ["metric.updated", "run.end"]
    assert "run.progress" in names
    case_block = next(b for n, b in events if n == "case.completed")
    assert case_block.startswith("id: 1\n")

    resumed = await api.get(f"/api/v1/runs/{run['id']}/events", headers={"Last-Event-ID": "2"})
    assert resumed.text.count("event: case.completed") == 1
    assert (await api.get(f"/api/v1/runs/{uuid.uuid4()}/events")).status_code == 404


async def test_create_validation(api, mock_server):
    ep = await make_endpoint(api, mock_server)
    ds = await make_dataset(api, n=3)

    async def rejected(**body):
        payload = {
            "name": "x",
            "endpoint_ids": [ep["id"]],
            "datasets": [{"dataset_id": ds["id"]}],
            **body,
        }
        resp = await api.post("/api/v1/runs", json=payload)
        assert resp.status_code == 422, resp.text
        return json.dumps(resp.json()["error"]["details"])

    assert "compared across runs" in await rejected(suites=["performance", "quantization"])
    assert "concurrency 1" in await rejected(load={"profile": "sequential", "concurrency": 4})
    assert "at most 5" in await rejected(load={"profile": "smoke", "requests": 10})
    assert "two concurrency steps" in await rejected(
        load={"profile": "ramp", "ramp_concurrency": [2]}
    )
    assert "increase" in await rejected(load={"profile": "ramp", "ramp_concurrency": [4, 2]})
    assert "duration_seconds" in await rejected(load={"profile": "sustained"})
    assert "At most" in await rejected(load={"requests": 10_000_000})
    assert "Concurrency may be at most" in await rejected(load={"concurrency": 1000})
    assert "does not exist" in await rejected(endpoint_ids=[str(uuid.uuid4())])
    assert "once" in await rejected(endpoint_ids=[ep["id"], ep["id"]])

    await api.patch(f"/api/v1/endpoints/{ep['id']}", json={"enabled": False})
    assert "disabled" in await rejected()


async def test_smoke_profile_and_datasets_in_use(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server)
    ds1 = await make_dataset(api, name="set-one", n=4)
    ds2 = await make_dataset(api, name="set-two", n=4)
    run = await create_run(api, [ep], [ds1, ds2], load={"profile": "smoke"})
    assert run["load"]["requests"] == 5  # smoke: at most 5
    assert run["load"]["case_count"] == 8
    assert [d["name"] for d in run["datasets"]] == ["set-one", "set-two"]

    # A queued run pins its datasets.
    blocked = await api.delete(f"/api/v1/datasets/{ds1['id']}")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "dataset_in_use"

    assert await execute(migrated_app, run["id"]) == "completed"
    assert (await api.delete(f"/api/v1/datasets/{ds1['id']}")).status_code == 204
    kept = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    assert kept["datasets"][0]["available"] is False  # results stay inspectable
    assert kept["datasets"][0]["content_hash"] == ds1["current_version"]["content_hash"]
    results = await all_results(api, run["id"])
    assert results["total"] == 5

    # Retrying needs the cases, which are gone: the run fails with a clear message.
    async with migrated_app.state.session_factory() as s:
        await s.execute(
            update(BenchmarkRun)
            .where(BenchmarkRun.id == uuid.UUID(run["id"]))
            .values(status="completed_with_errors")
        )
        await s.commit()
    await api.post(f"/api/v1/runs/{run['id']}/retry-failed")
    assert await execute(migrated_app, run["id"]) == "failed"
    failed = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    assert "deleted" in failed["error_summary"]["message"]


async def test_result_detail_and_listing_filters(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server, headers={"X-Mock-Response": ("word " * 200).strip()})
    ds = await make_dataset(api, n=2)
    run = await create_run(
        api,
        [ep],
        [ds],
        load={"profile": "sequential", "warmup_requests": 1},
        generation={"max_tokens": 500},
    )
    await execute(migrated_app, run["id"])
    listing = await all_results(api, run["id"], phase="measured")
    assert listing["total"] == 2
    item = listing["items"][0]
    assert item["truncated"] is True and len(item["response_data"]["content"]) == 300
    full = (await api.get(f"/api/v1/runs/{run['id']}/results/{item['id']}")).json()
    assert full["truncated"] is False and len(full["response_data"]["content"]) > 300
    assert (await all_results(api, run["id"], phase="warmup"))["total"] == 1
    assert (await all_results(api, run["id"], q="c1"))["total"] == 1
    other = await api.get(f"/api/v1/runs/{uuid.uuid4()}/results/{item['id']}")
    assert other.status_code == 404
