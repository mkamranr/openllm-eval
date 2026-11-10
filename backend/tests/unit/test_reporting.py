from __future__ import annotations

import csv
import io
from itertools import pairwise

from app.reporting import charts
from app.reporting.catalog import direction, evaluator_of, family
from app.reporting.compare import FIELDS, NOT_COMPARABLE, PARTIAL, differences
from app.reporting.exports import metrics_csv, results_csv
from app.reporting.html import primary_evaluator, render_html


def test_fmt_never_turns_missing_into_zero():
    assert charts.fmt(None) == "—"
    assert charts.fmt(0.0) == "0"
    assert charts.fmt(30.0, "count") == "30"
    assert charts.fmt(0.5) == "0.50"
    assert charts.fmt(0.256, "ratio") == "25.6%"
    assert charts.fmt(1234.5, "ms") == "1,234 ms" or charts.fmt(1234.5, "ms") == "1,235 ms"
    assert charts.fmt(12.34, "ms") == "12.3 ms"


def test_nice_ticks_cover_the_range():
    ticks = charts.nice_ticks(0, 87)
    assert ticks[0] == 0 and ticks[-1] >= 87
    assert all(b > a for a, b in pairwise(ticks))
    assert charts.nice_ticks(0, 1.0, 4)[-1] == 1.0


def test_colors_follow_position_and_never_cycle():
    assert charts.color(0) == "var(--c0)"
    assert charts.color(7) == "var(--c7)"
    assert charts.color(8) == charts.color(12) == "var(--c-other)"


def test_charts_escape_labels_and_handle_empty_data():
    svg = charts.scatter([("<b>m</b>", 0, 10.0, 0.5)], "t", "x", "y", "ms", "ratio")
    assert "<b>" not in svg and "&lt;b&gt;m&lt;/b&gt;" in svg
    assert "No data" in charts.histogram([charts.Series("a", 0, [])], "Latency", "ms")
    bars = charts.grouped_bars(["exact"], [("a", 0, [None]), ("b", 1, [0.5])], "q", "pass rate")
    assert "unavailable" in bars  # missing value is marked, not drawn as 0
    line = charts.line_chart([("a", 0, [(1, 2.0), (2, None), (3, 4.0)])], "t", "x", "y")
    assert line.count("<polyline") == 2  # None breaks the line


def test_catalog_directions_and_families():
    assert direction("latency_ms", "p95") == "lower"
    assert direction("request_throughput", "value") == "higher"
    assert direction("quality.exact_match", "pass_rate") == "higher"
    assert direction("hallucination.contradiction_rate", "ratio") == "lower"
    assert direction("requests", "failed") == "lower"
    assert direction("input_tokens", "mean") is None
    assert family("quality.exact_match") == "quality"
    assert family("rag.claim_faithfulness") == "rag"
    assert family("latency_ms") == "performance"
    assert evaluator_of("quality.exact_match") == "exact_match"
    assert evaluator_of("quality.output_consistency") is None


def _subject(sid, **overrides):
    config = {field: None for field, *_ in FIELDS} | overrides
    return {"id": sid, "config": config}


def test_comparability_labels_by_severity():
    same = differences([_subject("a"), _subject("b")], "a")
    assert same["overall"] == "equivalent_configuration" and same["differences"] == []

    models_differ = differences(
        [_subject("a", model_identifier="x"), _subject("b", model_identifier="y")], "a"
    )
    assert models_differ["overall"] == "equivalent_configuration"
    assert models_differ["differences"][0]["severity"] == "info"

    gen = differences([_subject("a", max_tokens=16), _subject("b", max_tokens=64)], "a")
    assert gen["overall"] == PARTIAL and gen["performance"] == PARTIAL and gen["quality"] == PARTIAL

    judge = differences([_subject("a", judge=None), _subject("b", judge={"m": 1})], "a")
    assert judge["quality"] == NOT_COMPARABLE and judge["performance"] == "equivalent_configuration"
    assert judge["overall"] == NOT_COMPARABLE

    load = differences([_subject("a", concurrency=1), _subject("b", concurrency=8)], "a")
    assert load["performance"] == PARTIAL and load["quality"] == "equivalent_configuration"


