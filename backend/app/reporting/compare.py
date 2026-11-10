"""Run comparison (spec §6.9, §11.4): side-by-side metrics, configuration differences with a
comparability label, and per-case regressions.

Subjects are the models of the selected runs (a run with three models gives three subjects).
Everything is compared against one baseline subject. Differences are descriptive: no
significance test is applied, and a comparison never hides a configuration difference.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppError, NotFoundError
from app.db.models.evaluation import EvaluationResult
from app.db.models.run import (
    PHASE_MEASURED,
    RESULT_SUCCEEDED,
    BenchmarkRun,
    RunAggregateMetric,
    RunCaseResult,
)
from app.reporting.catalog import direction, evaluator_of, family
from app.reporting.exports import _csv_value

MAX_RUNS = 10
MAX_SUBJECTS = 20
MAX_TRANSITIONS = 200

EQUIVALENT = "equivalent_configuration"
PARTIAL = "partially_controlled"
NOT_COMPARABLE = "not_directly_comparable"
_RANK = {EQUIVALENT: 0, PARTIAL: 1, NOT_COMPARABLE: 2}

# (field, label, aspect, severity). aspect: performance | quality | both | subject.
# severity: critical → not directly comparable; major → partially controlled; minor → noted
# only; info → expected to differ between the things being compared (the models).
FIELDS: list[tuple[str, str, str, str]] = [
    ("dataset_hashes", "Dataset content (sha256)", "both", "critical"),
    ("prompt_builder_version", "Prompt builder version", "both", "critical"),
    ("metrics_version", "Performance metrics version", "performance", "critical"),
    ("evaluation_framework_version", "Evaluation framework version", "quality", "critical"),
    ("quality_metrics_version", "Quality metrics version", "quality", "critical"),
    ("evaluators", "Evaluators and versions", "quality", "critical"),
    ("evidence_method", "Evidence method", "quality", "critical"),
    ("judge", "LLM judge (model, prompt version)", "quality", "critical"),
    ("evaluation_config", "Evaluation thresholds and settings", "quality", "critical"),
    ("temperature", "Temperature", "both", "major"),
    ("top_p", "top_p", "both", "major"),
    ("max_tokens", "max_tokens", "both", "major"),
    ("seed", "Seed", "both", "major"),
    ("load_profile", "Load profile", "performance", "major"),
    ("concurrency", "Concurrency", "performance", "major"),
    ("requests", "Measured requests per model", "performance", "major"),
    ("steps", "Load steps", "performance", "major"),
    ("stream", "Streaming", "performance", "major"),
    ("warmup_requests", "Warm-up requests", "performance", "minor"),
    ("request_timeout_seconds", "Request timeout (s)", "performance", "minor"),
    ("max_retries", "Max retries", "performance", "minor"),
    ("suites", "Suites", "quality", "minor"),
    ("model_identifier", "Model identifier", "subject", "info"),
    ("adapter", "Adapter", "subject", "info"),
    ("base_url", "Endpoint URL", "subject", "info"),
    ("declared_model_metadata", "Declared model metadata", "subject", "info"),
    ("hardware_metadata", "Hardware metadata", "subject", "info"),
]


def _strip_endpoint_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_endpoint_ids(v) for k, v in value.items() if k != "endpoint_id"}
    return value


def fingerprint(run: BenchmarkRun, rm) -> dict[str, Any]:
    snap = run.configuration_snapshot
    ep = rm.endpoint_snapshot
    load = snap.get("load") or {}
    evaluation = snap.get("evaluation") or {}
    gen = ep.get("effective_generation") or {}
    rc = ep.get("request_configuration") or {}
    judge = evaluation.get("judge_endpoint")
    return {
        "dataset_hashes": sorted(d.get("content_hash") for d in snap.get("datasets") or []),
        "prompt_builder_version": snap.get("prompt_builder_version"),
        "metrics_version": snap.get("metrics_version"),
        "evaluation_framework_version": evaluation.get("framework_version"),
        "quality_metrics_version": evaluation.get("metrics_version"),
        "evaluators": sorted(
            f"{e['name']}@{e['version']}" for e in evaluation.get("evaluators") or []
        ),
        "evidence_method": evaluation.get("evidence_method"),
        "judge": (
            {
                "model_identifier": judge.get("model_identifier"),
                "prompt_version": evaluation.get("judge_prompt_version"),
            }
            if judge
            else None
        ),
        "evaluation_config": _strip_endpoint_ids(evaluation.get("config") or {}),
        "temperature": gen.get("temperature"),
        "top_p": gen.get("top_p"),
        "max_tokens": gen.get("max_tokens"),
        "seed": gen.get("seed"),
        "load_profile": load.get("profile"),
        "concurrency": load.get("concurrency"),
        "requests": load.get("requests"),
        "steps": [(s.get("concurrency"), s.get("count")) for s in load.get("steps") or []],
        "stream": load.get("stream"),
        "warmup_requests": load.get("warmup_requests"),
        "request_timeout_seconds": load.get("request_timeout_seconds")
        or rc.get("request_timeout_seconds"),
        "max_retries": load.get("max_retries")
        if load.get("max_retries") is not None
        else rc.get("max_retries"),
        "suites": sorted(snap.get("suites") or []),
        "model_identifier": ep.get("model_identifier"),
        "adapter": f"{ep.get('adapter_type')} {ep.get('adapter_version') or ''}".strip(),
        "base_url": ep.get("base_url"),
        "declared_model_metadata": rm.declared_model_metadata or {},
        "hardware_metadata": rm.hardware_metadata or {},
    }


def _label(severities: set[str]) -> str:
    if "critical" in severities:
        return NOT_COMPARABLE
    if "major" in severities:
        return PARTIAL
    return EQUIVALENT


def differences(subjects: list[dict[str, Any]], baseline_id: str) -> dict[str, Any]:
    base = next(s for s in subjects if s["id"] == baseline_id)
    diffs = []
    per_aspect: dict[str, set[str]] = {"performance": set(), "quality": set()}
    for field, label, aspect, severity in FIELDS:
        values = {s["id"]: s["config"][field] for s in subjects}
        if all(v == base["config"][field] for v in values.values()):
            continue
        diffs.append(
            {
                "field": field,
                "label": label,
                "aspect": aspect,
                "severity": severity,
                "values": {k: v for k, v in values.items()},
            }
        )
        if aspect in ("performance", "both"):
            per_aspect["performance"].add(severity)
        if aspect in ("quality", "both"):
            per_aspect["quality"].add(severity)
    labels = {aspect: _label(sev) for aspect, sev in per_aspect.items()}
    overall = max(labels.values(), key=_RANK.__getitem__)
    return {
        "overall": overall,
        "performance": labels["performance"],
        "quality": labels["quality"],
        "differences": diffs,
    }


def _delta(value: float | None, base: float | None) -> tuple[float | None, float | None]:
    if value is None or base is None:
        return None, None
    delta = value - base
    return delta, (delta / abs(base) if base != 0 else None)


async def _load_runs(session: AsyncSession, run_ids: list[uuid.UUID]) -> list[BenchmarkRun]:
    if not run_ids:
        raise AppError("Select at least one run.", code="invalid_comparison", status_code=422)
    if len(set(run_ids)) != len(run_ids):
        raise AppError("Each run may be selected once.", code="invalid_comparison", status_code=422)
    if len(run_ids) > MAX_RUNS:
        raise AppError(
            f"At most {MAX_RUNS} runs can be compared.", code="invalid_comparison", status_code=422
        )
    rows = (
        await session.scalars(
            select(BenchmarkRun)
            .where(BenchmarkRun.id.in_(run_ids))
            .options(selectinload(BenchmarkRun.models), selectinload(BenchmarkRun.datasets))
        )
    ).all()
    by_id = {r.id: r for r in rows}
    missing = [str(i) for i in run_ids if i not in by_id]
    if missing:
        raise NotFoundError("Run not found", details={"run_ids": missing})
    return [by_id[i] for i in run_ids]


async def compare(
    session: AsyncSession,
    run_ids: list[uuid.UUID],
    baseline: uuid.UUID | None = None,
) -> dict[str, Any]:
    """``baseline`` is a run id (its first model) or a run-model id; default: first run."""
    runs = await _load_runs(session, run_ids)
    subjects: list[dict[str, Any]] = []
    for run in runs:
        multi = len(run.models) > 1
        for rm in sorted(run.models, key=lambda m: m.position):
            name = rm.endpoint_snapshot.get("name") or "model"
            subjects.append(
                {
                    "id": str(rm.id),
                    "run_id": str(run.id),
                    "run_name": run.name,
                    "run_status": run.status,
                    "run_completed_at": run.completed_at.isoformat() if run.completed_at else None,
                    "model_name": name,
                    "label": f"{run.name} · {name}" if multi or len(runs) > 1 else name,
                    "index": len(subjects),
                    "config": fingerprint(run, rm),
                }
            )
    if len(subjects) > MAX_SUBJECTS:
        raise AppError(
            f"At most {MAX_SUBJECTS} models can be compared ({len(subjects)} selected).",
            code="invalid_comparison",
            status_code=422,
        )
    if baseline is None:
        baseline_subject = subjects[0]
    else:
        b = str(baseline)
        baseline_subject = next((s for s in subjects if s["id"] == b), None) or next(
            (s for s in subjects if s["run_id"] == b), None
        )
        if baseline_subject is None:
            raise AppError(
                "The baseline must be one of the compared runs or their models.",
                code="invalid_comparison",
                status_code=422,
            )
    base_id = baseline_subject["id"]

    # ---- metrics
    metric_rows = (
        await session.scalars(
            select(RunAggregateMetric).where(
                RunAggregateMetric.run_id.in_(run_ids),
                RunAggregateMetric.load_step.is_(None),
                RunAggregateMetric.run_model_id.is_not(None),
            )
        )
    ).all()
    by_key: dict[tuple[str, str], dict[str, RunAggregateMetric]] = defaultdict(dict)
    for m in metric_rows:
        by_key[(m.metric_name, m.aggregation)][str(m.run_model_id)] = m
    metrics = []
    for (name, agg), per in sorted(
        by_key.items(),
        key=lambda kv: (
            ("performance", "quality", "tool", "rag", "hallucination").index(family(kv[0][0])),
            kv[0],
        ),
    ):
        base_row = per.get(base_id)
        base_value = base_row.metric_value if base_row else None
        better = direction(name, agg)
        values = []
        for s in subjects:
            row = per.get(s["id"])
            value = row.metric_value if row else None
            delta, rel = _delta(value, base_value) if s["id"] != base_id else (None, None)
            improved = None
            if delta is not None and better and delta != 0:
                improved = (delta > 0) == (better == "higher")
            values.append(
                {
                    "subject_id": s["id"],
                    "value": value,
                    "sample_count": row.sample_count if row else None,
                    "unavailable_reason": (
                        (row.metadata_ or {}).get("unavailable_reason")
                        if row
                        else "not measured in this run"
                    )
                    if value is None
                    else None,
                    "delta": delta,
                    "relative_change": rel,
                    "improved": improved,
                }
            )
        unit = next(iter(per.values())).metric_unit
        metrics.append(
            {
                "metric_name": name,
                "aggregation": agg,
                "unit": unit,
                "family": family(name),
                "evaluator": evaluator_of(name),
                "better": better,
                "values": values,
            }
        )

    # ---- per-case transitions (first final measured request per case)
    cases: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)  # subject → case → info
    result_case: dict[uuid.UUID, tuple[str, str]] = {}
    rows = await session.execute(
        select(
            RunCaseResult.id,
            RunCaseResult.run_model_id,
            RunCaseResult.case_identifier,
            RunCaseResult.request_index,
            RunCaseResult.status,
            RunCaseResult.error_type,
        )
        .where(
            RunCaseResult.run_id.in_(run_ids),
            RunCaseResult.phase == PHASE_MEASURED,
            RunCaseResult.is_final.is_(True),
        )
        .order_by(RunCaseResult.request_index)
    )
    for rid, rm_id, case, req, status, err in rows:
        per = cases[str(rm_id)]
        if case not in per:
            per[case] = {"request_index": req, "status": status, "error_type": err, "evals": {}}
            result_case[rid] = (str(rm_id), case)
    ev_rows = await session.execute(
        select(
            EvaluationResult.run_case_result_id,
            EvaluationResult.evaluator_name,
            EvaluationResult.status,
            EvaluationResult.passed,
        ).where(EvaluationResult.run_id.in_(run_ids))
    )
    for rid, evaluator, status, passed in ev_rows:
        key = result_case.get(rid)
        if key and status == "completed" and passed is not None:
            cases[key[0]][key[1]]["evals"][evaluator] = bool(passed)

    base_cases = cases.get(base_id, {})
    transitions = []
    for s in subjects:
        if s["id"] == base_id:
            continue
        mine = cases.get(s["id"], {})
        shared = sorted(set(base_cases) & set(mine))
        by_eval: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: {"pass_to_fail": [], "fail_to_pass": []}
        )
        new_errors, fixed_errors = [], []
        for case in shared:
            b, c = base_cases[case], mine[case]
            b_ok, c_ok = b["status"] == RESULT_SUCCEEDED, c["status"] == RESULT_SUCCEEDED
            if b_ok and not c_ok:
                new_errors.append({"case_identifier": case, "error_type": c["error_type"]})
            elif c_ok and not b_ok:
                fixed_errors.append({"case_identifier": case, "error_type": b["error_type"]})
            for evaluator in set(b["evals"]) & set(c["evals"]):
                if b["evals"][evaluator] and not c["evals"][evaluator]:
                    by_eval[evaluator]["pass_to_fail"].append(case)
                elif c["evals"][evaluator] and not b["evals"][evaluator]:
                    by_eval[evaluator]["fail_to_pass"].append(case)
        transitions.append(
            {
                "subject_id": s["id"],
                "shared_cases": len(shared),
                "only_in_baseline": len(set(base_cases) - set(mine)),
                "only_in_subject": len(set(mine) - set(base_cases)),
                "datasets_identical": s["config"]["dataset_hashes"]
                == baseline_subject["config"]["dataset_hashes"],
                "new_errors_total": len(new_errors),
                "new_errors": new_errors[:MAX_TRANSITIONS],
                "fixed_errors_total": len(fixed_errors),
                "fixed_errors": fixed_errors[:MAX_TRANSITIONS],
                "evaluators": {
                    e: {
                        "pass_to_fail_total": len(v["pass_to_fail"]),
                        "pass_to_fail": v["pass_to_fail"][:MAX_TRANSITIONS],
                        "fail_to_pass_total": len(v["fail_to_pass"]),
                        "fail_to_pass": v["fail_to_pass"][:MAX_TRANSITIONS],
                    }
                    for e, v in sorted(by_eval.items())
                },
            }
        )

    comparability = differences(subjects, base_id)
    notes = [
        "Deltas are differences from the baseline. No statistical significance test is applied.",
        "Per-case transitions use the first measured request of each case and completed "
        "evaluations only.",
    ]
    if any(s["run_status"] != "completed" for s in subjects):
        notes.append("At least one run did not complete successfully; its metrics may be partial.")
    if len(runs) > 1:
        notes.append(
            "Runs were executed at different times; server load, versions, and hardware may "
            "have changed in between."
        )
    return {
        "run_ids": [str(r.id) for r in runs],
        "baseline_subject_id": base_id,
        "subjects": subjects,
        "comparability": comparability,
        "metrics": metrics,
        "transitions": transitions,
        "notes": notes,
    }


def comparison_csv(result: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    subjects = result["subjects"]
    header = ["metric_name", "aggregation", "unit", "better"]
    for s in subjects:
        header += [f"{s['label']} value", f"{s['label']} delta", f"{s['label']} relative_change"]
    w.writerow([_csv_value(h) for h in header])

    def cell(v: Any) -> Any:  # missing → empty cell, never 0
        return _csv_value(v)

    for m in result["metrics"]:
        row = [m["metric_name"], m["aggregation"], m["unit"], m["better"] or ""]
        for v in m["values"]:
            row += [cell(v["value"]), cell(v["delta"]), cell(v["relative_change"])]
        w.writerow(row)
    return buf.getvalue()


def comparison_json(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)
