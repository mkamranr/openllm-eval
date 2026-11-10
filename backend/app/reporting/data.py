"""Collect everything a report needs from one finished run (spec §5.13).

The collected data is plain JSON. It is the JSON export as-is, and the HTML, CSV, and Markdown
renderers read only from it, so every number in a report can be traced to the JSON export.
"""

from __future__ import annotations

import copy
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.db.models.evaluation import EvaluationResult
from app.db.models.run import (
    COMPLETED,
    PHASE_MEASURED,
    RESULT_SUCCEEDED,
    BenchmarkRun,
    RunAggregateMetric,
    RunCaseResult,
)

REPORT_VERSION = "1"
MAX_LISTED_FAILURES = 500
MAX_SCATTER_POINTS = 2000
TIMELINE_BUCKETS = 30


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def rerun_request(run: BenchmarkRun) -> dict[str, Any]:
    """A run-creation request that repeats this run's configuration (same endpoints, dataset
    versions, suites, evaluation, generation, and load). Endpoint settings are read from the
    endpoints as they are now, which may differ from the snapshot."""
    snap = run.configuration_snapshot
    load = snap.get("load") or {}
    profile = load.get("profile")
    steps = load.get("steps") or []
    request_load: dict[str, Any] = {
        "profile": profile,
        "warmup_requests": load.get("warmup_requests") or 0,
        "stream": load.get("stream", True),
        "request_timeout_seconds": load.get("request_timeout_seconds"),
        "max_retries": load.get("max_retries"),
    }
    if profile == "ramp":
        request_load["ramp_concurrency"] = [s["concurrency"] for s in steps]
        request_load["requests_per_step"] = steps[0]["count"] if steps else None
    else:
        request_load["concurrency"] = load.get("concurrency") or 1
        if profile == "sustained":
            request_load["duration_seconds"] = load.get("duration_seconds")
            if load.get("requests"):
                request_load["requests"] = load["requests"]
        else:
            request_load["requests"] = load.get("requests")
    if profile == "sequential":
        request_load.pop("concurrency", None)
    evaluation = snap.get("evaluation") or {}
    return {
        "name": f"{run.name} (re-run)"[:200],
        "description": run.description,
        "project_id": str(run.project_id) if run.project_id else None,
        "endpoint_ids": [str(m.endpoint_id) for m in run.models if m.endpoint_id],
        "datasets": [
            {"dataset_id": d["dataset_id"], "version_id": d["version_id"]}
            for d in snap.get("datasets") or []
        ],
        "suites": snap.get("suites") or ["performance"],
        "generation": snap.get("generation") or {},
        "load": {k: v for k, v in request_load.items() if v is not None},
        "evaluation": evaluation.get("config") or {},
    }


def reproduction(run: BenchmarkRun) -> dict[str, Any]:
    body = rerun_request(run)
    snap = run.configuration_snapshot
    evaluation = dict(body["evaluation"])
    names = {str(m.endpoint_id): m.endpoint_snapshot.get("name") for m in run.models}
    for key in ("embedding", "judge"):
        block = evaluation.get(key)
        if isinstance(block, dict) and block.get("endpoint_id"):
            snap_key = f"{key}_endpoint"
            ep = (snap.get("evaluation") or {}).get(snap_key) or {}
            block = dict(block)
            block["endpoint"] = ep.get("name") or block.pop("endpoint_id")
            block.pop("endpoint_id", None)
            evaluation[key] = block
    config = {
        "name": body["name"],
        "endpoints": [names.get(e) or e for e in body["endpoint_ids"]],
        "datasets": [
            {"id": d["dataset_id"], "version": d.get("version_number")}
            for d in snap.get("datasets") or []
        ],
        "evaluation": {"suites": body["suites"], **evaluation},
        "generation": body["generation"],
        "load": body["load"],
    }
    return {
        "cli_config": yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        "cli_command": "openllm-eval run create --config run.yaml --wait",
        "api_request": body,
        "notes": [
            "Re-running uses the same dataset versions (by content hash) and settings; endpoint "
            "request settings are read from the endpoints as they are configured at that time.",
            "Model servers are outside OpenLLM-Eval: the same endpoint name is not proof that "
            "the same weights, quantization, or server version are served.",
        ],
    }


