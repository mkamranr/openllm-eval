from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from app.cli import main as cli

runner = CliRunner()

EP = {"id": "11111111-1111-1111-1111-111111111111", "name": "local-vllm"}
DS = {
    "id": "22222222-2222-2222-2222-222222222222",
    "name": "general-qa",
    "current_version": {"id": "33333333-3333-3333-3333-333333333333"},
}
RM = "44444444-4444-4444-4444-444444444444"


def run_body(status="completed"):
    return {
        "id": "55555555-5555-5555-5555-555555555555",
        "name": "nightly",
        "status": status,
        "progress": {"planned": 4, "completed": 4, "succeeded": 4, "failed": 0, "in_flight": 0},
        "models": [
            {"id": RM, "name": "local-vllm", "status": "completed", "progress": {"completed": 4}}
        ],
        "error_summary": None,
    }


METRICS = {
    "live": False,
    "items": [
        {
            "run_model_id": RM,
            "load_step": None,
            "metric_name": n,
            "aggregation": a,
            "metric_value": v,
        }
        for n, a, v in [
            ("requests", "succeeded", 4),
            ("requests", "attempted", 4),
            ("error_rate", "ratio", 0.0),
            ("ttft_ms", "p50", 41.5),
            ("ttft_ms", "p95", None),
            ("latency_ms", "p50", 120.25),
            ("request_throughput", "value", 3.5),
        ]
    ],
}


def patch_api(monkeypatch, routes):
    calls = []

    def fake_request(method, url, timeout=None, **kwargs):
        calls.append((method, url, kwargs))
        for (m, suffix), (status, body) in routes.items():
            if m == method and url.split("?")[0].endswith(suffix):
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": {"message": f"no route {method} {url}"}})

    monkeypatch.setattr(cli.httpx, "request", fake_request)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return calls


def write_config(tmp_path, text):
    path = tmp_path / "bench.yaml"
    path.write_text(text, encoding="utf-8")
    return path


CONFIG = """
name: nightly
endpoints:
  - name: local-vllm
dataset:
  name: general-qa
generation:
  temperature: 0
  max_tokens: 128
  stream: true
load:
  profile: fixed_concurrency
  requests: 4
  concurrency: 2
  warmup_requests: 1
report:
  formats: [html]
"""


