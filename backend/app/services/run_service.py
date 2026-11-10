"""Benchmark run business logic (spec §5.4, §9.5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import __version__
from app.adapters.registry import adapter_classes
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.db.models import Dataset, DatasetVersion, ModelEndpoint
from app.db.models.evaluation import EvaluationProfile, EvaluationResult
from app.db.models.run import (
    ACTIVE_STATUSES,
    CANCEL_REQUESTED,
    CANCELLED,
    COMPLETED_WITH_ERRORS,
    FAILED,
    PENDING_STATUSES,
    QUEUED,
    RESULT_FAILED,
    TERMINAL_STATUSES,
    BenchmarkRun,
    ResourceSample,
    RunAggregateMetric,
    RunCaseResult,
    RunDataset,
    RunModel,
)
from app.evaluators.judge import JUDGE_PROMPT_VERSION
from app.evaluators.registry import (
    EVALUATION_FRAMEWORK_VERSION,
    JUDGE_SUITES,
    QUALITY_SUITES,
    all_evaluators,
    evaluators_for,
)
from app.execution.engine import compute_metric_rows
from app.execution.plan import PlanError, resolve_load
from app.execution.prompting import PROMPT_BUILDER_VERSION
from app.metrics.performance import METRICS_VERSION
from app.metrics.quality import QUALITY_METRICS_VERSION
from app.schemas.common import Page
from app.schemas.endpoint import RequestConfiguration
from app.schemas.evaluation import EvaluationConfig, EvaluationResultRead, EvaluationSummary
from app.schemas.run import (
    CaseResultDetail,
    CaseResultRead,
    GenerationConfig,
    LoadConfig,
    MetricRead,
    ResourceSampleRead,
    RunCreate,
    RunDatasetRead,
    RunDetail,
    RunMetrics,
    RunModelRead,
    RunRead,
    RunSummary,
    RunTelemetry,
)
from app.services.project_service import get_project

SNAPSHOT_SCHEMA_VERSION = "1"
LIST_PREVIEW_CHARS = 300


def utcnow() -> datetime:
    return datetime.now(UTC)


def _conflict(message: str, code: str = "conflict", **details: Any) -> AppError:
    return AppError(message, code=code, status_code=409, details=details or None)


def _invalid(message: str, field: str) -> AppError:
    return AppError(
        message,
        code="validation_error",
        status_code=422,
        details=[{"loc": ["body", field], "msg": message, "type": "value_error"}],
    )


# --------------------------------------------------------------------------------- snapshots


def sampling_mode(temperatures: list[float | None]) -> str:
    """How deterministic the run's sampling settings are (spec §5.4)."""
    if any(t is None for t in temperatures):
        return "server_default"
    return "greedy" if all(t == 0 for t in temperatures) else "stochastic"


def endpoint_snapshot(
    endpoint: ModelEndpoint, load: LoadConfig, generation: GenerationConfig
) -> dict[str, Any]:
    """Secret-free endpoint snapshot. The secret *reference* is kept so the worker can resolve
    it at request time; resolved values never leave the adapter."""
    rc = RequestConfiguration.model_validate(endpoint.request_configuration or {})
    overrides: dict[str, Any] = {"stream": load.stream}
    if load.request_timeout_seconds is not None:
        overrides["request_timeout_seconds"] = load.request_timeout_seconds
    if load.max_retries is not None:
        overrides["max_retries"] = load.max_retries
    rc = rc.model_copy(update=overrides)
    cls = adapter_classes().get(endpoint.adapter_type)
    effective = {
        "temperature": generation.temperature
        if generation.temperature is not None
        else rc.temperature,
        "top_p": generation.top_p if generation.top_p is not None else rc.top_p,
        "max_tokens": generation.max_tokens if generation.max_tokens is not None else rc.max_tokens,
        "seed": generation.seed,
    }
    return {
        "endpoint_id": str(endpoint.id),
        "name": endpoint.name,
        "adapter_type": endpoint.adapter_type,
        "adapter_version": cls.version if cls else None,
        "base_url": endpoint.base_url,
        "model_identifier": endpoint.model_identifier,
        "secret_reference": endpoint.secret_reference,
        "request_configuration": rc.model_dump(mode="json"),
        "effective_generation": effective,
        "model_metadata": endpoint.model_metadata or {},
        "deployment_metadata": endpoint.deployment_metadata or {},
        "telemetry": endpoint.telemetry_configuration,
        "tags": list(endpoint.tags or []),
        "last_test_status": endpoint.last_test_status,
        "captured_at": utcnow().isoformat(),
    }


