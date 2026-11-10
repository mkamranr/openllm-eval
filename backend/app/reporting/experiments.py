"""Report sections for Milestone 7: context-window results and resource telemetry."""

from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

from app.reporting import charts
from app.reporting.charts import fmt
from app.schemas.model_metadata import variant_fields

CONTEXT_COLUMNS = [
    ("Requests", "context.requests", "count", "count"),
    ("Success", "context.success_rate", "ratio", "ratio"),
    ("Overflow", "context.overflow_rate", "ratio", "ratio"),
    ("OOM", "context.oom_rate", "ratio", "ratio"),
    ("Retrieval", "context.retrieval_accuracy", "pass_rate", "ratio"),
    ("Early", "context.retrieval_accuracy", "early", "ratio"),
    ("Middle", "context.retrieval_accuracy", "middle", "ratio"),
    ("Late", "context.retrieval_accuracy", "late", "ratio"),
    ("TTFT p50", "context.ttft_ms", "p50", "ms"),
    ("Latency p50", "context.latency_ms", "p50", "ms"),
]
TOKEN_COLUMNS = [
    ("Estimated input tokens", "context.estimated_input_tokens", "mean", "tokens"),
    ("Server-reported input tokens", "context.input_tokens", "mean", "tokens"),
    ("Actual / estimated", "context.actual_to_estimated", "mean", ""),
    ("Truncation suspected", "context.truncation_suspected", "ratio", "ratio"),
]