def _data(**overrides):
    model = {
        "id": "m1",
        "position": 0,
        "name": "<img src=x onerror=alert(1)>",
        "model_identifier": "mock",
        "adapter_type": "openai_compatible",
        "status": "completed",
        "declared_model_metadata": {},
        "hardware_metadata": {},
        "endpoint": {"name": "e", "base_url": "http://h", "effective_generation": {}},
    }
    metric = {
        "run_model_id": "m1",
        "load_step": None,
        "metric_name": "latency_ms",
        "aggregation": "p50",
        "metric_value": None,
        "metric_unit": "ms",
        "sample_count": 0,
        "metadata": {"unavailable_reason": "no <successful> requests"},
    }
    result = {
        "id": "r1",
        "run_model_id": "m1",
        "case_identifier": "c1",
        "phase": "measured",
        "request_index": 0,
        "attempt_number": 1,
        "pass_number": 1,
        "load_step": None,
        "is_final": True,
        "status": "failed",
        "error_type": "http_status",
        "error_message": '=HYPERLINK("http://evil")',
        "http_status": 500,
        "latency_ms": None,
        "output_tokens": None,
        "content": "</pre><script>alert(1)</script>",
        "evaluations": [],
    }
    data = {
        "report_version": "1",
        "generated_at": "2026-11-10T00:00:00+00:00",
        "framework_version": "0.0.1",
        "options": {"redact_content": False, "redact_endpoints": False},
        "run": {
            "id": "run1",
            "name": "run <1>",
            "description": None,
            "status": "completed",
            "suites": ["performance"],
            "started_at": None,
            "completed_at": None,
        },
        "configuration": {"load": {}, "generation": {}, "evaluation": {}, "datasets": []},
        "models": [model],
        "datasets": [],
        "metrics": [metric],
        "results": [result],
        "results_total": 1,
        "results_truncated": False,
        "series": {},
        "failures": [
            {
                "run_model_id": "m1",
                "case_identifier": "c1",
                "request_index": 0,
                "error_type": "http_status",
                "http_status": 500,
                "error_message": "<script>x</script>",
            }
        ],
        "quality_failures": [],
        "warnings": [{"level": "info", "message": "<i>w</i>"}],
        "reproduction": {
            "cli_config": "name: x\n",
            "cli_command": "openllm-eval run create --config run.yaml --wait",
            "api_request": {},
            "notes": [],
        },
    }
    return data | overrides


def test_html_escapes_everything_and_shows_unavailable():
    html = render_html(_data())
    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "<i>w</i>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert 'title="no &lt;successful&gt; requests">—</td>' in html


def test_csv_neutralizes_formulas_and_leaves_missing_empty():
    rows = list(csv.DictReader(io.StringIO(results_csv(_data()))))
    assert rows[0]["error_message"].startswith("'=")
    assert rows[0]["latency_ms"] == "" and rows[0]["output_tokens"] == ""
    assert rows[0]["model"] == "<img src=x onerror=alert(1)>"
    m = list(csv.DictReader(io.StringIO(metrics_csv(_data()))))
    assert m[0]["metric_value"] == "" and m[0]["unavailable_reason"] == "no <successful> requests"


def test_primary_evaluator_prefers_widest_coverage():
    def pr(model, ev, value):
        return {
            "run_model_id": model,
            "load_step": None,
            "metric_name": f"quality.{ev}",
            "aggregation": "pass_rate",
            "metric_value": value,
        }

    data = {
        "metrics": [
            pr("a", "zeta", 1.0),
            pr("b", "zeta", 0.5),
            pr("a", "alpha", 1.0),
            pr("b", "alpha", None),
        ]
    }
    assert primary_evaluator(data) == "zeta"
    assert primary_evaluator({"metrics": []}) is None