# ------------------------------------------------------------------------------------ reads


def _dataset_reads(run: BenchmarkRun) -> list[RunDatasetRead]:
    snaps = run.configuration_snapshot.get("datasets", [])
    out = []
    for ref, snap in zip(run.datasets, snaps, strict=False):
        out.append(
            RunDatasetRead(
                dataset_id=ref.dataset_id,
                dataset_version_id=ref.dataset_version_id,
                name=snap.get("name", ""),
                dataset_type=snap.get("dataset_type", ""),
                version_number=snap.get("version_number", 0),
                content_hash=ref.content_hash,
                record_count=ref.record_count,
                available=ref.dataset_version_id is not None,
            )
        )
    return out


def _model_read(rm: RunModel) -> RunModelRead:
    snap = rm.endpoint_snapshot
    return RunModelRead(
        id=rm.id,
        position=rm.position,
        endpoint_id=rm.endpoint_id,
        name=snap.get("name", ""),
        model_identifier=snap.get("model_identifier", ""),
        adapter_type=snap.get("adapter_type", ""),
        base_url=snap.get("base_url", ""),
        status=rm.status,
        progress=rm.progress or {},
        started_at=rm.started_at,
        completed_at=rm.completed_at,
        declared_model_metadata=rm.declared_model_metadata or {},
        hardware_metadata=rm.hardware_metadata or {},
    )


def to_read(run: BenchmarkRun) -> RunRead:
    snap = run.configuration_snapshot
    return RunRead(
        id=run.id,
        project_id=run.project_id,
        name=run.name,
        description=run.description,
        status=run.status,
        suites=snap.get("suites", []),
        framework_version=run.framework_version,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        cancel_requested_at=run.cancel_requested_at,
        error_summary=run.error_summary,
        progress=run.progress or {},
        pass_count=run.pass_count,
        retry_failed_requested=run.retry_failed_requested,
        load=snap.get("load", {}),
        models=[_model_read(m) for m in run.models],
        datasets=_dataset_reads(run),
    )