def _t(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def context_index(data: dict[str, Any]) -> dict[str, dict[int, dict[tuple[str, str], dict]]]:
    idx: dict[str, dict[int, dict[tuple[str, str], dict]]] = defaultdict(lambda: defaultdict(dict))
    for m in data["metrics"]:
        if m.get("context_length") is not None and m["run_model_id"]:
            idx[m["run_model_id"]][m["context_length"]][(m["metric_name"], m["aggregation"])] = m
    return idx


def _cell(m: dict | None, unit: str) -> str:
    if m is None:
        return '<td class="n muted">—</td>'
    if m["metric_value"] is None:
        reason = (m.get("metadata") or {}).get("unavailable_reason") or "unavailable"
        return f'<td class="n muted" title="{_t(reason)}">—</td>'
    value = m["metric_value"]
    text = f"{value:.2f}" if unit == "" else fmt(value, unit)
    return f'<td class="n" title="n={m["sample_count"]}">{_t(text)}</td>'


def _table(headers: list[str], rows: list[str], label: str) -> str:
    head = "".join(f'<th scope="col">{_t(h)}</th>' for h in headers)
    return (
        f'<div class="scroll"><table aria-label="{_t(label)}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def context_section(data: dict[str, Any]) -> str:
    idx = context_index(data)
    if not idx:
        return ""
    models = data["models"]
    lengths = sorted({n for per in idx.values() for n in per})
    out = [
        '<p class="muted">Grouped by the <em>requested</em> context length of each case. '
        "Estimated sizes use a fixed heuristic (4 characters per token); actual input tokens are "
        "what the server reported, because tokenizers differ. A request that succeeds at a length "
        "does not show the model uses that context: see retrieval accuracy.</p>"
    ]
    series = [
        (
            m["name"],
            m["position"],
            [
                (
                    idx[m["id"]].get(n, {}).get(("context.retrieval_accuracy", "pass_rate")) or {}
                ).get("metric_value")
                for n in lengths
            ],
        )
        for m in models
    ]
    out.append(
        '<div class="multiples">'
        + "<figure>"
        + charts.grouped_bars(
            [f"{n:,}" for n in lengths],
            series,
            "Retrieval accuracy by requested context length",
            "accuracy",
        )
        + "</figure>"
    )
    ttft = [
        (
            m["name"],
            m["position"],
            [
                (idx[m["id"]].get(n, {}).get(("context.ttft_ms", "p50")) or {}).get("metric_value")
                for n in lengths
            ],
        )
        for m in models
    ]
    out.append(
        "<figure>"
        + charts.grouped_bars(
            [f"{n:,}" for n in lengths], ttft, "TTFT p50 by requested context length", "ms", "ms"
        )
        + "</figure></div>"
    )
    for m in models:
        per = idx.get(m["id"])
        if not per:
            continue
        rows = [
            f'<tr><td class="n">{n:,}</td>'
            + "".join(
                _cell(per[n].get((name, agg)), unit) for _, name, agg, unit in CONTEXT_COLUMNS
            )
            + "</tr>"
            for n in sorted(per)
        ]
        token_rows = []
        for n in sorted(per):
            source = (per[n].get(("context.input_tokens", "mean")) or {}).get("metadata", {})
            sources = source.get("token_count_source") or {}
            token_rows.append(
                f'<tr><td class="n">{n:,}</td>'
                + "".join(
                    _cell(per[n].get((name, agg)), unit) for _, name, agg, unit in TOKEN_COLUMNS
                )
                + f"<td>{_t(', '.join(f'{k}: {v}' for k, v in sources.items()) or '—')}</td></tr>"
            )
        out.append(f"<h3>{_t(m['name'])}</h3>")
        out.append(
            _table(
                ["Requested tokens"] + [h for h, *_ in CONTEXT_COLUMNS],
                rows,
                f"Context window results for {m['name']}",
            )
        )
        out.append(
            _table(
                ["Requested tokens"] + [h for h, *_ in TOKEN_COLUMNS] + ["Token count source"],
                token_rows,
                f"Requested, estimated and actual tokens for {m['name']}",
            )
        )
    return "".join(out)


def telemetry_section(data: dict[str, Any]) -> str:
    tel = data.get("telemetry") or {}
    if not tel:
        return ""
    models = [m for m in data["models"] if m["id"] in tel]
    out = [
        '<p class="muted">Sampled from the exporter configured on each endpoint while the model '
        "was measured (offset from the start of the measured phase). Telemetry is best effort and "
        "never affects the run.</p>"
    ]
    charts_html = []
    for key, title, unit in (
        ("gpu_memory_used_mb", "GPU memory used", "MB"),
        ("gpu_utilization_pct", "GPU utilization", "%"),
        ("gpu_power_w", "GPU power", "W"),
        ("kv_cache_usage_pct", "KV cache usage", "%"),
    ):
        series = [
            (
                m["name"],
                m["position"],
                [(s["offset_seconds"], s["values"].get(key)) for s in tel[m["id"]]["samples"]],
            )
            for m in models
        ]
        # A line needs two points; a single sample is shown in the table only.
        if any(sum(y is not None for _, y in pts) >= 2 for _, _, pts in series):
            charts_html.append(
                "<figure>"
                + charts.line_chart(series, f"{title} over time", "seconds", title, unit)
                + "</figure>"
            )
    if charts_html:
        out.append('<div class="multiples">' + "".join(charts_html) + "</div>")
    rows = []
    for m in models:
        status = tel[m["id"]]["status"] or {}
        rows.append(
            f"<tr><td>{_t(m['name'])}</td><td>{len(tel[m['id']]['samples'])}</td>"
            f"<td>{_t(status.get('failures', '—'))}</td>"
            f"<td>{_t(status.get('last_error') or '—')}</td></tr>"
        )
    out.append(
        _table(["Model", "Samples", "Failed scrapes", "Last error"], rows, "Telemetry status")
    )
    return "".join(out)


def variant_pairs(
    model_meta: dict[str, Any], deployment_meta: dict[str, Any]
) -> list[tuple[str, Any]]:
    """Well-known variant fields for the configuration section, unknown ones included."""
    pairs = []
    for row in variant_fields(model_meta, deployment_meta):
        if row["value"] is None and row["key"] not in (
            "quantization_method",
            "weight_precision",
            "weight_bits",
            "kv_cache_dtype",
            "serving_backend",
            "gpu_type",
        ):
            continue
        pairs.append((row["label"], "unknown" if row["value"] is None else row["value"]))
    return pairs
