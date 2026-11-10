"""Self-contained HTML report (spec §5.13, §11).

One file with no scripts and no external resources: styles are inline, charts are inline SVG,
and it works offline. Every value from the database is HTML-escaped, so model output and
dataset text can never inject markup. Served with a restrictive Content-Security-Policy.
"""

from __future__ import annotations

import json
from collections import defaultdict
from html import escape
from typing import Any

from app.metrics.stats import PERCENTILE_METHOD
from app.reporting import charts
from app.reporting.catalog import evaluator_of, family
from app.reporting.charts import Series, fmt

CSS = """
:root{color-scheme:light dark;--bg:#fcfcfb;--fg:#0b0b0b;--fg2:#52514e;--line:#e4e3df;
--grid:#eeede9;--card:#ffffff;--warn:#8a5a00;--warn-bg:#fff6df;--info-bg:#eef4fc;
--c0:#2a78d6;--c1:#eb6834;--c2:#1baf7a;--c3:#eda100;--c4:#e87ba4;--c5:#008300;--c6:#6250d6;
--c7:#e34948;--c-other:#8a8984}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--fg:#f0efec;--fg2:#c3c2b7;
--line:#3a3a37;--grid:#2c2c2a;--card:#222221;--warn:#f2c35b;--warn-bg:#3a2f12;--info-bg:#1f2a38;
--c0:#3987e5;--c1:#d95926;--c2:#199e70;--c3:#c98500;--c4:#d55181;--c5:#008300;--c6:#9085e9;
--c7:#e66767;--c-other:#9a998f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 8px;padding-top:8px;
border-top:1px solid var(--line)}h3{font-size:15px;margin:20px 0 6px}
.muted,figcaption,.tk,.al{color:var(--fg2)}
table{border-collapse:collapse;width:100%;margin:8px 0;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
th{font-weight:600}td.n{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto}
code,pre{font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace}
pre{background:var(--card);border:1px solid var(--line);padding:8px;overflow-x:auto;
white-space:pre-wrap;word-break:break-word}
.warnings li{margin:4px 0;padding:6px 10px;border-radius:4px;list-style:none}
.warnings{padding:0}.warning{background:var(--warn-bg)}.info{background:var(--info-bg)}
.badge{display:inline-block;padding:0 8px;border-radius:10px;border:1px solid var(--line);
font-size:12px}
.chart{width:100%;height:auto;max-width:720px;display:block}
.chart text{font-size:11px}.tk{fill:var(--fg2)}.al{fill:var(--fg2);font-size:11px}
.pl{fill:var(--fg);font-size:11px}
.gr{stroke:var(--grid);stroke-width:1}.ax{stroke:var(--line);stroke-width:1}
.hit{fill:transparent}.ring{stroke:var(--bg);stroke-width:2}.na{fill:var(--line)}
.multiples{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:8px}
figure{margin:0}.panel figcaption{font-size:12px}
.key{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;
vertical-align:-1px}
.legend{display:flex;flex-wrap:wrap;gap:12px;padding:0;margin:4px 0;list-style:none;
font-size:12px;color:var(--fg2)}
details{margin:8px 0}summary{cursor:pointer;color:var(--fg2)}
.empty{color:var(--fg2);font-style:italic}
nav.toc{font-size:13px}nav.toc a{color:inherit;margin-right:12px}
@media print{details{display:block}details>*{display:block}}
"""


