from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from app.cli import main as cli
from tests.unit.test_cli_runs import patch_api

runner = CliRunner()
RUN = "55555555-5555-5555-5555-555555555555"
RUN2 = "66666666-6666-6666-6666-666666666666"


def comparison():
    def v(sid, value, rel=None):
        return {"subject_id": sid, "value": value, "delta": None, "relative_change": rel}

    return {
        "baseline_subject_id": "a",
        "subjects": [{"id": "a", "label": "base"}, {"id": "b", "label": "candidate"}],
        "comparability": {
            "overall": "partially_controlled",
            "performance": "partially_controlled",
            "quality": "equivalent_configuration",
            "differences": [
                {"label": "Concurrency", "severity": "major", "values": {"a": 1, "b": 8}},
                {"label": "Model identifier", "severity": "info", "values": {"a": "x", "b": "y"}},
            ],
        },
        "metrics": [
            {
                "metric_name": "latency_ms",
                "aggregation": "p50",
                "unit": "ms",
                "values": [v("a", 100.0), v("b", 150.0, 0.5)],
            },
            {
                "metric_name": "quality.exact_match",
                "aggregation": "pass_rate",
                "unit": "ratio",
                "values": [v("a", 1.0), v("b", None)],
            },
        ],
        "transitions": [
            {
                "subject_id": "b",
                "new_errors_total": 2,
                "fixed_errors_total": 0,
                "evaluators": {"exact_match": {"pass_to_fail_total": 3, "fail_to_pass_total": 1}},
            }
        ],
        "notes": ["No statistical significance test is applied."],
    }


def test_report_generate_saves_the_file(monkeypatch, tmp_path):
    calls = patch_api(
        monkeypatch,
        {("POST", f"/api/v1/runs/{RUN}/reports"): (201, {"id": "r1", "filename": "run.md"})},
    )
    got = {}

    def fake_get(url, params=None, timeout=None):
        got["url"] = url
        return httpx.Response(200, content=b"# report")

    monkeypatch.setattr(cli.httpx, "get", fake_get)
    result = runner.invoke(
        cli.app,
        ["report", "generate", RUN, "-f", "markdown", "-o", str(tmp_path), "--redact-content"],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "run.md").read_bytes() == b"# report"
    assert got["url"].endswith("/api/v1/reports/r1/download")
    body = calls[0][2]["json"]
    assert body == {"format": "markdown", "redact_content": True, "redact_endpoints": False}

    bad = runner.invoke(cli.app, ["report", "generate", RUN, "-f", "pdf"])
    assert bad.exit_code == 1 and "unknown report format" in bad.output


def test_report_generate_unfinished_run_is_an_error(monkeypatch):
    patch_api(
        monkeypatch,
        {
            ("POST", f"/api/v1/runs/{RUN}/reports"): (
                409,
                {"error": {"message": "Reports can be generated once the run has finished."}},
            )
        },
    )
    result = runner.invoke(cli.app, ["report", "generate", RUN])
    assert result.exit_code == 1 and "once the run has finished" in result.output


def test_report_list(monkeypatch):
    item = {
        "id": "r1",
        "report_type": "html",
        "size_bytes": 1234,
        "generated_at": "2026-11-10T10:00:00",
        "run_name": "nightly",
    }
    patch_api(monkeypatch, {("GET", "/api/v1/reports"): (200, {"items": [item]})})
    result = runner.invoke(cli.app, ["report", "list"])
    assert result.exit_code == 0 and "nightly" in result.output and "html" in result.output


def test_compare_prints_comparability_and_deltas(monkeypatch):
    calls = patch_api(monkeypatch, {("GET", "/api/v1/compare"): (200, comparison())})
    result = runner.invoke(cli.app, ["compare", RUN, RUN2, "--baseline", RUN2])
    assert result.exit_code == 0, result.output
    assert calls[0][2]["params"] == {"run_ids": f"{RUN},{RUN2}", "baseline": RUN2}
    out = result.output
    assert "partially controlled" in out
    assert "[major] Concurrency: 1, 8" in out
    assert "Model identifier" not in out  # expected differences between models are not warnings
    assert "150.0 (+50%)" in out
    assert "n/a" in out  # missing stays missing
    assert "3 pass->fail" in out and "2 new errors" in out

    js = runner.invoke(cli.app, ["run", "compare", RUN, RUN2, "--json"])
    assert js.exit_code == 0 and json.loads(js.output)["subjects"][1]["label"] == "candidate"


def test_rerun(monkeypatch):
    patch_api(monkeypatch, {("POST", f"/api/v1/runs/{RUN}/rerun"): (201, {"id": RUN2})})
    result = runner.invoke(cli.app, ["run", "rerun", RUN])
    assert result.exit_code == 0 and RUN2 in result.output


def test_dataset_generate_needle(monkeypatch):
    calls = patch_api(
        monkeypatch,
        {
            ("POST", "/api/v1/datasets/generate"): (
                201,
                {"id": "d1", "current_version": {"record_count": 6, "content_hash": "abc"}},
            )
        },
    )
    result = runner.invoke(
        cli.app,
        ["dataset", "generate-needle", "--name", "n", "--lengths", "1024,2048", "--seed", "3"],
    )
    assert result.exit_code == 0, result.output
    assert "6 cases" in result.output
    body = calls[0][2]["json"]
    assert body["params"]["context_lengths"] == [1024, 2048]
    assert body["params"]["depths"] == [0.1, 0.5, 0.9]
    assert body["params"]["seed"] == 3
    bad = runner.invoke(cli.app, ["dataset", "generate-needle", "--name", "n", "--lengths", "x"])
    assert bad.exit_code == 1
