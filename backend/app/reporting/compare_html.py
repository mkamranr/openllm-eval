"""Self-contained HTML export of a run comparison (same offline rules as the run report)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app import __version__
from app.reporting import charts
from app.reporting.charts import fmt
from app.reporting.html import CSS, _t

LABELS = {
    "equivalent_configuration": "Equivalent configuration",
    "partially_controlled": "Partially controlled",
    "not_directly_comparable": "Not directly comparable",
}


def _value(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, dict | list):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _signed(v: float | None, unit: str) -> str:
    if v is None:
        return ""
    return ("+" if v > 0 else "") + fmt(v, unit)


def render_comparison_html(result: dict[str, Any], title: str = "Run comparison") -> str:
    subjects = result["subjects"]
    base_id = result["baseline_subject_id"]
    comp = result["comparability"]
    head = "".join(
        f'<th scope="col"><span class="key" style="background:{charts.color(s["index"])}"></span>'
        f'{_t(s["label"])}{" (baseline)" if s["id"] == base_id else ""}</th>'
        for s in subjects
    )
    diff_rows = "".join(
        f"<tr><td>{_t(d['label'])}</td><td>{_t(d['severity'])}</td><td>{_t(d['aspect'])}</td>"
        + "".join(f"<td>{_t(_value(d['values'].get(s['id'])))}</td>" for s in subjects)
        + "</tr>"
        for d in comp["differences"]
    )
    metric_rows = []
    for m in result["metrics"]:
        cells = []
        for v in m["values"]:
            if v["value"] is None:
                cells.append(f'<td class="n muted" title="{_t(v["unavailable_reason"])}">—</td>')
                continue
            delta = _signed(v["delta"], m["unit"]) if v["delta"] else ""
            rel = (
                f" ({'+' if v['relative_change'] > 0 else ''}{v['relative_change'] * 100:.1f}%)"
                if v["relative_change"] is not None
                else ""
            )
            mark = {True: " ▲ better", False: " ▼ worse"}.get(v["improved"], "")
            extra = f'<br><span class="muted">{_t(delta + rel + mark)}</span>' if delta else ""
            cells.append(f'<td class="n">{_t(fmt(v["value"], m["unit"]))}{extra}</td>')
        metric_rows.append(
            f"<tr><td><code>{_t(m['metric_name'])}</code></td><td>{_t(m['aggregation'])}</td>"
            f"<td>{_t(m['better'] or '')}</td>{''.join(cells)}</tr>"
        )
    names = {s["id"]: s["label"] for s in subjects}
    trans = []
    for t in result["transitions"]:
        items = [
            f"{t['shared_cases']} shared cases; datasets "
            + ("identical" if t["datasets_identical"] else "differ"),
            f"new errors: {t['new_errors_total']}, fixed errors: {t['fixed_errors_total']}",
        ]
        for ev, v in t["evaluators"].items():
            items.append(
                f"{ev}: {v['pass_to_fail_total']} pass→fail"
                + (f" ({', '.join(v['pass_to_fail'][:20])})" if v["pass_to_fail"] else "")
                + f", {v['fail_to_pass_total']} fail→pass"
            )
        trans.append(
            f"<h3>{_t(names.get(t['subject_id']))} vs baseline</h3><ul>"
            + "".join(f"<li>{_t(i)}</li>" for i in items)
            + "</ul>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>{_t(title)} · OpenLLM-Eval</title><style>{CSS}</style></head>
<body><main>
<h1>{_t(title)}</h1>
<p class="muted">Generated {_t(datetime.now().astimezone().isoformat())} by OpenLLM-Eval {_t(__version__)}.</p>
<h2>Comparability</h2>
<p>Overall: <span class="badge">{_t(LABELS[comp['overall']])}</span> · performance:
<span class="badge">{_t(LABELS[comp['performance']])}</span> · quality:
<span class="badge">{_t(LABELS[comp['quality']])}</span></p>
<div class="scroll"><table aria-label="Configuration differences"><thead><tr><th>Setting</th>
<th>Severity</th><th>Affects</th>{head}</tr></thead><tbody>{diff_rows or '<tr><td colspan="99" class="muted">No configuration differences.</td></tr>'}</tbody></table></div>
<h2>Metrics</h2>
<div class="scroll"><table aria-label="Metrics"><thead><tr><th>Metric</th><th>Aggregation</th>
<th>Better</th>{head}</tr></thead><tbody>{''.join(metric_rows)}</tbody></table></div>
<h2>Per-case changes</h2>
{''.join(trans) or '<p class="empty">Only one model selected.</p>'}
<h2>Notes</h2><ul>{''.join(f'<li>{_t(n)}</li>' for n in result['notes'])}</ul>
</main></body></html>
"""