def to_detail(run: BenchmarkRun) -> RunDetail:
    return RunDetail(**to_read(run).model_dump(), configuration_snapshot=run.configuration_snapshot)


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> BenchmarkRun:
    run = await session.scalar(
        select(BenchmarkRun)
        .where(BenchmarkRun.id == run_id)
        .options(selectinload(BenchmarkRun.models), selectinload(BenchmarkRun.datasets))
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise NotFoundError("Run not found")
    return run


async def list_runs(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None,
    status: list[str] | None,
    q: str | None,
    limit: int,
    offset: int,
) -> Page[RunRead]:
    conditions = []
    if project_id is not None:
        conditions.append(BenchmarkRun.project_id == project_id)
    if status:
        conditions.append(BenchmarkRun.status.in_(status))
    if q:
        conditions.append(BenchmarkRun.name.ilike(f"%{q}%"))
    total = await session.scalar(select(func.count()).select_from(BenchmarkRun).where(*conditions))
    rows = (
        await session.scalars(
            select(BenchmarkRun)
            .where(*conditions)
            .options(selectinload(BenchmarkRun.models), selectinload(BenchmarkRun.datasets))
            .order_by(BenchmarkRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(items=[to_read(r) for r in rows], total=total or 0, limit=limit, offset=offset)


async def summary(session: AsyncSession) -> RunSummary:
    rows = await session.execute(
        select(BenchmarkRun.status, func.count()).group_by(BenchmarkRun.status)
    )
    by_status = {status: n for status, n in rows}
    return RunSummary(
        total=sum(by_status.values()),
        by_status=by_status,
        queued=sum(by_status.get(s, 0) for s in PENDING_STATUSES),
        active=sum(by_status.get(s, 0) for s in ACTIVE_STATUSES),
    )


# ----------------------------------------------------------------------------------- create


def suite_case_types(suite: str) -> set[str]:
    return {t for e in all_evaluators() if e.suite == suite for t in e.case_types}


async def apply_profile(
    session: AsyncSession, data: RunCreate
) -> tuple[RunCreate, EvaluationProfile | None]:
    """Fill suites, evaluation, generation, and load from the profile when the request leaves
    them out. Fields the request sets explicitly always win."""
    if data.profile_id is None:
        return data, None
    profile = await session.get(EvaluationProfile, data.profile_id)
    if profile is None:
        raise _invalid("The evaluation profile does not exist.", "profile_id")
    values = data.model_dump()
    from_profile = {
        "suites": profile.suites,
        "evaluation": profile.evaluator_configuration,
        "generation": profile.generation_configuration,
        "load": profile.load_configuration,
    }
    for key, value in from_profile.items():
        if key not in data.model_fields_set:
            values[key] = value
    try:
        return RunCreate.model_validate(values), profile
    except ValueError as exc:
        raise _invalid(
            f"The profile {profile.name!r} is no longer valid: {exc}", "profile_id"
        ) from None


async def create_run(session: AsyncSession, settings: Settings, data: RunCreate) -> BenchmarkRun:
    data, profile = await apply_profile(session, data)
    if data.project_id is not None:
        await get_project(session, data.project_id)
    if len(data.endpoint_ids) > settings.run_max_endpoints:
        raise _invalid(f"At most {settings.run_max_endpoints} endpoints per run.", "endpoint_ids")

    endpoints = (
        await session.scalars(select(ModelEndpoint).where(ModelEndpoint.id.in_(data.endpoint_ids)))
    ).all()
    by_id = {e.id: e for e in endpoints}
    for eid in data.endpoint_ids:
        endpoint = by_id.get(eid)
        if endpoint is None:
            raise _invalid(f"Endpoint {eid} does not exist.", "endpoint_ids")
        if not endpoint.enabled:
            raise _invalid(f"Endpoint {endpoint.name!r} is disabled.", "endpoint_ids")

    dataset_snaps: list[dict[str, Any]] = []
    versions: list[DatasetVersion] = []
    for ref in data.datasets:
        dataset = await session.get(Dataset, ref.dataset_id)
        if dataset is None:
            raise _invalid(f"Dataset {ref.dataset_id} does not exist.", "datasets")
        version_id = ref.version_id or dataset.current_version_id
        version = await session.get(DatasetVersion, version_id) if version_id else None
        if version is None or version.dataset_id != dataset.id:
            raise _invalid(f"Dataset {dataset.name!r} has no such version.", "datasets")
        versions.append(version)
        dataset_snaps.append(
            {
                "dataset_id": str(dataset.id),
                "name": dataset.name,
                "dataset_type": dataset.dataset_type,
                "version_id": str(version.id),
                "version_number": version.version_number,
                "version_label": version.version_label,
                "content_hash": version.content_hash,
                "record_count": version.record_count,
                "schema_version": version.schema_version,
            }
        )
    case_count = sum(v.record_count for v in versions)
    present_types = {t for v in versions for t, n in (v.type_counts or {}).items() if n}
    quality_suites = [s for s in data.suites if s in QUALITY_SUITES]
    for suite in quality_suites:
        wanted = suite_case_types(suite)
        if not wanted & present_types:
            raise _invalid(
                f"The {suite!r} suite evaluates {', '.join(sorted(wanted))} cases; the selected "
                f"datasets contain only {', '.join(sorted(present_types)) or 'no'} cases.",
                "suites",
            )
    embedding_snapshot = None
    if quality_suites and data.evaluation.embedding is not None:
        if "answer_quality" not in quality_suites:
            raise _invalid(
                "Embedding similarity belongs to the answer_quality suite.", "evaluation"
            )
        emb = await session.get(ModelEndpoint, data.evaluation.embedding.endpoint_id)
        if emb is None or not emb.enabled:
            raise _invalid("The embedding endpoint does not exist or is disabled.", "evaluation")
        embedding_snapshot = endpoint_snapshot(emb, LoadConfig(stream=False), GenerationConfig())
    judge_snapshot = None
    if data.evaluation.judge is not None:
        if not set(quality_suites) & set(JUDGE_SUITES):
            raise _invalid(
                "The LLM judge is used by the rag and hallucination suites; select one of them "
                "or remove the judge.",
                "evaluation",
            )
        jc = data.evaluation.judge
        judge_ep = await session.get(ModelEndpoint, jc.endpoint_id)
        if judge_ep is None or not judge_ep.enabled:
            raise _invalid("The judge endpoint does not exist or is disabled.", "evaluation")
        judge_snapshot = endpoint_snapshot(
            judge_ep,
            LoadConfig(stream=False),
            GenerationConfig(temperature=jc.temperature, max_tokens=jc.max_tokens),
        )
        judge_snapshot["judges_tested_model"] = str(judge_ep.id) in {
            str(e) for e in data.endpoint_ids
        }
    try:
        load = resolve_load(
            data.load,
            case_count=case_count,
            max_requests=settings.run_max_requests,
            max_concurrency=settings.run_max_concurrency,
            max_duration_seconds=settings.run_max_duration_seconds,
        )
    except PlanError as exc:
        raise _invalid(str(exc), "load") from None

    ep_snaps = [
        endpoint_snapshot(by_id[eid], data.load, data.generation) for eid in data.endpoint_ids
    ]
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "framework_version": __version__,
        "metrics_version": METRICS_VERSION,
        "prompt_builder_version": PROMPT_BUILDER_VERSION,
        "suites": data.suites,
        "generation": data.generation.model_dump(mode="json"),
        "sampling": sampling_mode([s["effective_generation"]["temperature"] for s in ep_snaps]),
        "load": load,
        "datasets": dataset_snaps,
        "endpoints": ep_snaps,
        "evaluation": evaluation_snapshot(
            quality_suites, data.evaluation, embedding_snapshot, judge_snapshot
        ),
        "profile": {"id": str(profile.id), "name": profile.name} if profile else None,
        "created_at": utcnow().isoformat(),
    }
    run = BenchmarkRun(
        project_id=data.project_id,
        name=data.name,
        description=data.description,
        status=QUEUED,
        configuration_snapshot=snapshot,
        framework_version=__version__,
        progress={},
        profile_id=profile.id if profile else None,
    )
    run.datasets = [
        RunDataset(
            position=i,
            dataset_version_id=v.id,
            dataset_id=v.dataset_id,
            content_hash=v.content_hash,
            record_count=v.record_count,
        )
        for i, v in enumerate(versions)
    ]
    run.models = [
        RunModel(
            position=i,
            endpoint_id=by_id[eid].id,
            endpoint_snapshot=snap,
            declared_model_metadata=by_id[eid].model_metadata or {},
            hardware_metadata=by_id[eid].deployment_metadata or {},
            status="pending",
            progress={},
        )
        for i, (eid, snap) in enumerate(zip(data.endpoint_ids, ep_snaps, strict=True))
    ]
    session.add(run)
    await session.commit()
    return await get_run(session, run.id)


def evaluation_snapshot(
    suites: list[str],
    config: EvaluationConfig,
    embedding_endpoint: dict[str, Any] | None,
    judge_endpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What evaluates this run, frozen at creation: evaluator names and versions, settings,
    and the (secret-free) embedding and judge endpoints."""
    cfg = config.model_dump(mode="json")
    if not suites:
        return {"suites": [], "evaluators": [], "config": cfg}
    return {
        "framework_version": EVALUATION_FRAMEWORK_VERSION,
        "metrics_version": QUALITY_METRICS_VERSION,
        "suites": suites,
        "config": cfg,
        "evaluators": [
            {"name": e.name, "version": e.version, "suite": e.suite, "kind": e.kind}
            for e in evaluators_for(suites, cfg)
        ],
        "tool_execution": "simulated (fixture results; nothing is executed)"
        if "tool_calling" in suites and cfg["tool_mode"] == "simulated"
        else (
            "generation only (calls are inspected, not answered)"
            if "tool_calling" in suites
            else None
        ),
        "embedding_endpoint": embedding_endpoint,
        "judge_endpoint": judge_endpoint,
        "judge_prompt_version": JUDGE_PROMPT_VERSION if judge_endpoint else None,
        "evidence_method": (
            ("judge" if judge_endpoint else "lexical") if set(suites) & set(JUDGE_SUITES) else None
        ),
    }


# ---------------------------------------------------------------------------------- control


async def cancel_run(session: AsyncSession, run_id: uuid.UUID) -> BenchmarkRun:
    run = await get_run(session, run_id)
    if run.status in TERMINAL_STATUSES:
        raise _conflict(f"The run already finished ({run.status}).", "run_finished")
    now = utcnow()
    if run.status in PENDING_STATUSES:
        # Not started: nothing to stop. (A worker's claim requires status 'queued'.)
        run.status = CANCELLED
        run.completed_at = now
        run.cancel_requested_at = now
        for rm in run.models:
            rm.status = "cancelled"
    elif run.status != CANCEL_REQUESTED:
        run.status = CANCEL_REQUESTED
        run.cancel_requested_at = now
    await session.commit()
    return await get_run(session, run_id)


async def retry_failed(session: AsyncSession, run_id: uuid.UUID) -> BenchmarkRun:
    """Queue the run again to retry failed requests and finish any that never completed.

    New attempts are added as new rows (attempt numbers continue); earlier attempts are kept.
    """
    run = await get_run(session, run_id)
    if run.status not in (COMPLETED_WITH_ERRORS, CANCELLED, FAILED):
        raise _conflict(
            "Only runs that completed with errors, were cancelled, or failed can be retried.",
            "run_not_retryable",
        )
    run.status = QUEUED
    run.retry_failed_requested = True
    run.completed_at = None
    run.cancel_requested_at = None
    run.error_summary = None
    await session.commit()
    return await get_run(session, run_id)


async def delete_run(session: AsyncSession, run_id: uuid.UUID) -> None:
    run = await get_run(session, run_id)
    if run.status in ACTIVE_STATUSES:
        raise _conflict("Cancel the run before deleting it.", "run_active")
    await session.delete(run)
    await session.commit()


async def active_runs_using_datasets(session: AsyncSession, dataset_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count(func.distinct(BenchmarkRun.id)))
        .select_from(BenchmarkRun)
        .join(RunDataset, RunDataset.run_id == BenchmarkRun.id)
        .where(
            RunDataset.dataset_id == dataset_id,
            BenchmarkRun.status.in_((*PENDING_STATUSES, *ACTIVE_STATUSES)),
        )
    )
    return count or 0


async def active_runs_in_project(session: AsyncSession, project_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(BenchmarkRun)
        .where(
            BenchmarkRun.project_id == project_id,
            BenchmarkRun.status.in_((*PENDING_STATUSES, *ACTIVE_STATUSES)),
        )
    )
    return count or 0


# ---------------------------------------------------------------------------------- results


async def _evaluations(
    session: AsyncSession, result_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[EvaluationResult]]:
    out: dict[uuid.UUID, list[EvaluationResult]] = {}
    if not result_ids:
        return out
    rows = await session.scalars(
        select(EvaluationResult)
        .where(EvaluationResult.run_case_result_id.in_(result_ids))
        .order_by(EvaluationResult.created_at, EvaluationResult.id)
    )
    for r in rows:
        out.setdefault(r.run_case_result_id, []).append(r)
    return out


def _evaluation_read(r: EvaluationResult) -> EvaluationResultRead:
    return EvaluationResultRead(
        id=r.id,
        evaluator_name=r.evaluator_name,
        evaluator_version=r.evaluator_version,
        suite=r.suite,
        status=r.status,
        score=r.score,
        passed=r.passed,
        label=r.label,
        rationale=r.rationale,
        evidence=r.evidence or [],
        metadata=r.metadata_ or {},
        created_at=r.created_at,
    )


def _result_read(row: RunCaseResult, preview: bool) -> CaseResultRead:
    data = dict(row.response_data or {})
    truncated = False
    if preview:
        for key in ("content", "reasoning_content"):
            text = data.get(key)
            if isinstance(text, str) and len(text) > LIST_PREVIEW_CHARS:
                data[key] = text[:LIST_PREVIEW_CHARS]
                truncated = True
        if data.get("tool_calls"):
            data["tool_calls"] = [
                {"name": t.get("name"), "arguments_error": t.get("arguments_error")}
                for t in data["tool_calls"]
            ]
            truncated = True
    return CaseResultRead(
        id=row.id,
        run_model_id=row.run_model_id,
        dataset_case_id=row.dataset_case_id,
        case_identifier=row.case_identifier,
        sequence=row.sequence,
        phase=row.phase,  # type: ignore[arg-type]
        request_index=row.request_index,
        attempt_number=row.attempt_number,
        pass_number=row.pass_number,
        load_step=row.load_step,
        is_final=row.is_final,
        is_warmup=row.is_warmup,
        status=row.status,  # type: ignore[arg-type]
        error_type=row.error_type,
        error_message=row.error_message,
        http_status=row.http_status,
        latency_ms=row.latency_ms,
        ttft_ms=row.ttft_ms,
        request_metadata=row.request_metadata or {},
        response_data=data,
        performance_metrics=row.performance_metrics or {},
        started_at=row.started_at,
        completed_at=row.completed_at,
        truncated=truncated,
    )


async def list_results(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    run_model_id: uuid.UUID | None,
    status: str | None,
    phase: str | None,
    final_only: bool,
    q: str | None,
    limit: int,
    offset: int,
    evaluator: str | None = None,
    evaluation: str | None = None,
) -> Page[CaseResultRead]:
    await get_run(session, run_id)
    conditions: list[Any] = [RunCaseResult.run_id == run_id]
    if run_model_id is not None:
        conditions.append(RunCaseResult.run_model_id == run_model_id)
    if status is not None:
        conditions.append(RunCaseResult.status == status)
    if phase is not None:
        conditions.append(RunCaseResult.phase == phase)
    if final_only:
        conditions.append(RunCaseResult.is_final.is_(True))
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(
            or_(
                RunCaseResult.case_identifier.ilike(f"%{escaped}%", escape="\\"),
                RunCaseResult.error_type.ilike(f"%{escaped}%", escape="\\"),
            )
        )
    if evaluator is not None or evaluation is not None:
        # Results with at least one matching evaluation (e.g. evaluation=failed: any failed check).
        sub = [EvaluationResult.run_case_result_id == RunCaseResult.id]
        if evaluator is not None:
            sub.append(EvaluationResult.evaluator_name == evaluator)
        if evaluation == "passed":
            sub += [EvaluationResult.status == "completed", EvaluationResult.passed.is_(True)]
        elif evaluation == "failed":
            sub += [EvaluationResult.status == "completed", EvaluationResult.passed.is_(False)]
        elif evaluation is not None:
            sub.append(EvaluationResult.status == evaluation)
        conditions.append(exists().where(*sub))
    total = await session.scalar(select(func.count()).select_from(RunCaseResult).where(*conditions))
    rows = (
        await session.scalars(
            select(RunCaseResult)
            .where(*conditions)
            .order_by(RunCaseResult.sequence)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    evals = await _evaluations(session, [r.id for r in rows])
    items = []
    for r in rows:
        item = _result_read(r, preview=True)
        item.evaluations = [
            EvaluationSummary(
                evaluator_name=e.evaluator_name,
                suite=e.suite,
                status=e.status,
                score=e.score,
                passed=e.passed,
                label=e.label,
            )
            for e in evals.get(r.id, [])
        ]
        items.append(item)
    return Page(
        items=items,
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def get_result(
    session: AsyncSession, run_id: uuid.UUID, result_id: uuid.UUID
) -> CaseResultDetail:
    row = await session.get(RunCaseResult, result_id)
    if row is None or row.run_id != run_id:
        raise NotFoundError("Result not found")
    base = _result_read(row, preview=False)
    evals = (await _evaluations(session, [row.id])).get(row.id, [])
    return CaseResultDetail(
        **base.model_dump(exclude={"evaluations"}),
        evaluations=[_evaluation_read(e) for e in evals],
    )


async def failed_count(session: AsyncSession, run_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(RunCaseResult)
        .where(
            RunCaseResult.run_id == run_id,
            RunCaseResult.status == RESULT_FAILED,
            RunCaseResult.is_final.is_(True),
        )
    )
    return count or 0


async def run_metrics(session: AsyncSession, run_id: uuid.UUID) -> RunMetrics:
    """Stored aggregates for finished runs; computed from results so far otherwise."""
    run = await get_run(session, run_id)
    live = run.status not in TERMINAL_STATUSES
    items: list[MetricRead]
    if live:
        rows, _ = await compute_metric_rows(session, run)
        items = [
            MetricRead(
                run_model_id=r["run_model_id"],
                load_step=r["load_step"],
                context_length=r.get("context_length"),
                metric_name=r["metric_name"],
                aggregation=r["aggregation"],
                metric_value=r["metric_value"],
                metric_unit=r["metric_unit"],
                sample_count=r["sample_count"],
                metadata=r["metadata_"],
            )
            for r in rows
        ]
    else:
        stored = (
            await session.scalars(
                select(RunAggregateMetric)
                .where(RunAggregateMetric.run_id == run_id)
                .order_by(RunAggregateMetric.created_at, RunAggregateMetric.id)
            )
        ).all()
        items = [
            MetricRead(
                run_model_id=m.run_model_id,
                load_step=m.load_step,
                context_length=m.context_length,
                metric_name=m.metric_name,
                aggregation=m.aggregation,
                metric_value=m.metric_value,
                metric_unit=m.metric_unit,
                sample_count=m.sample_count,
                metadata=m.metadata_,
            )
            for m in stored
        ]
    return RunMetrics(
        run_id=run.id,
        live=live,
        metrics_version=run.configuration_snapshot.get("metrics_version", METRICS_VERSION),
        items=items,
    )


TELEMETRY_MAX_SAMPLES = 20_000


async def run_telemetry(
    session: AsyncSession, run_id: uuid.UUID, run_model_id: uuid.UUID | None = None
) -> RunTelemetry:
    run = await get_run(session, run_id)
    query = select(ResourceSample).where(ResourceSample.run_id == run_id)
    if run_model_id is not None:
        query = query.where(ResourceSample.run_model_id == run_model_id)
    rows = (
        await session.scalars(
            query.order_by(ResourceSample.run_model_id, ResourceSample.offset_seconds).limit(
                TELEMETRY_MAX_SAMPLES + 1
            )
        )
    ).all()
    status = {
        str(rm.id): dict((rm.progress or {}).get("telemetry"))
        for rm in run.models
        if (rm.progress or {}).get("telemetry")
    }
    return RunTelemetry(
        run_id=run.id,
        status=status,
        items=[ResourceSampleRead.model_validate(r) for r in rows[:TELEMETRY_MAX_SAMPLES]],
        truncated=len(rows) > TELEMETRY_MAX_SAMPLES,
    )
