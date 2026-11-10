"""Reports, exports, comparisons, and re-runs end to end (milestone 6 acceptance)."""

from __future__ import annotations

import csv
import io
import json

import pytest

from tests.conftest import MOCK_API_KEY
from tests.integration.test_quality_runs import make_ds
from tests.integration.test_runs_api import create_run, execute, make_endpoint

FAST = {"X-Mock-First-Token-Delay-Ms": "0", "X-Mock-Chunk-Delay-Ms": "0"}
CASES = [
    {"id": "q-paris", "type": "qa", "input": "Name the city: Paris", "reference_answer": "Paris"},
    {
        "id": "q-xss",
        "type": "qa",
        "input": "<script>alert('x')</script> what is this?",
        "reference_answer": "Paris",
    },
    {"id": "q-fail", "type": "qa", "input": "boom please", "reference_answer": "Paris"},
]
BODY = {"suites": ["performance", "answer_quality"], "load": {"profile": "sequential"}}


@pytest.fixture(autouse=True)
def fast_progress(settings):
    settings.run_progress_interval_seconds = 0.05


async def finished_run(api, app, endpoints, ds, **body):
    run = await create_run(api, endpoints, [ds], **(BODY | body))
    assert await execute(app, run["id"]) in ("completed", "completed_with_errors")
    return run


async def make_report(api, run_id, expect=201, **body):
    resp = await api.post(f"/api/v1/runs/{run_id}/reports", json=body)
    assert resp.status_code == expect, resp.text
    return resp.json()


async def download(api, report_id, **params):
    resp = await api.get(f"/api/v1/reports/{report_id}/download", params=params)
    assert resp.status_code == 200, resp.text
    return resp


async def test_html_report_is_offline_escaped_and_complete(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server, headers=FAST | {"X-Mock-Fail-If-Contains": "boom"})
    ds = await make_ds(api, "rep-qa", "qa", CASES)
    run = await finished_run(
        api,
        migrated_app,
        [ep],
        ds,
        name="<script>alert(1)</script> bench",
        description="a &amp; <b>b</b>",
    )

    report = await make_report(api, run["id"], format="html")
    assert report["report_type"] == "html"
    assert report["filename"].endswith(".html")
    assert report["report_metadata"]["results_total"] == 4  # 3 requests + 1 retry attempt
    resp = await download(api, report["id"])
    assert resp.headers["content-type"].startswith("text/html")
    assert "attachment" in resp.headers["content-disposition"]
    assert "default-src 'none'" in resp.headers["content-security-policy"]
    assert "sandbox" in resp.headers["content-security-policy"]
    html = resp.text
    assert len(resp.content) == report["size_bytes"]

    # Offline and inert: no scripts, no external resources, model/dataset text escaped.
    assert "<script" not in html.lower()
    assert "&lt;script&gt;alert(1)&lt;/script&gt; bench" in html
    assert "a &amp;amp; &lt;b&gt;b&lt;/b&gt;" in html
    for needle in ('src="http', 'href="http', "@import", "url(http"):
        assert needle not in html
    # Methodology, configuration, charts, failures, reproduction.
    for section in ("Methodology", "Configuration", "Reproduce", "Failures", "All metrics"):
        assert f">{section}</h2>" in html
    assert "<svg" in html and "Latency distribution" in html
    assert ds["current_version"]["content_hash"] in html
    assert "openllm-eval run create --config run.yaml" in html
    assert "q-fail" in html  # the failed request is listed
    assert "linear interpolation" in html  # percentile method stated
    assert MOCK_API_KEY not in html

    listed = (await api.get(f"/api/v1/runs/{run['id']}/reports")).json()
    assert listed["total"] == 1 and listed["items"][0]["run_name"] == run["name"]
    assert (await api.get("/api/v1/reports")).json()["total"] == 1
    inline = await download(api, report["id"], inline="true")
    assert inline.headers["content-disposition"].startswith("inline")