def test_run_create_wait_prints_metrics(monkeypatch, tmp_path):
    calls = patch_api(
        monkeypatch,
        {
            ("GET", "/api/v1/endpoints"): (200, {"items": [EP]}),
            ("GET", "/api/v1/datasets"): (200, {"items": [DS]}),
            ("POST", "/api/v1/runs"): (201, run_body("queued")),
            ("GET", f"/api/v1/runs/{run_body()['id']}"): (200, run_body()),
            ("GET", f"/api/v1/runs/{run_body()['id']}/metrics"): (200, METRICS),
            ("POST", f"/api/v1/runs/{run_body()['id']}/reports"): (
                201,
                {"id": "r1", "filename": "nightly.html"},
            ),
        },
    )
    monkeypatch.setattr(
        cli.httpx, "get", lambda url, **kw: httpx.Response(200, content=b"<!doctype html>")
    )
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    result = runner.invoke(
        cli.app,
        [
            "run",
            "create",
            "--config",
            str(write_config(tmp_path, CONFIG)),
            "--wait",
            "--report-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = next(k["json"] for m, u, k in calls if u.endswith("/api/v1/runs"))
    assert payload["endpoint_ids"] == [EP["id"]]
    assert payload["datasets"] == [{"dataset_id": DS["id"]}]
    assert payload["load"] == {
        "profile": "fixed_concurrency",
        "requests": 4,
        "concurrency": 2,
        "warmup_requests": 1,
        "stream": True,
    }
    assert payload["generation"] == {"temperature": 0, "max_tokens": 128}
    assert payload["suites"] == ["performance"]
    assert "41.5" in result.output and "120.2" in result.output and "3.50" in result.output
    assert "n/a" in result.output  # missing values are not shown as 0
    # report.formats from the config are generated and saved after the run.
    assert (out_dir / "nightly.html").read_bytes() == b"<!doctype html>"
    report_post = next(k["json"] for m, u, k in calls if u.endswith("/reports"))
    assert report_post == {"format": "html", "redact_content": False, "redact_endpoints": False}


def test_run_create_wait_exits_1_on_errors(monkeypatch, tmp_path):
    rid = run_body()["id"]
    patch_api(
        monkeypatch,
        {
            ("GET", "/api/v1/endpoints"): (200, {"items": [EP]}),
            ("GET", "/api/v1/datasets"): (200, {"items": [DS]}),
            ("POST", "/api/v1/runs"): (201, run_body("queued")),
            ("GET", f"/api/v1/runs/{rid}"): (200, run_body("completed_with_errors")),
            ("GET", f"/api/v1/runs/{rid}/metrics"): (200, METRICS),
        },
    )
    result = runner.invoke(
        cli.app, ["run", "create", "--config", str(write_config(tmp_path, CONFIG)), "--wait"]
    )
    assert result.exit_code == 1


def test_dataset_path_reuses_identical_version(monkeypatch, tmp_path):
    (tmp_path / "cases.jsonl").write_text('{"id": "a", "input": "hi"}\n', encoding="utf-8")
    config = "endpoints: [local-vllm]\ndatasets:\n  - path: cases.jsonl\n    name: general-qa\n"
    calls = patch_api(
        monkeypatch,
        {
            ("GET", "/api/v1/endpoints"): (200, {"items": [EP]}),
            ("GET", "/api/v1/datasets"): (200, {"items": [DS]}),
            ("POST", f"/api/v1/datasets/{DS['id']}/versions"): (
                409,
                {"error": {"message": "duplicate", "details": {"version_id": "v-existing"}}},
            ),
            ("POST", "/api/v1/runs"): (201, run_body("queued")),
        },
    )
    result = runner.invoke(
        cli.app, ["run", "create", "--config", str(write_config(tmp_path, config))]
    )
    assert result.exit_code == 0, result.output
    payload = next(k["json"] for m, u, k in calls if m == "POST" and u.endswith("/runs"))
    assert payload["datasets"] == [{"dataset_id": DS["id"], "version_id": "v-existing"}]


def test_run_create_reports_validation_errors(monkeypatch, tmp_path):
    patch_api(
        monkeypatch,
        {
            ("GET", "/api/v1/endpoints"): (200, {"items": [EP]}),
            ("GET", "/api/v1/datasets"): (200, {"items": [DS]}),
            ("POST", "/api/v1/runs"): (
                422,
                {
                    "error": {
                        "message": "Request validation failed",
                        "details": [{"loc": ["body", "suites"], "msg": "arrives in Milestone 4"}],
                    }
                },
            ),
        },
    )
    result = runner.invoke(
        cli.app, ["run", "create", "--config", str(write_config(tmp_path, CONFIG))]
    )
    assert result.exit_code == 1
    assert "suites: arrives in Milestone 4" in result.output


def test_bad_configs(monkeypatch, tmp_path):
    patch_api(monkeypatch, {("GET", "/api/v1/endpoints"): (200, {"items": []})})
    bad_key = runner.invoke(
        cli.app, ["run", "create", "--config", str(write_config(tmp_path, "endpoint: x\n"))]
    )
    assert bad_key.exit_code == 1 and "unknown config keys" in bad_key.output
    missing = runner.invoke(
        cli.app, ["run", "create", "--config", str(write_config(tmp_path, "endpoints: [nope]\n"))]
    )
    assert missing.exit_code == 1 and "no endpoint named 'nope'" in missing.output
    not_yaml = runner.invoke(
        cli.app, ["run", "create", "--config", str(write_config(tmp_path, "a: [\n"))]
    )
    assert not_yaml.exit_code == 1 and "invalid YAML" in not_yaml.output


def test_status_list_results_cancel(monkeypatch):
    rid = run_body()["id"]
    patch_api(
        monkeypatch,
        {
            ("GET", f"/api/v1/runs/{rid}"): (200, run_body()),
            ("GET", f"/api/v1/runs/{rid}/metrics"): (200, METRICS),
            ("GET", "/api/v1/runs"): (200, {"items": [run_body()]}),
            ("POST", f"/api/v1/runs/{rid}/cancel"): (200, run_body("cancel_requested")),
        },
    )
    status = runner.invoke(cli.app, ["run", "status", rid])
    assert status.exit_code == 0 and "4/4 requests" in status.output
    listing = runner.invoke(cli.app, ["run", "list"])
    assert rid in listing.output and "nightly" in listing.output
    results = runner.invoke(cli.app, ["run", "results", rid, "--json"])
    assert json.loads(results.output)["items"][0]["metric_value"] == 4
    cancel = runner.invoke(cli.app, ["run", "cancel", rid])
    assert "cancel_requested" in cancel.output


QUALITY_CONFIG = """
name: quality
profile: QA sequential
endpoints: [local-vllm]
dataset: general-qa
evaluation:
  suites: [answer_quality, tool_calling]
  tool_mode: generation_only
  embedding:
    endpoint: local-vllm
    threshold: 0.85
  judge:
    endpoint: local-vllm
    concurrency: 2
"""


def test_run_create_with_profile_evaluation_and_quality_summary(monkeypatch, tmp_path):
    profile = {"id": "66666666-6666-6666-6666-666666666666", "name": "QA sequential"}
    metrics = {
        "live": False,
        "items": METRICS["items"]
        + [
            {
                "run_model_id": RM,
                "load_step": None,
                "metric_name": n,
                "aggregation": a,
                "metric_value": v,
            }
            for n, a, v in [
                ("quality.exact_match", "pass_rate", 0.5),
                ("quality.exact_match", "mean_score", 0.5),
                ("tool.selection_accuracy", "ratio", None),
                ("hallucination.contradiction_rate", "ratio", 0.25),
            ]
        ],
    }
    calls = patch_api(
        monkeypatch,
        {
            ("GET", "/api/v1/endpoints"): (200, {"items": [EP]}),
            ("GET", "/api/v1/datasets"): (200, {"items": [DS]}),
            ("GET", "/api/v1/profiles"): (200, {"items": [profile]}),
            ("POST", "/api/v1/runs"): (201, run_body("queued")),
            ("GET", f"/api/v1/runs/{run_body()['id']}"): (200, run_body()),
            ("GET", f"/api/v1/runs/{run_body()['id']}/metrics"): (200, metrics),
        },
    )
    result = runner.invoke(
        cli.app,
        ["run", "create", "--config", str(write_config(tmp_path, QUALITY_CONFIG)), "--wait"],
    )
    assert result.exit_code == 0, result.output
    payload = next(k["json"] for m, u, k in calls if m == "POST")
    assert payload["profile_id"] == profile["id"]
    assert payload["suites"] == ["answer_quality", "tool_calling"]
    assert payload["evaluation"] == {
        "tool_mode": "generation_only",
        "embedding": {"threshold": 0.85, "endpoint_id": EP["id"]},
        "judge": {"concurrency": 2, "endpoint_id": EP["id"]},
    }
    # Not set in the config, so the profile's values apply.
    assert "load" not in payload and "generation" not in payload
    assert "quality.exact_match" in result.output and "50.0%" in result.output
    assert "tool.selection_accuracy" in result.output
    assert "hallucination.contradiction_rate" in result.output