def _redact_snapshot(snap: dict[str, Any], redact_endpoints: bool) -> dict[str, Any]:
    out = copy.deepcopy(snap)
    if redact_endpoints:
        endpoints = list(out.get("endpoints") or [])
        evaluation = out.get("evaluation") or {}
        endpoints += [evaluation.get("embedding_endpoint"), evaluation.get("judge_endpoint")]
        for ep in endpoints:
            if isinstance(ep, dict):
                ep["base_url"] = "[redacted]"
                (ep.get("request_configuration") or {}).pop("headers", None)
    return out


def _metric_row(m: RunAggregateMetric) -> dict[str, Any]:
    return {
        "run_model_id": str(m.run_model_id) if m.run_model_id else None,
        "load_step": m.load_step,
        "metric_name": m.metric_name,
        "aggregation": m.aggregation,
        "metric_value": m.metric_value,
        "metric_unit": m.metric_unit,
        "sample_count": m.sample_count,
        "metadata": m.metadata_ or {},
    }


def _timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Completed measured requests per time bucket, from the model's first measured start."""
    if not rows:
        return []
    start = min(r["_started"] for r in rows)
    end = max(r["_completed"] for r in rows)
    span = max((end - start).total_seconds(), 0.001)
    width = span / TIMELINE_BUCKETS
    buckets = [{"ok": 0, "failed": 0} for _ in range(TIMELINE_BUCKETS)]
    for r in rows:
        i = min(int((r["_completed"] - start).total_seconds() / width), TIMELINE_BUCKETS - 1)
        buckets[i]["ok" if r["status"] == RESULT_SUCCEEDED else "failed"] += 1
    return [
        {
            "t_start_s": round(i * width, 3),
            "t_end_s": round((i + 1) * width, 3),
            "succeeded": b["ok"],
            "failed": b["failed"],
            "request_throughput": b["ok"] / width,
            "error_rate": b["failed"] / (b["ok"] + b["failed"]) if b["ok"] + b["failed"] else None,
        }
        for i, b in enumerate(buckets)
    ]


def _warnings(run: BenchmarkRun, data: dict[str, Any]) -> list[dict[str, str]]:
    snap = run.configuration_snapshot
    load = snap.get("load") or {}
    evaluation = snap.get("evaluation") or {}
    out: list[dict[str, str]] = []

    def warn(level: str, text: str) -> None:
        out.append({"level": level, "message": text})

    if run.status != COMPLETED:
        warn("warning", f"The run finished with status '{run.status}'.")
    if not load.get("stream", True):
        warn("info", "Streaming was off, so time to first token was not measured.")
    if run.pass_count > 1:
        warn(
            "warning",
            f"The run executed in {run.pass_count} passes (resumed or retried). Throughput "
            "needs one contiguous measurement window and may be unavailable.",
        )
    if snap.get("sampling") not in (None, "deterministic"):
        warn(
            "info",
            "Sampling was not greedy (temperature above 0 or unset), so answers vary between "
            "runs.",
        )
    sources = Counter(r["token_count_source"] for r in data["results"] if r["phase"] == "measured")
    if sources and sources.get("server_reported", 0) < sum(sources.values()):
        warn(
            "warning",
            "Some token counts were not reported by the server ("
            + ", ".join(f"{k}: {v}" for k, v in sorted(sources.items()))
            + "); token throughput may be unavailable or estimated.",
        )
    judge = evaluation.get("judge_endpoint")
    if judge and judge.get("judges_tested_model"):
        warn("warning", "The LLM judge is also one of the models under test (self-judging).")
    if judge:
        warn(
            "info",
            f"Evidence verdicts come from an LLM judge ({judge.get('model_identifier')}, prompt "
            f"version {evaluation.get('judge_prompt_version')}). A judge can be wrong; its "
            "confidence is not a measured accuracy.",
        )
    elif evaluation.get("evidence_method") == "lexical":
        warn(
            "info",
            "Evidence checks are lexical (word overlap). Paraphrases are often 'unverifiable' "
            "and need review; contradiction detection covers numbers only.",
        )
    if any(d.get("version_id") is None for d in snap.get("datasets") or []) or any(
        rd.dataset_version_id is None for rd in run.datasets
    ):
        warn("warning", "A dataset version used by this run has since been deleted.")
    unavailable = [
        f"{m['metric_name']}.{m['aggregation']}"
        for m in data["metrics"]
        if m["metric_value"] is None and m["load_step"] is None
    ]
    if unavailable:
        warn(
            "info",
            f"{len(unavailable)} metric(s) could not be measured and are shown as unavailable "
            "(never as 0); hover over them or see the JSON export for the reasons.",
        )
    if data["results_truncated"]:
        warn(
            "warning",
            f"Only the first {len(data['results'])} of {data['results_total']} recorded "
            "attempts are included in this export (REPORT_MAX_RESULTS).",
        )
    for m in data["models"]:
        declared = m.get("declared_model_metadata") or {}
        if not declared.get("quantization"):
            warn(
                "info",
                f"{m['name']}: quantization is not declared, so it is unknown. It is never "
                "inferred from the model name.",
            )
    warn(
        "info",
        "The model name a server reports is not proof of the weights it serves. No "
        "statistical significance test is applied to any difference.",
    )
    return out