async def test_raw_exports_can_be_inspected_on_their_own(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server, headers=FAST | {"X-Mock-Fail-If-Contains": "boom"})
    ds = await make_ds(api, "rep-qa2", "qa", CASES)
    run = await finished_run(api, migrated_app, [ep], ds)

    data = json.loads(
        (await download(api, (await make_report(api, run["id"], format="json"))["id"])).text
    )
    assert data["run"]["id"] == run["id"]
    assert data["results_total"] == 4 and len(data["results"]) == 4  # incl. the retry
    assert (
        data["configuration"]["datasets"][0]["content_hash"]
        == ds["current_version"]["content_hash"]
    )
    failed = next(r for r in data["results"] if r["case_identifier"] == "q-fail" and r["is_final"])
    assert failed["status"] == "failed" and failed["output_tokens"] is None
    assert any(m["metric_name"] == "latency_ms" for m in data["metrics"])
    assert (
        data["reproduction"]["api_request"]["datasets"][0]["version_id"]
        == ds["current_version"]["id"]
    )

    rows = list(
        csv.DictReader(
            io.StringIO(
                (await download(api, (await make_report(api, run["id"], format="csv"))["id"])).text
            )
        )
    )
    assert len(rows) == 4
    ok = next(r for r in rows if r["case_identifier"] == "q-paris")
    assert float(ok["latency_ms"]) > 0
    bad = next(r for r in rows if r["case_identifier"] == "q-fail" and r["is_final"] == "True")
    assert bad["status"] == "failed" and bad["output_tokens"] == ""  # missing is empty, never 0

    mrows = list(
        csv.DictReader(
            io.StringIO(
                (
                    await download(
                        api, (await make_report(api, run["id"], format="metrics_csv"))["id"]
                    )
                ).text
            )
        )
    )
    assert {"metric_name", "aggregation", "metric_value", "unavailable_reason"} <= set(mrows[0])
    assert any(r["metric_name"] == "quality.exact_match" for r in mrows)

    md = (await download(api, (await make_report(api, run["id"], format="markdown"))["id"])).text
    assert md.startswith("# ") and "## Summary" in md and "```yaml" in md


async def test_redaction_and_report_rules(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server, headers=FAST)
    ds = await make_ds(api, "rep-qa3", "qa", CASES[:1])
    run = await finished_run(api, migrated_app, [ep], ds)
    report = await make_report(
        api, run["id"], format="json", redact_content=True, redact_endpoints=True
    )
    assert report["options"] == {"redact_content": True, "redact_endpoints": True}
    text = (await download(api, report["id"])).text
    assert mock_server.base_url not in text
    assert "Name the city: Paris" not in json.dumps(json.loads(text)["results"])
    data = json.loads(text)
    assert all(r["content"] is None for r in data["results"])
    assert data["models"][0]["endpoint"]["base_url"] == "[redacted]"

    # Reports need a finished run; unknown formats are rejected.
    pending = await create_run(api, [ep], [ds], **BODY)
    await make_report(api, pending["id"], expect=409, format="html")
    await make_report(api, run["id"], expect=422, format="pdf")

    assert (await api.delete(f"/api/v1/reports/{report['id']}")).status_code == 204
    assert (await api.get(f"/api/v1/reports/{report['id']}")).status_code == 404
    # Deleting the run deletes its reports.
    other = await make_report(api, run["id"], format="csv")
    await api.delete(f"/api/v1/runs/{pending['id']}")
    assert (await api.delete(f"/api/v1/runs/{run['id']}")).status_code == 204
    assert (await api.get(f"/api/v1/reports/{other['id']}")).status_code == 404


async def test_rerun_repeats_the_configuration(api, migrated_app, mock_server):
    ep = await make_endpoint(api, mock_server, headers=FAST)
    ds = await make_ds(api, "rep-qa4", "qa", CASES[:2])
    run = await finished_run(
        api, migrated_app, [ep], ds, generation={"temperature": 0, "max_tokens": 16, "seed": 7}
    )
    resp = await api.post(f"/api/v1/runs/{run['id']}/rerun")
    assert resp.status_code == 201, resp.text
    again = resp.json()
    assert again["name"] == "bench (re-run)"
    assert again["id"] != run["id"]
    a, b = run["configuration_snapshot"], again["configuration_snapshot"]
    for key in ("suites", "generation", "datasets", "metrics_version", "prompt_builder_version"):
        assert a[key] == b[key], key
    assert {k: v for k, v in a["load"].items()} == b["load"]
    assert await execute(migrated_app, again["id"]) == "completed"

    comparison = (
        await api.get("/api/v1/compare", params={"run_ids": f"{run['id']},{again['id']}"})
    ).json()
    comp = comparison["comparability"]
    assert comp["overall"] == "equivalent_configuration", comp["differences"]
    assert comparison["transitions"][0]["datasets_identical"] is True


