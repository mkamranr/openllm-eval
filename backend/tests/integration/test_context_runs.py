"""Context-window experiments and resource telemetry end to end (milestone 7 acceptance).

The mock server answers generated needle questions from the facts in the leading share of the
prompt it is told to "see" (X-Mock-Needle-Reach), rejects prompts above X-Mock-Max-Context
estimated tokens with a context-length error, and serves DCGM / vLLM metrics on /metrics.
"""

from __future__ import annotations

import pytest

from tests.conftest import MOCK_SECRET_REF
from tests.integration.test_runs_api import create_run, execute, make_endpoint, metrics_of


@pytest.fixture(autouse=True)
def fast_progress(settings):
    settings.run_progress_interval_seconds = 0.05


NEEDLE = {
    "X-Mock-Mode": "needle",
    "X-Mock-Needle-Reach": "0.75",
    "X-Mock-Max-Context": "3000",
}


async def generate(api, name="needles", **params):
    body = {
        "name": name,
        "params": {"context_lengths": [1024, 2048, 4096], "depths": [0.1, 0.5, 0.9], **params},
    }
    resp = await api.post("/api/v1/datasets/generate", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def by_length(data, rm_id):
    out: dict[int, dict[str, dict]] = {}
    for m in data["items"]:
        if m["run_model_id"] == rm_id and m["context_length"] is not None:
            out.setdefault(m["context_length"], {}).setdefault(m["metric_name"], {})[
                m["aggregation"]
            ] = m
    return out


async def test_generated_dataset_is_deterministic_and_valid(api):
    a = await generate(api, "needles-a")
    b = await generate(api, "needles-b")
    c = await generate(api, "needles-c", seed=7)
    assert a["source"] == "generated"
    assert a["dataset_type"] == "long_context"
    assert a["current_version"]["record_count"] == 9
    assert a["current_version"]["content_hash"] == b["current_version"]["content_hash"]
    assert a["current_version"]["content_hash"] != c["current_version"]["content_hash"]
    assert "4 characters per token" in a["description"]

    version = a["current_version"]["id"]
    resp = await api.get(
        f"/api/v1/datasets/{a['id']}/cases/ctx-2048-d0.5-1", params={"version_id": version}
    )
    assert resp.status_code == 200, resp.text
    case = resp.json()["content"]
    assert case["context_length"] == 2048
    assert case["context_estimate"]["method"] == "heuristic"
    # The heuristic size lands near the requested length.
    assert 1900 <= case["context_estimate"]["tokens"] <= 2200
    assert case["expected_answer"] in case["context"]

    multi = await generate(api, "needles-multi", needles_per_case=3, context_lengths=[1024])
    assert multi["current_version"]["record_count"] == 1

    bad = await api.post(
        "/api/v1/datasets/generate",
        json={"name": "too-big", "params": {"context_lengths": [524288], "repeats": 50}},
    )
    assert bad.status_code == 422
    assert "tokens" in bad.text


async def test_context_window_run(api, migrated_app, mock_server):
    ds = await generate(api)
    ep = await make_endpoint(api, mock_server, name="ctx-ep", headers=NEEDLE)
    patch = await api.patch(
        f"/api/v1/endpoints/{ep['id']}",
        json={
            "telemetry": {"url": mock_server.base_url[:-3] + "/metrics", "interval_seconds": 0.5}
        },
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["telemetry"]["url"].endswith("/metrics")

    run = await create_run(
        api,
        [ep],
        [ds],
        suites=["context_window"],
        load={"profile": "sequential", "requests": 9},
    )
    assert await execute(migrated_app, run["id"]) == "completed_with_errors"

    run = (await api.get(f"/api/v1/runs/{run['id']}")).json()
    rm_id = run["models"][0]["id"]
    data, overall = await metrics_of(api, run["id"])
    ctx = by_length(data, rm_id)
    assert sorted(ctx) == [1024, 2048, 4096]

    # Requested 4096 tokens exceeds the mock's 3000: every request overflows, and the error
    # is classified as a context overflow (not retried).
    big = ctx[4096]
    assert big["context.requests"]["count"]["metric_value"] == 3
    assert big["context.success_rate"]["ratio"]["metric_value"] == 0
    assert big["context.overflow_rate"]["ratio"]["metric_value"] == 1
    assert big["context.input_tokens"]["mean"]["metric_value"] is None
    assert big["context.input_tokens"]["mean"]["metadata"]["unavailable_reason"]
    # Retrieval is not measured for failed requests: null, never 0.
    assert big["context.retrieval_accuracy"]["pass_rate"]["metric_value"] is None

    for length in (1024, 2048):
        group = ctx[length]
        assert group["context.success_rate"]["ratio"]["metric_value"] == 1
        assert group["context.overflow_rate"]["ratio"]["metric_value"] == 0
        # Requested, estimated and actual token counts are kept apart.
        estimated = group["context.estimated_input_tokens"]["mean"]["metric_value"]
        actual = group["context.input_tokens"]["mean"]
        assert abs(estimated - length) / length < 0.15
        assert actual["metadata"]["token_count_source"] == {"server_reported": 3}
        assert actual["metric_value"] != estimated
        assert group["context.actual_to_estimated"]["mean"]["metric_value"] > 0.5
        assert group["context.truncation_suspected"]["ratio"]["metric_value"] == 0
        # The mock "sees" the first 75% of the prompt: early and middle needles are found.
        acc = group["context.retrieval_accuracy"]
        assert acc["pass_rate"]["metric_value"] == pytest.approx(2 / 3)
        assert acc["early"]["metric_value"] == 1
        assert acc["middle"]["metric_value"] == 1
        assert acc["late"]["metric_value"] == 0
        assert group["context.ttft_ms"]["p50"]["metric_value"] is not None
        assert group["context.requests"]["count"]["metadata"]["requested_context_length"] == length

    # Context rows do not leak into the overall (context_length = null) metrics.
    assert "context.requests" not in overall
    assert overall["requests"]["failed"]["metric_value"] == 3

    results = (await api.get(f"/api/v1/runs/{run['id']}/results", params={"limit": 50})).json()[
        "items"
    ]
    overflow = [r for r in results if r["status"] == "failed"]
    assert {r["error_type"] for r in overflow} == {"context_overflow"}
    assert all(r["attempt_number"] == 1 for r in overflow)
    detail = (await api.get(f"/api/v1/runs/{run['id']}/results/{results[0]['id']}")).json()
    meta = detail["request_metadata"]
    assert meta["context_length"] in (1024, 2048, 4096)
    assert meta["token_estimator"] == "heuristic"
    assert meta["estimated_input_tokens"] > 0

    # Telemetry from the mock exporter.
    assert overall["telemetry.samples"]["count"]["metric_value"] >= 1
    memory = overall["gpu.memory_used_mb"]["max"]["metric_value"]
    assert memory >= 8000
    assert overall["gpu.memory_total_mb"]["max"]["metric_value"] == 40960
    assert overall["gpu.kv_cache_usage_pct"]["max"]["metric_value"] is not None
    samples = (await api.get(f"/api/v1/runs/{run['id']}/telemetry")).json()
    assert samples["items"][0]["values"]["gpus"][0]["gpu"] == "0"
    assert samples["items"][0]["source"] == "prometheus"

    # The HTML report has the context and resource sections.
    report = await api.post(f"/api/v1/runs/{run['id']}/reports", json={"format": "html"})
    assert report.status_code == 201, report.text
    html = (await api.get(f"/api/v1/reports/{report.json()['id']}/download")).text
    assert 'id="context"' in html and 'id="resources"' in html
    assert "Server-reported input tokens" in html
    assert "Retrieval accuracy by requested context length" in html
    assert "Failed scrapes" in html  # status table (charts need two or more samples)
    redacted = await api.post(
        f"/api/v1/runs/{run['id']}/reports", json={"format": "json", "redact_endpoints": True}
    )
    body = (await api.get(f"/api/v1/reports/{redacted.json()['id']}/download")).text
    assert "/metrics" not in body
    assert '"telemetry"' in body


async def test_quantization_variants_comparison(api, migrated_app, mock_server):
    ds = await generate(api, "needles-v", context_lengths=[1024, 2048])
    runs = []
    for name, meta in (
        ("fp16", {"family": "mock", "weight_precision": "fp16", "weight_bits": 16}),
        ("awq", {"family": "mock", "quantization_method": "AWQ", "weight_bits": 4}),
        ("unknown", {}),
    ):
        resp = await api.post(
            "/api/v1/endpoints",
            json={
                "name": name,
                "base_url": mock_server.base_url,
                "model_identifier": "mock-model",
                "secret_reference": MOCK_SECRET_REF,
                "request_configuration": {"headers": NEEDLE},
                "model_metadata": meta,
                "deployment_metadata": {"serving_backend": "mock", "gpu_count": "1"},
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["deployment_metadata"]["gpu_count"] == 1
        run = await create_run(
            api,
            [resp.json()],
            [ds],
            name=name,
            suites=["context_window"],
            load={"profile": "sequential"},
        )
        await execute(migrated_app, run["id"])
        runs.append(run)
    ids = ",".join(r["id"] for r in runs)
    result = (await api.get("/api/v1/compare", params={"run_ids": ids})).json()
    variants = result["variants"]
    fields = {f["key"]: f for f in variants["fields"]}
    subjects = [s["id"] for s in result["subjects"]]
    assert fields["weight_bits"]["values"] == {subjects[0]: 16, subjects[1]: 4, subjects[2]: None}
    assert fields["weight_bits"]["differs"]
    assert fields["quantization_method"]["unknown_for"] == [subjects[0], subjects[2]]
    messages = " ".join(w["message"] for w in variants["warnings"])
    assert "Quantization is unknown for unknown" in messages
    assert "never guessed" in messages
    assert "No GPU telemetry" in messages
    assert variants["context_lengths"] == [1024, 2048]
    acc = [c for c in variants["context"] if c["metric_name"] == "context.retrieval_accuracy"]
    assert len(acc) == 2
    assert acc[0]["values"][subjects[1]]["value"] == pytest.approx(2 / 3)
    assert any(m["metric_name"] == "ttft_ms" for m in variants["key_metrics"])
    # Context rows stay out of the main metric table (names repeat per length).
    assert not [m for m in result["metrics"] if m["metric_name"].startswith("context.")]
    html = (await api.get("/api/v1/compare/export", params={"run_ids": ids, "format": "html"})).text
    assert "Deployment variants" in html and "unknown" in html


async def test_failing_telemetry_never_fails_the_run(api, migrated_app, mock_server):
    ds = await generate(api, "needles-t")
    ep = await make_endpoint(api, mock_server, name="bad-telemetry", headers=NEEDLE)
    await api.patch(
        f"/api/v1/endpoints/{ep['id']}",
        json={"telemetry": {"url": "http://127.0.0.1:9/metrics", "timeout_seconds": 0.5}},
    )
    plain = await make_endpoint(api, mock_server, name="no-telemetry", headers=NEEDLE)
    run = await create_run(
        api,
        [ep, plain],
        [ds],
        suites=["context_window"],
        load={"profile": "sequential", "requests": 3},
    )
    assert await execute(migrated_app, run["id"]) == "completed"
    _, failing = await metrics_of(api, run["id"], 0)
    assert failing["telemetry.samples"]["count"]["metric_value"] == 0
    assert failing["telemetry.samples"]["count"]["metadata"]["last_error"]
    gpu = failing["gpu.memory_used_mb"]["max"]
    assert gpu["metric_value"] is None
    assert "no sample" in gpu["metadata"]["unavailable_reason"]
    # Telemetry not configured: no resource metrics at all (absence is not an error).
    _, absent = await metrics_of(api, run["id"], 1)
    assert not [k for k in absent if k.startswith(("gpu.", "telemetry."))]


async def test_quantization_is_not_a_suite(api, mock_server):
    ep = await make_endpoint(api, mock_server, name="q-ep")
    ds = await generate(api, "needles-q")
    resp = await create_run(api, [ep], [ds], expect=422, suites=["quantization"])
    assert "compared across runs" in str(resp)
