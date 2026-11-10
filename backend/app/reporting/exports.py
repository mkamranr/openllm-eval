"""Raw exports: JSON (the full collected data), per-request CSV, metrics CSV, and Markdown."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.reporting.charts import fmt
from app.reporting.html import SUMMARY, _metric_index, render_html

RESULT_COLUMNS = [
    "id",
    "model",
    "run_model_id",
    "case_identifier",
    "phase",
    "request_index",
    "attempt_number",
    "pass_number",
    "load_step",
    "is_final",
    "status",
    "error_type",
    "http_status",
    "error_message",
    "latency_ms",
    "ttft_ms",
    "input_tokens",
    "output_tokens",
    "output_tokens_per_second",
    "token_count_source",
    "concurrency",
    "finish_reason",
    "started_at",
    "completed_at",
    "evaluations",
    "content",
]
METRIC_COLUMNS = [
    "model",
    "run_model_id",
    "load_step",
    "metric_name",
    "aggregation",
    "metric_value",
    "metric_unit",
    "sample_count",
    "unavailable_reason",
    "metadata",
]
# Spreadsheet apps execute cells starting with these characters as formulas.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_value(value: Any) -> Any:
    """Empty cell for missing values (never 0); neutralize spreadsheet formulas in text."""
    if value is None:
        return ""
    if isinstance(value, dict | list):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _csv(columns: list[str], rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for r in rows:
        writer.writerow([_csv_value(r.get(c)) for c in columns])
    return buf.getvalue()


def _names(data: dict[str, Any]) -> dict[str | None, str]:
    return {m["id"]: m["name"] for m in data["models"]}


def results_csv(data: dict[str, Any]) -> str:
    names = _names(data)
    rows = []
    for r in data["results"]:
        evals = [
            {k: e[k] for k in ("evaluator", "status", "passed", "score", "label")}
            for e in r["evaluations"]
        ]
        rows.append(r | {"model": names.get(r["run_model_id"]), "evaluations": evals or None})
    return _csv(RESULT_COLUMNS, rows)


def metrics_csv(data: dict[str, Any]) -> str:
    names = _names(data)
    rows = [
        m
        | {
            "model": names.get(m["run_model_id"]),
            "unavailable_reason": (m["metadata"] or {}).get("unavailable_reason"),
        }
        for m in data["metrics"]
    ]
    return _csv(METRIC_COLUMNS, rows)


def _md(value: Any) -> str:
    return str("" if value is None else value).replace("|", "\\|").replace("\n", " ")


def render_markdown(data: dict[str, Any]) -> str:
    run = data["run"]
    idx = _metric_index(data)
    lines = [
        f"# {_md(run['name'])}",
        "",
        f"Run `{run['id']}` · status **{run['status']}** · suites: {', '.join(run['suites'])}",
        f"Generated {data['generated_at']} by OpenLLM-Eval {data['framework_version']}.",
        "",
        "## Warnings",
        "",
        *[f"- {_md(w['message'])}" for w in data["warnings"]],
        "",
        "## Summary",
        "",
        "| Model | " + " | ".join(h for h, _, _ in SUMMARY) + " |",
        "|---|" + "---:|" * len(SUMMARY),
    ]
    for m in data["models"]:
        cells = []
        for _, name, agg in SUMMARY:
            row = idx[m["id"]].get((name, agg))
            cells.append(fmt(row["metric_value"], row["metric_unit"]) if row else "—")
        lines.append(f"| {_md(m['name'])} | " + " | ".join(cells) + " |")
    lines += ["", "— = could not be measured (see the JSON export for the reason).", ""]
    lines += ["## Datasets", ""]
    for d in data["datasets"]:
        lines.append(
            f"- {_md(d.get('name'))} ({d.get('dataset_type')}), version "
            f"{d.get('version_label') or d.get('version_number')}, "
            f"{d.get('record_count')} records, "
            f"sha256 `{d.get('content_hash')}`"
        )
    lines += ["", "## Models", ""]
    for m in data["models"]:
        ep = m["endpoint"]
        lines.append(
            f"- **{_md(m['name'])}**: `{_md(ep.get('model_identifier'))}` "
            f"via {ep.get('adapter_type')}"
            f", generation {json.dumps(ep.get('effective_generation') or {})}"
        )
    quality = sorted(
        {
            k
            for per in idx.values()
            for k in per
            if k[0].startswith(("quality.", "rag.", "hallucination.", "tool."))
        }
    )
    if quality:
        lines += [
            "",
            "## Quality",
            "",
            "| Metric | " + " | ".join(_md(m["name"]) for m in data["models"]) + " |",
        ]
        lines.append("|---|" + "---:|" * len(data["models"]))
        for name, agg in quality:
            cells = []
            for m in data["models"]:
                row = idx[m["id"]].get((name, agg))
                cells.append(fmt(row["metric_value"], row["metric_unit"]) if row else "—")
            lines.append(f"| `{name}` {agg} | " + " | ".join(cells) + " |")
    repro = data["reproduction"]
    lines += [
        "",
        "## Reproduce",
        "",
        f"Save as `run.yaml` and run `{repro['cli_command']}`:",
        "",
        "```yaml",
        repro["cli_config"].rstrip(),
        "```",
        "",
    ]
    return "\n".join(lines)


FORMATS: dict[str, tuple[str, str]] = {
    # format: (media type, file extension)
    "html": ("text/html; charset=utf-8", "html"),
    "json": ("application/json", "json"),
    "csv": ("text/csv; charset=utf-8", "csv"),
    "metrics_csv": ("text/csv; charset=utf-8", "metrics.csv"),
    "markdown": ("text/markdown; charset=utf-8", "md"),
}


def render(data: dict[str, Any], report_format: str) -> bytes:
    if report_format == "html":
        text = render_html(data)
    elif report_format == "json":
        text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    elif report_format == "csv":
        text = results_csv(data)
    elif report_format == "metrics_csv":
        text = metrics_csv(data)
    elif report_format == "markdown":
        text = render_markdown(data)
    else:  # pragma: no cover - validated by the schema
        raise ValueError(report_format)
    return text.encode("utf-8")