def _t(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _metric_index(data: dict[str, Any]) -> dict[str | None, dict[tuple[str, str], dict]]:
    idx: dict[str | None, dict[tuple[str, str], dict]] = defaultdict(dict)
    for m in data["metrics"]:
        if m["load_step"] is None:
            idx[m["run_model_id"]][(m["metric_name"], m["aggregation"])] = m
    return idx


def _cell(m: dict | None) -> str:
    if m is None:
        return '<td class="n muted">—</td>'
    reason = (m.get("metadata") or {}).get("unavailable_reason")
    if m["metric_value"] is None:
        return f'<td class="n muted" title="{_t(reason or "unavailable")}">—</td>'
    return f'<td class="n" title="n={m["sample_count"]}">{_t(fmt(m["metric_value"], m["metric_unit"]))}</td>'


def _table(headers: list[str], rows: list[list[str]], label: str, numeric_from: int = 1) -> str:
    head = "".join(
        f'<th{" class=n" if i >= numeric_from else ""} scope="col">{_t(h)}</th>'
        for i, h in enumerate(headers)
    )
    body = "".join("<tr>" + "".join(r) + "</tr>" for r in rows)
    return (
        f'<div class="scroll"><table aria-label="{_t(label)}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


SUMMARY = [
    ("Succeeded", "requests", "succeeded"),
    ("Error rate", "error_rate", "ratio"),
    ("Latency p50", "latency_ms", "p50"),
    ("Latency p95", "latency_ms", "p95"),
    ("TTFT p50", "ttft_ms", "p50"),
    ("Output tok/s (mean)", "output_tokens_per_second", "mean"),
    ("Request throughput", "request_throughput", "value"),
    ("Output token throughput", "output_token_throughput", "value"),
]


def primary_evaluator(data: dict[str, Any]) -> str | None:
    """The quality evaluator with a pass rate for the most models (first by name on ties)."""
    counts: dict[str, int] = defaultdict(int)
    for m in data["metrics"]:
        ev = evaluator_of(m["metric_name"])
        if ev and m["aggregation"] == "pass_rate" and m["metric_value"] is not None:
            counts[ev] += 1
    return min(counts, key=lambda e: (-counts[e], e)) if counts else None


def _charts(data: dict[str, Any], idx) -> str:
    models = data["models"]
    series = data["series"]
    out: list[str] = []

    def hist(key: str, title: str, unit: str) -> str:
        return charts.histogram(
            [
                Series(m["name"], m["position"], series.get(m["id"], {}).get(key, []))
                for m in models
            ],
            title,
            unit,
        )

    out.append("<h3>Latency distribution</h3>" + hist("latency_ms", "Latency", "ms"))
    out.append("<h3>Time to first token distribution</h3>" + hist("ttft_ms", "TTFT", "ms"))
    out.append(
        "<h3>Output tokens per second distribution</h3>"
        + hist("output_tokens_per_second", "Output tokens/s", "tokens/s")
    )

    def timeline(key: str) -> list:
        return [
            (
                m["name"],
                m["position"],
                [
                    ((b["t_start_s"] + b["t_end_s"]) / 2, b[key])
                    for b in series.get(m["id"], {}).get("timeline", [])
                ],
            )
            for m in models
        ]

    out.append(
        "<h3>Throughput over time</h3>"
        + charts.line_chart(
            timeline("request_throughput"),
            "Successful requests per second over time",
            "seconds since the model's first measured request",
            "requests/s",
            "requests/s",
        )
        + '<p class="muted">Bucketed by completion time; each model is measured separately, so '
        "time starts at 0 for each.</p>"
    )
    out.append(
        "<h3>Error rate over time</h3>"
        + charts.line_chart(
            timeline("error_rate"),
            "Error rate over time",
            "seconds since the model's first measured request",
            "error rate",
            "ratio",
        )
    )

    evaluators = sorted(
        {
            evaluator_of(m["metric_name"])
            for m in data["metrics"]
            if evaluator_of(m["metric_name"]) and m["aggregation"] == "pass_rate"
        }
    )
    if evaluators:
        out.append(
            "<h3>Quality pass rate by evaluator</h3>"
            + charts.grouped_bars(
                evaluators,
                [
                    (
                        m["name"],
                        m["position"],
                        [
                            (idx[m["id"]].get((f"quality.{e}", "pass_rate")) or {}).get(
                                "metric_value"
                            )
                            for e in evaluators
                        ],
                    )
                    for m in models
                ],
                "Quality pass rate by evaluator",
                "pass rate",
            )
        )
        primary = primary_evaluator(data)
        for x_key, x_agg, x_label, x_unit in (
            (
                ("latency_ms", "p50", "latency p50 (ms)", "ms"),
                ("request_throughput", "value", "request throughput (requests/s)", "requests/s"),
            )
            if primary
            else ()
        ):
            pts = []
            for m in models:
                x = (idx[m["id"]].get((x_key, x_agg)) or {}).get("metric_value")
                y = (idx[m["id"]].get((f"quality.{primary}", "pass_rate")) or {}).get(
                    "metric_value"
                )
                if x is not None and y is not None:
                    pts.append((m["name"], m["position"], x, y))
            title = f"Quality ({primary}) vs {x_label}"
            out.append(
                f"<h3>{_t(title)}</h3>"
                + charts.scatter(pts, title, x_label, f"{primary} pass rate", x_unit, "ratio")
            )

    panels = []
    for m in models:
        rows = series.get(m["id"], {}).get("input_size_vs_latency", [])
        use_tokens = any(r["input_tokens"] is not None for r in rows)
        key = "input_tokens" if use_tokens else "prompt_chars"
        pts = [
            ("request", m["position"], r[key], r["latency_ms"]) for r in rows if r[key] is not None
        ]
        label = "input tokens" if use_tokens else "prompt characters"
        panels.append(
            f'<figure class="panel"><figcaption>{_t(m["name"])}</figcaption>'
            + charts.scatter(
                pts, f"{label} vs latency: {m['name']}", label, "latency (ms)", "", "ms", False
            )
            + "</figure>"
        )
    out.append('<h3>Input size vs latency</h3><div class="multiples">' + "".join(panels) + "</div>")
    return "".join(out)


def _all_metrics(data: dict[str, Any], idx) -> str:
    models = data["models"]
    keys = sorted(
        {k for per in idx.values() for k in per},
        key=lambda k: (
            ("performance", "quality", "tool", "rag", "hallucination").index(family(k[0])),
            k,
        ),
    )
    rows = []
    for name, agg in keys:
        unit = next((idx[m][(name, agg)]["metric_unit"] for m in idx if (name, agg) in idx[m]), "")
        rows.append(
            [f"<td><code>{_t(name)}</code></td>", f"<td>{_t(agg)}</td>", f"<td>{_t(unit)}</td>"]
            + [_cell(idx[m["id"]].get((name, agg))) for m in models]
        )
    return _table(
        ["Metric", "Aggregation", "Unit"] + [m["name"] for m in models], rows, "All metrics", 3
    )


def _kv(pairs: list[tuple[str, Any]]) -> str:
    rows = "".join(
        f"<tr><th scope=row>{_t(k)}</th><td>{_t(v if not isinstance(v, dict | list) else json.dumps(v, ensure_ascii=False))}</td></tr>"
        for k, v in pairs
    )
    return f"<table>{rows}</table>"


def _configuration(data: dict[str, Any]) -> str:
    snap = data["configuration"]
    out = ["<h3>Models</h3>"]
    for m in data["models"]:
        ep = m["endpoint"]
        gen = ep.get("effective_generation") or {}
        out.append(
            f"<h4>{_t(m['name'])}</h4>"
            + _kv(
                [
                    ("Model identifier", ep.get("model_identifier")),
                    ("Adapter", f"{ep.get('adapter_type')} {ep.get('adapter_version') or ''}"),
                    ("Base URL", ep.get("base_url")),
                    ("Effective generation", gen),
                    ("Declared model metadata", m["declared_model_metadata"] or "none declared"),
                    ("Hardware metadata", m["hardware_metadata"] or "none declared"),
                    ("Status", m["status"]),
                ]
            )
        )
    out.append("<h3>Datasets</h3>")
    out.append(
        _table(
            ["Dataset", "Type", "Version", "Records", "Content hash (sha256)"],
            [
                [
                    f"<td>{_t(d.get('name'))}</td>",
                    f"<td>{_t(d.get('dataset_type'))}</td>",
                    f"<td class=n>{_t(d.get('version_label') or d.get('version_number'))}</td>",
                    f"<td class=n>{_t(d.get('record_count'))}</td>",
                    f"<td><code>{_t(d.get('content_hash'))}</code></td>",
                ]
                for d in data["datasets"]
            ],
            "Datasets",
            2,
        )
    )
    load = snap.get("load") or {}
    out.append("<h3>Load</h3>")
    out.append(
        _kv(
            [
                ("Profile", load.get("profile")),
                ("Concurrency", load.get("concurrency")),
                ("Measured requests per model", load.get("requests")),
                ("Warm-up requests", load.get("warmup_requests")),
                ("Streaming", load.get("stream")),
                ("Duration (s)", load.get("duration_seconds")),
                ("Steps", load.get("steps")),
                ("Case order", load.get("case_order")),
                ("Endpoint order", load.get("endpoint_order")),
            ]
        )
    )
    out.append("<h3>Generation</h3>" + _kv(list((snap.get("generation") or {}).items())))
    evaluation = snap.get("evaluation") or {}
    if evaluation.get("evaluators"):
        out.append("<h3>Evaluators</h3>")
        out.append(
            _table(
                ["Evaluator", "Version", "Suite", "Kind"],
                [
                    [
                        f"<td>{_t(e['name'])}</td>",
                        f"<td>{_t(e['version'])}</td>",
                        f"<td>{_t(e['suite'])}</td>",
                        f"<td>{_t(e['kind'])}</td>",
                    ]
                    for e in evaluation["evaluators"]
                ],
                "Evaluators",
                9,
            )
        )
        out.append(
            "<h3>Evaluation settings</h3>" + _kv(list((evaluation.get("config") or {}).items()))
        )
    out.append(
        "<details><summary>Full configuration snapshot (JSON)</summary><pre>"
        + _t(json.dumps(snap, indent=2, ensure_ascii=False, default=str))
        + "</pre></details>"
    )
    return "".join(out)


def _methodology(data: dict[str, Any]) -> str:
    snap = data["configuration"]
    evaluation = snap.get("evaluation") or {}
    items = [
        "Each endpoint is measured on its own, one after another, never concurrently with another "
        "endpoint. Warm-up requests are excluded from every metric.",
        "A request's outcome is its final attempt. Error rate = failed requests / requests with an "
        "outcome; retries never hide failures.",
        "Latency is measured client-side from sending the request to the last byte. TTFT is the "
        "time to the first streamed content token (streaming runs only).",
        "Latency, TTFT, and token metrics use successful requests only.",
        f"Percentiles: {PERCENTILE_METHOD}.",
        "Throughput = successful requests (or tokens) / measurement window, from the first measured "
        "request sent to the last response received, including client-side queueing. It is not "
        "available when the run executed in more than one pass.",
        "Token counts come from the server's usage report when present; the source is recorded per "
        "request (server_reported, tokenizer_estimated, unavailable).",
        "Values that could not be measured are shown as — with the reason, never as 0.",
        "Differences between models are descriptive; no statistical significance test is applied.",
        f"Metrics version {snap.get('metrics_version')}, prompt builder version "
        f"{snap.get('prompt_builder_version')}, evaluation framework version "
        f"{evaluation.get('framework_version') or '—'}, quality metrics version "
        f"{evaluation.get('metrics_version') or '—'}.",
    ]
    if evaluation.get("evidence_method"):
        items.append(
            f"Evidence verdicts: {evaluation['evidence_method']}"
            + (
                f" (judge prompt version {evaluation.get('judge_prompt_version')})."
                if evaluation.get("judge_endpoint")
                else " (lexical overlap; contradictions detected for numbers only)."
            )
        )
    return "<ul>" + "".join(f"<li>{_t(i)}</li>" for i in items) + "</ul>"


def _failures(data: dict[str, Any]) -> str:
    names = {m["id"]: m["name"] for m in data["models"]}
    out = []
    if data["failures"]:
        out.append(f"<h3>Failed requests ({len(data['failures'])} listed)</h3>")
        out.append(
            _table(
                ["Model", "Case", "Request", "Error type", "HTTP", "Message"],
                [
                    [
                        f"<td>{_t(names.get(f['run_model_id']))}</td>",
                        f"<td><code>{_t(f['case_identifier'])}</code></td>",
                        f"<td class=n>{_t(f['request_index'])}</td>",
                        f"<td>{_t(f['error_type'])}</td>",
                        f"<td class=n>{_t(f['http_status'])}</td>",
                        f"<td>{_t((f['error_message'] or '')[:300])}</td>",
                    ]
                    for f in data["failures"]
                ],
                "Failed requests",
                9,
            )
        )
    if data["quality_failures"]:
        out.append(f"<h3>Quality failures ({len(data['quality_failures'])} listed)</h3>")
        out.append(
            _table(
                ["Model", "Case", "Request", "Evaluator", "Status", "Label", "Score", "Rationale"],
                [
                    [
                        f"<td>{_t(names.get(f['run_model_id']))}</td>",
                        f"<td><code>{_t(f['case_identifier'])}</code></td>",
                        f"<td class=n>{_t(f['request_index'])}</td>",
                        f"<td>{_t(f['evaluator'])}</td>",
                        f"<td>{_t(f['status'])}</td>",
                        f"<td>{_t(f['label'])}</td>",
                        f"<td class=n>{_t(fmt(f['score']))}</td>",
                        f"<td>{_t((f['rationale'] or '')[:300])}</td>",
                    ]
                    for f in data["quality_failures"]
                ],
                "Quality failures",
                9,
            )
        )
    if not out:
        return '<p class="empty">No failed requests or failed quality checks.</p>'
    return "".join(out)


def render_html(data: dict[str, Any]) -> str:
    run = data["run"]
    idx = _metric_index(data)
    models = data["models"]
    summary_rows = [
        [
            f'<td><span class="key" style="background:{charts.color(m["position"])}"></span>{_t(m["name"])}</td>'
        ]
        + [_cell(idx[m["id"]].get((name, agg))) for _, name, agg in SUMMARY]
        for m in models
    ]
    warnings = "".join(
        f'<li class="{_t(w["level"])}">{_t(w["message"])}</li>' for w in data["warnings"]
    )
    repro = data["reproduction"]
    opts = data["options"]
    redactions = [k.replace("redact_", "") for k, v in opts.items() if v]
    sections = [
        ("summary", "Summary"),
        ("charts", "Charts"),
        ("metrics", "All metrics"),
        ("failures", "Failures"),
        ("configuration", "Configuration"),
        ("methodology", "Methodology"),
        ("reproduce", "Reproduce"),
    ]
    toc = "".join(f'<a href="#{s}">{_t(t)}</a>' for s, t in sections)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<meta name="generator" content="OpenLLM-Eval {_t(data['framework_version'])}">
<title>{_t(run['name'])} · OpenLLM-Eval report</title><style>{CSS}</style></head>
<body><main>
<h1>{_t(run['name'])}</h1>
<p class="muted">Run <code>{_t(run['id'])}</code> · <span class="badge">{_t(run['status'])}</span>
· suites: {_t(', '.join(run['suites']))} · started {_t(run['started_at'] or '—')} · completed
{_t(run['completed_at'] or '—')}</p>
<p class="muted">Report generated {_t(data['generated_at'])} by OpenLLM-Eval
{_t(data['framework_version'])} (report format {_t(data['report_version'])}).
{('Redacted: ' + _t(', '.join(redactions)) + '.') if redactions else ''}</p>
{f'<p>{_t(run["description"])}</p>' if run.get('description') else ''}
<nav class="toc">{toc}</nav>
<ul class="warnings">{warnings}</ul>
<h2 id="summary">Summary</h2>
{_table(['Model'] + [h for h, _, _ in SUMMARY], summary_rows, 'Summary')}
<p class="muted">— means the value could not be measured; hover for the reason.</p>
<h2 id="charts">Charts</h2>
{_charts(data, idx)}
<h2 id="metrics">All metrics</h2>
{_all_metrics(data, idx)}
<h2 id="failures">Failures</h2>
{_failures(data)}
<h2 id="configuration">Configuration</h2>
{_configuration(data)}
<h2 id="methodology">Methodology</h2>
{_methodology(data)}
<h2 id="reproduce">Reproduce</h2>
<p>Save as <code>run.yaml</code> and run <code>{_t(repro['cli_command'])}</code>:</p>
<pre>{_t(repro['cli_config'])}</pre>
<details><summary>API request body (POST /api/v1/runs)</summary>
<pre>{_t(json.dumps(repro['api_request'], indent=2, ensure_ascii=False))}</pre></details>
<ul>{''.join(f'<li class="muted">{_t(n)}</li>' for n in repro['notes'])}</ul>
</main></body></html>
"""