async def test_compare_flags_differences_and_regressions(api, migrated_app, mock_server):
    ds = await make_ds(api, "cmp-qa", "qa", CASES)
    good = await make_endpoint(
        api, mock_server, name="good", headers=FAST | {"X-Mock-Response": "Paris"}
    )
    bad = await make_endpoint(
        api,
        mock_server,
        name="bad",
        headers=FAST | {"X-Mock-Response": "London", "X-Mock-Fail-If-Contains": "Name the city"},
    )
    base = await finished_run(api, migrated_app, [good], ds, name="baseline")
    worse = await finished_run(
        api,
        migrated_app,
        [bad],
        ds,
        name="candidate",
        generation={"temperature": 0, "max_tokens": 64},
    )

    result = (
        await api.get("/api/v1/compare", params={"run_ids": f"{base['id']},{worse['id']}"})
    ).json()
    assert [s["run_name"] for s in result["subjects"]] == ["baseline", "candidate"]
    assert result["baseline_subject_id"] == result["subjects"][0]["id"]
    comp = result["comparability"]
    fields = {d["field"]: d for d in comp["differences"]}
    assert fields["max_tokens"]["severity"] == "major"
    assert comp["overall"] == "partially_controlled"

    exact = next(
        m
        for m in result["metrics"]
        if m["metric_name"] == "quality.exact_match" and m["aggregation"] == "pass_rate"
    )
    assert exact["better"] == "higher"
    b, c = exact["values"]
    assert b["delta"] is None and b["value"] == pytest.approx(1.0)
    assert c["value"] == 0.0 and c["delta"] == pytest.approx(-1.0) and c["improved"] is False
    assert c["relative_change"] == pytest.approx(-1.0)

    t = result["transitions"][0]
    assert t["datasets_identical"] is True
    assert t["new_errors_total"] == 1 and t["new_errors"][0]["case_identifier"] == "q-paris"
    assert set(t["evaluators"]["exact_match"]["pass_to_fail"]) == {"q-xss", "q-fail"}

    # Another dataset makes the quality comparison not directly comparable.
    other_ds = await make_ds(api, "cmp-qa-other", "qa", CASES[:1])
    third = await finished_run(api, migrated_app, [good], other_ds, name="other-data")
    result = (
        await api.get(
            "/api/v1/compare",
            params={"run_ids": f"{base['id']},{third['id']}", "baseline": third["id"]},
        )
    ).json()
    assert result["baseline_subject_id"] == result["subjects"][1]["id"]
    assert result["comparability"]["overall"] == "not_directly_comparable"
    assert result["transitions"][0]["datasets_identical"] is False

    # Saved comparison: computed on read, exportable, deletable.
    resp = await api.post(
        "/api/v1/comparisons",
        json={"name": "good vs bad", "run_ids": [base["id"], worse["id"]]},
    )
    assert resp.status_code == 201, resp.text
    saved = resp.json()
    assert saved["result"]["comparability"]["overall"] == "partially_controlled"
    assert (await api.get("/api/v1/comparisons")).json()["total"] == 1
    detail = (await api.get(f"/api/v1/comparisons/{saved['id']}")).json()
    assert detail["result"]["subjects"][1]["run_name"] == "candidate"
    html = await api.get(f"/api/v1/comparisons/{saved['id']}/export", params={"format": "html"})
    assert html.status_code == 200 and "Partially controlled" in html.text
    assert "<script" not in html.text.lower()
    rows = list(
        csv.reader(
            io.StringIO(
                (
                    await api.get(
                        f"/api/v1/comparisons/{saved['id']}/export", params={"format": "csv"}
                    )
                ).text
            )
        )
    )
    assert rows[0][:4] == ["metric_name", "aggregation", "unit", "better"]
    js = await api.get("/api/v1/compare/export", params={"run_ids": base["id"], "format": "json"})
    assert js.status_code == 200 and json.loads(js.text)["subjects"][0]["run_name"] == "baseline"

    # Deleting a compared run leaves the saved comparison, flagged as unavailable.
    await api.delete(f"/api/v1/runs/{worse['id']}")
    detail = (await api.get(f"/api/v1/comparisons/{saved['id']}")).json()
    assert detail["result"] is None and "deleted" in detail["error"]
    assert (await api.delete(f"/api/v1/comparisons/{saved['id']}")).status_code == 204


async def test_compare_validation(api, migrated_app, mock_server):
    assert (await api.get("/api/v1/compare", params={"run_ids": "nope"})).status_code == 422
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await api.get("/api/v1/compare", params={"run_ids": missing})).status_code == 404
    ep = await make_endpoint(api, mock_server, headers=FAST)
    ds = await make_ds(api, "cmp-v", "qa", CASES[:1])
    run = await finished_run(api, migrated_app, [ep], ds)
    resp = await api.get("/api/v1/compare", params={"run_ids": run["id"], "baseline": missing})
    assert resp.status_code == 422
    resp = await api.post(
        "/api/v1/comparisons", json={"name": "x", "run_ids": [run["id"], run["id"]]}
    )
    assert resp.status_code == 422