async def collect(
    session: AsyncSession,
    run: BenchmarkRun,
    *,
    redact_content: bool = False,
    redact_endpoints: bool = False,
    max_results: int = 50_000,
) -> dict[str, Any]:
    snap = run.configuration_snapshot
    models = []
    for rm in run.models:
        ep = dict(rm.endpoint_snapshot)
        if redact_endpoints:
            ep["base_url"] = "[redacted]"
            (ep.get("request_configuration") or {}).pop("headers", None)
        models.append(
            {
                "id": str(rm.id),
                "position": rm.position,
                "name": ep.get("name"),
                "model_identifier": ep.get("model_identifier"),
                "adapter_type": ep.get("adapter_type"),
                "status": rm.status,
                "started_at": _iso(rm.started_at),
                "completed_at": _iso(rm.completed_at),
                "declared_model_metadata": rm.declared_model_metadata or {},
                "hardware_metadata": rm.hardware_metadata or {},
                "endpoint": ep,
            }
        )

    metric_rows = (
        await session.scalars(
            select(RunAggregateMetric)
            .where(RunAggregateMetric.run_id == run.id)
            .order_by(RunAggregateMetric.metric_name, RunAggregateMetric.aggregation)
        )
    ).all()

    evaluations: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for e in await session.scalars(
        select(EvaluationResult).where(EvaluationResult.run_id == run.id)
    ):
        item = {
            "evaluator": e.evaluator_name,
            "evaluator_version": e.evaluator_version,
            "suite": e.suite,
            "status": e.status,
            "score": e.score,
            "passed": e.passed,
            "label": e.label,
            "rationale": None if redact_content else e.rationale,
        }
        if not redact_content:
            item["evidence"] = e.evidence
        evaluations[e.run_case_result_id].append(item)

    total = 0
    results: list[dict[str, Any]] = []
    series_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stream = await session.stream_scalars(
        select(RunCaseResult)
        .where(RunCaseResult.run_id == run.id)
        .order_by(RunCaseResult.sequence)
        .execution_options(yield_per=1000)
    )
    async for r in stream:
        total += 1
        perf = r.performance_metrics or {}
        response = r.response_data or {}
        row = {
            "id": str(r.id),
            "run_model_id": str(r.run_model_id),
            "case_identifier": r.case_identifier,
            "phase": r.phase,
            "request_index": r.request_index,
            "attempt_number": r.attempt_number,
            "pass_number": r.pass_number,
            "load_step": r.load_step,
            "is_final": r.is_final,
            "status": r.status,
            "error_type": r.error_type,
            "error_message": None if redact_content else r.error_message,
            "http_status": r.http_status,
            "latency_ms": r.latency_ms,
            "ttft_ms": r.ttft_ms,
            "input_tokens": perf.get("input_tokens"),
            "output_tokens": perf.get("output_tokens"),
            "output_tokens_per_second": perf.get("output_tokens_per_second"),
            "token_count_source": perf.get("token_count_source"),
            "prompt_chars": (r.request_metadata or {}).get("prompt_chars"),
            "concurrency": (r.request_metadata or {}).get("concurrency"),
            "finish_reason": response.get("finish_reason"),
            "content": None if redact_content else response.get("content"),
            "content_truncated": response.get("content_truncated"),
            "started_at": _iso(r.started_at),
            "completed_at": _iso(r.completed_at),
            "evaluations": evaluations.get(r.id, []),
        }
        if r.phase == PHASE_MEASURED and r.is_final:
            series_rows[row["run_model_id"]].append(
                row | {"_started": r.started_at, "_completed": r.completed_at}
            )
        if len(results) < max_results:
            results.append(row)

    series = {}
    for rm_id, rows in series_rows.items():
        ok = [r for r in rows if r["status"] == RESULT_SUCCEEDED]
        series[rm_id] = {
            "latency_ms": [r["latency_ms"] for r in ok if r["latency_ms"] is not None],
            "ttft_ms": [r["ttft_ms"] for r in ok if r["ttft_ms"] is not None],
            "output_tokens_per_second": [
                r["output_tokens_per_second"]
                for r in ok
                if r["output_tokens_per_second"] is not None
            ],
            "input_size_vs_latency": [
                {
                    "input_tokens": r["input_tokens"],
                    "prompt_chars": r["prompt_chars"],
                    "latency_ms": r["latency_ms"],
                }
                for r in ok[:MAX_SCATTER_POINTS]
                if r["latency_ms"] is not None
            ],
            "timeline": _timeline(rows),
        }

    failures, quality_failures = [], []
    for r in results:
        if r["phase"] != PHASE_MEASURED or not r["is_final"]:
            continue
        if r["status"] != RESULT_SUCCEEDED and len(failures) < MAX_LISTED_FAILURES:
            failures.append(
                {
                    k: r[k]
                    for k in (
                        "run_model_id",
                        "case_identifier",
                        "request_index",
                        "error_type",
                        "http_status",
                        "error_message",
                    )
                }
            )
        for e in r["evaluations"]:
            if e["status"] in ("completed", "requires_review", "failed") and not e["passed"]:
                if len(quality_failures) < MAX_LISTED_FAILURES:
                    quality_failures.append(
                        {
                            "run_model_id": r["run_model_id"],
                            "case_identifier": r["case_identifier"],
                            "request_index": r["request_index"],
                            "evaluator": e["evaluator"],
                            "status": e["status"],
                            "label": e["label"],
                            "score": e["score"],
                            "rationale": e["rationale"],
                        }
                    )

    data: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "framework_version": __version__,
        "options": {"redact_content": redact_content, "redact_endpoints": redact_endpoints},
        "run": {
            "id": str(run.id),
            "name": run.name,
            "description": run.description,
            "status": run.status,
            "suites": snap.get("suites") or [],
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "completed_at": _iso(run.completed_at),
            "pass_count": run.pass_count,
            "error_summary": run.error_summary,
            "profile": snap.get("profile"),
        },
        "configuration": _redact_snapshot(snap, redact_endpoints),
        "models": models,
        "datasets": snap.get("datasets") or [],
        "metrics": [_metric_row(m) for m in metric_rows],
        "results": results,
        "results_total": total,
        "results_truncated": total > len(results),
        "series": series,
        "failures": failures,
        "quality_failures": quality_failures,
        "reproduction": reproduction(run),
    }
    data["warnings"] = _warnings(run, data)
    return data
