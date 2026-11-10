"""Run executor: executes one benchmark run (spec §5.4, §13.2).

Reliability model:

* The database is the source of truth. A worker claims a run with an atomic conditional
  UPDATE and holds a lease that it renews on every progress flush. If the worker dies, the
  lease expires and another worker resumes the run.
* Every attempt of every request is a separate row, unique on (model, phase, request index,
  attempt number). Rows are buffered in memory and flushed every
  ``run_progress_interval_seconds``. A crash loses at most that buffer, and the lost requests are
  executed again on resume. Nothing is duplicated.
* Cancellation is cooperative. The flush loop sees ``cancel_requested``, stops dispatching, and
  cancels in-flight requests (they are not recorded). Results so far are kept and aggregated.
* On graceful worker shutdown the run goes back to ``queued`` and is resumed by the next
  worker. Each execution is a *pass*; throughput is only computed when every outcome comes from
  the first pass, because the measurement window is otherwise not contiguous.
* Quality evaluation (M4) runs inline on each final, successful measured request, using the full
  response in memory (not the stored, possibly truncated copy). Evaluation rows are buffered and
  flushed in the same transaction as their result rows. Simulated tool execution (spec §5.7
  mode B) sends follow-up requests with fixture tool results; nothing is ever executed.
* With an LLM judge (M5, opt-in), the RAG and hallucination evaluators run after each model's
  measurement instead of inline, with their own concurrency, so judge latency never holds back
  the measured lanes. A resumed run judges earlier results from their stored text; a response
  that was not stored in full is recorded as a failed evaluation rather than guessed.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

from sqlalchemy import and_, case, delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.adapters.base import AdapterContext, EndpointRuntimeConfig, ModelAdapter
from app.adapters.registry import create_adapter
from app.adapters.types import GenerationRequest, ModelResponse
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models.dataset import DatasetCase
from app.db.models.evaluation import EvaluationResult
from app.db.models.run import (
    ACTIVE_STATUSES,
    CANCEL_REQUESTED,
    CANCELLED,
    COMPLETED,
    COMPLETED_WITH_ERRORS,
    FAILED,
    FINALIZING,
    INITIALIZING,
    PHASE_MEASURED,
    PHASE_WARMUP,
    QUEUED,
    RESULT_FAILED,
    RESULT_SUCCEEDED,
    RUNNING,
    BenchmarkRun,
    RunAggregateMetric,
    RunCaseResult,
    RunModel,
)
from app.evaluators.base import EvaluationContext, Evaluator, ModelOutput, ToolCallOutput
from app.evaluators.judge import Judge
from app.evaluators.registry import JUDGE_SUITES, QUALITY_SUITES, evaluate_case, evaluators_for
from app.evaluators.tools import needs_simulation, simulate_tool_result
from app.execution.plan import Step, steps_of
from app.execution.prompting import build_request
from app.metrics import performance, quality
from app.metrics.stats import percentile
from app.schemas.endpoint import RequestConfiguration

logger = get_logger(__name__)

MAX_BACKOFF_SECONDS = 60.0
MAX_ERROR_MESSAGE_CHARS = 2000
MAX_TOOL_RESULT_CHARS = 2000


def utcnow() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------------------- claiming


async def claim_run(
    factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    worker_id: str,
    lease_seconds: float,
) -> bool:
    """Atomically take ownership of a queued run, or of an active run whose lease expired."""
    now = utcnow()
    async with factory() as session:
        result = await session.execute(
            update(BenchmarkRun)
            .where(
                BenchmarkRun.id == run_id,
                or_(
                    BenchmarkRun.status == QUEUED,
                    and_(
                        BenchmarkRun.status.in_(ACTIVE_STATUSES),
                        or_(
                            BenchmarkRun.lease_expires_at.is_(None),
                            BenchmarkRun.lease_expires_at < now,
                        ),
                    ),
                ),
            )
            .values(
                status=case(
                    (BenchmarkRun.status == QUEUED, INITIALIZING), else_=BenchmarkRun.status
                ),
                worker_id=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return result.rowcount == 1  # type: ignore[attr-defined]


async def claim_next_run(
    factory: async_sessionmaker[AsyncSession], worker_id: str, lease_seconds: float
) -> uuid.UUID | None:
    """Claim the oldest abandoned run (expired lease), else the oldest queued run."""
    now = utcnow()
    async with factory() as session:
        stale = (
            await session.scalars(
                select(BenchmarkRun.id)
                .where(
                    BenchmarkRun.status.in_(ACTIVE_STATUSES),
                    or_(
                        BenchmarkRun.lease_expires_at.is_(None),
                        BenchmarkRun.lease_expires_at < now,
                    ),
                )
                .order_by(BenchmarkRun.created_at)
                .limit(5)
            )
        ).all()
        queued = (
            await session.scalars(
                select(BenchmarkRun.id)
                .where(BenchmarkRun.status == QUEUED)
                .order_by(BenchmarkRun.created_at)
                .limit(5)
            )
        ).all()
    for run_id in [*stale, *queued]:
        if await claim_run(factory, run_id, worker_id, lease_seconds):
            return run_id
    return None


# ------------------------------------------------------------------------------ execution


@dataclass(frozen=True)
class CaseSpec:
    dataset_case_id: uuid.UUID
    case_identifier: str
    request: GenerationRequest
    prompt_chars: int
    case: dict[str, Any] = field(default_factory=dict)


@dataclass
class Final:
    status: str
    latency_ms: float | None
    ttft_ms: float | None
    output_tps: float | None


@dataclass
class ModelState:
    run_model_id: uuid.UUID
    adapter: ModelAdapter
    stream: bool
    max_retries: int
    backoff_seconds: float
    planned: int | None
    warmup_planned: int
    next_attempt: dict[tuple[str, int], int] = field(default_factory=dict)
    finals: dict[int, Final] = field(default_factory=dict)
    retries: int = 0
    warmup_completed: int = 0
    in_flight: int = 0
    phase: str = "pending"
    current_step: int | None = None
    current_concurrency: int | None = None
    pass_succeeded: int = 0
    measured_started: float | None = None  # monotonic
    # Outputs waiting for the judge phase: (result id, case, output).
    pending_judge: list[tuple[uuid.UUID, CaseSpec, ModelOutput]] = field(default_factory=list)
    judge_planned: int = 0
    judge_completed: int = 0

    def progress(self) -> dict[str, Any]:
        finals = list(self.finals.values())
        ok = [f for f in finals if f.status == RESULT_SUCCEEDED]
        latencies = [f.latency_ms for f in ok if f.latency_ms is not None]
        ttfts = [f.ttft_ms for f in ok if f.ttft_ms is not None]
        tps = [f.output_tps for f in ok if f.output_tps is not None]
        elapsed = (
            time.monotonic() - self.measured_started if self.measured_started is not None else None
        )
        return {
            "phase": self.phase,
            "planned": self.planned,
            "completed": len(finals),
            "succeeded": len(ok),
            "failed": len(finals) - len(ok),
            "retries": self.retries,
            "in_flight": self.in_flight,
            "warmup_planned": self.warmup_planned,
            "warmup_completed": self.warmup_completed,
            "current_step": self.current_step,
            "current_concurrency": self.current_concurrency,
            "elapsed_s": round(elapsed, 3) if elapsed is not None else None,
            "judge_planned": self.judge_planned,
            "judge_completed": self.judge_completed,
            # Live indications from results so far; final metrics come from aggregation.
            "live": {
                "latency_ms_p50": percentile(latencies, 50),
                "latency_ms_p95": percentile(latencies, 95),
                "ttft_ms_p50": percentile(ttfts, 50),
                "ttft_ms_p95": percentile(ttfts, 95),
                "output_tokens_per_second_p50": percentile(tps, 50),
                "request_throughput": (self.pass_succeeded / elapsed)
                if elapsed and elapsed > 0
                else None,
            },
        }


class StopRun(Exception):
    """Internal: unwinds execution when the run is stopped."""


class RunExecutor:
    def __init__(
        self,
        run_id: uuid.UUID,
        factory: async_sessionmaker[AsyncSession],
        context: AdapterContext,
        settings: Settings,
        worker_id: str,
    ) -> None:
        self.run_id = run_id
        self.factory = factory
        self.context = context
        self.settings = settings
        self.worker_id = worker_id
        self._stop = asyncio.Event()
        self.stop_reason: str | None = None
        self._buffer: list[dict[str, Any]] = []
        self._sequence = count(1)
        self._models: dict[uuid.UUID, ModelState] = {}
        self._current: ModelState | None = None
        self._flush_lock = asyncio.Lock()
        self._model_status: dict[uuid.UUID, str] = {}
        self.cases: list[CaseSpec] = []
        self.pass_number = 0
        # Quality evaluation (configured from the snapshot in execute()).
        self._eval_buffer: list[dict[str, Any]] = []
        self.evaluators: list[Evaluator] = []
        # With a judge, evaluators of the judge suites run in the judge phase (deferred).
        self.inline_evaluators: list[Evaluator] = []
        self.deferred_evaluators: list[Evaluator] = []
        self.judge: Judge | None = None
        self.judge_concurrency = 1
        self.eval_config: dict[str, Any] = {}
        self.embedder: Any = None
        self.simulate_tools = False

    # ------------------------------------------------------------------ control

    def stop(self, reason: str) -> None:
        """Stop executing: ``cancel`` (user), ``shutdown`` (worker exit), or ``lease_lost``."""
        if self.stop_reason is None:
            self.stop_reason = reason
        self._stop.set()

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    async def _owned_update(self, session: AsyncSession, **values: Any) -> bool:
        result = await session.execute(
            update(BenchmarkRun)
            .where(BenchmarkRun.id == self.run_id, BenchmarkRun.worker_id == self.worker_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1  # type: ignore[attr-defined]

    async def _set_status(self, status: str) -> None:
        """Advance the status unless a cancel was requested meanwhile."""
        async with self.factory() as session:
            await session.execute(
                update(BenchmarkRun)
                .where(
                    BenchmarkRun.id == self.run_id,
                    BenchmarkRun.worker_id == self.worker_id,
                    BenchmarkRun.status != CANCEL_REQUESTED,
                )
                .values(status=status)
                .execution_options(synchronize_session=False)
            )
            await session.commit()

    def _run_progress(self) -> dict[str, Any]:
        states = list(self._models.values())
        progresses = [s.progress() for s in states]
        planned = [p["planned"] for p in progresses]
        current = self._current
        return {
            "pass_number": self.pass_number,
            "models_total": len(states),
            "models_done": sum(
                1 for s in states if self._model_status.get(s.run_model_id) == "completed"
            ),
            "current_run_model_id": str(current.run_model_id) if current else None,
            "phase": current.phase if current else None,
            "planned": None if any(p is None for p in planned) else sum(planned),  # type: ignore[arg-type]
            "completed": sum(p["completed"] for p in progresses),
            "succeeded": sum(p["succeeded"] for p in progresses),
            "failed": sum(p["failed"] for p in progresses),
            "in_flight": sum(p["in_flight"] for p in progresses),
            "updated_at": utcnow().isoformat(),
        }

    async def flush(self) -> None:
        """Persist buffered results and progress, renew the lease, and check for cancellation."""
        async with self._flush_lock:
            rows, self._buffer = self._buffer, []
            evals, self._eval_buffer = self._eval_buffer, []
            try:
                async with self.factory() as session:
                    # Renew the lease first: a worker that lost the run must not write to it.
                    owned = await self._owned_update(
                        session,
                        progress=self._run_progress(),
                        lease_expires_at=utcnow()
                        + timedelta(seconds=self.settings.run_lease_seconds),
                    )
                    if owned:
                        if rows:
                            await session.execute(insert(RunCaseResult), rows)
                        if evals:
                            await session.execute(insert(EvaluationResult), evals)
                        for state in self._models.values():
                            await session.execute(
                                update(RunModel)
                                .where(RunModel.id == state.run_model_id)
                                .values(progress=state.progress())
                                .execution_options(synchronize_session=False)
                            )
                        await session.commit()
                    else:
                        await session.rollback()
                    status = await session.scalar(
                        select(BenchmarkRun.status).where(BenchmarkRun.id == self.run_id)
                    )
            except Exception:
                # Keep the results for the next flush (e.g. the database was briefly away).
                self._buffer[:0] = rows
                self._eval_buffer[:0] = evals
                raise
        if not owned:
            logger.warning("run_lease_lost", run_id=str(self.run_id), worker_id=self.worker_id)
            self.stop("lease_lost")
        elif status == CANCEL_REQUESTED:
            self.stop("cancel")

    async def _control_loop(self, done: asyncio.Event) -> None:
        while not done.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    done.wait(), timeout=self.settings.run_progress_interval_seconds
                )
            try:
                await self.flush()
            except Exception as exc:  # keep running; the next flush retries
                logger.warning("run_flush_failed", run_id=str(self.run_id), error=str(exc)[:300])

    # ------------------------------------------------------------------ setup

    async def _load_cases(self, run: BenchmarkRun) -> list[CaseSpec]:
        generation = run.configuration_snapshot.get("generation", {})
        specs: list[CaseSpec] = []
        async with self.factory() as session:
            for ref in run.datasets:
                if ref.dataset_version_id is None:
                    raise RuntimeError(
                        "A dataset version used by this run has been deleted; "
                        "its cases can no longer be executed."
                    )
                rows = await session.execute(
                    select(DatasetCase.id, DatasetCase.case_identifier, DatasetCase.content)
                    .where(DatasetCase.dataset_version_id == ref.dataset_version_id)
                    .order_by(DatasetCase.position)
                )
                for case_id, identifier, content in rows:
                    request = build_request(content, generation)
                    chars = sum(len(str(m.get("content") or "")) for m in request.messages)
                    specs.append(CaseSpec(case_id, identifier, request, chars, content))
        if not specs:
            raise RuntimeError("The run's datasets contain no cases.")
        return specs

    def _adapter_for(self, rm: RunModel) -> tuple[ModelAdapter, RequestConfiguration]:
        adapter = self._adapter_from_snapshot(rm.endpoint_snapshot, rm.endpoint_id)
        return adapter, adapter.config.request

    def _adapter_from_snapshot(
        self, snap: dict[str, Any], endpoint_id: uuid.UUID | None
    ) -> ModelAdapter:
        rc = RequestConfiguration.model_validate(snap["request_configuration"])
        config = EndpointRuntimeConfig(
            base_url=snap["base_url"],
            model_identifier=snap["model_identifier"],
            request=rc,
            secret_reference=snap.get("secret_reference"),
            endpoint_id=endpoint_id,
            name=snap.get("name"),
        )
        return create_adapter(snap["adapter_type"], config, self.context)

    async def _existing(self, session: AsyncSession, state: ModelState) -> None:
        rows = await session.execute(
            select(
                RunCaseResult.phase,
                RunCaseResult.request_index,
                RunCaseResult.attempt_number,
                RunCaseResult.pass_number,
                RunCaseResult.is_final,
                RunCaseResult.status,
                RunCaseResult.latency_ms,
                RunCaseResult.ttft_ms,
                RunCaseResult.performance_metrics,
            ).where(RunCaseResult.run_model_id == state.run_model_id)
        )
        best: dict[int, tuple[int, int]] = {}
        for phase, idx, attempt, pass_no, is_final, status, latency, ttft, perf in rows:
            key = (phase, idx)
            state.next_attempt[key] = max(state.next_attempt.get(key, 0), attempt)
            if phase != PHASE_MEASURED:
                continue
            if is_final and (pass_no, attempt) > best.get(idx, (0, 0)):
                best[idx] = (pass_no, attempt)
                state.finals[idx] = Final(
                    status, latency, ttft, (perf or {}).get("output_tokens_per_second")
                )
        attempts: dict[int, int] = {}
        for (phase, idx), n in state.next_attempt.items():
            if phase == PHASE_MEASURED:
                attempts[idx] = n
        state.retries = sum(n - 1 for n in attempts.values())

    # ------------------------------------------------------------------ requests

    def _record(
        self,
        state: ModelState,
        spec: CaseSpec,
        *,
        phase: str,
        index: int,
        attempt: int,
        step: int | None,
        concurrency: int,
        is_final: bool,
        response: ModelResponse,
        started: datetime,
        completed: datetime,
        simulation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timing = response.timing
        usage = response.usage
        derived = performance.per_request_rates(
            streamed=state.stream,
            latency_ms=timing.latency_ms if timing else None,
            generation_ms=timing.generation_ms if timing else None,
            output_tokens=usage.output_tokens,
            output_event_offsets_ms=timing.output_event_offsets_ms if timing else [],
        )
        limit = self.settings.run_response_max_chars
        store = self.settings.run_store_responses
        content = response.content or ""
        reasoning = response.reasoning_content or ""
        req = spec.request
        return {
            "id": uuid.uuid4(),
            "run_id": self.run_id,
            "run_model_id": state.run_model_id,
            "dataset_case_id": spec.dataset_case_id,
            "case_identifier": spec.case_identifier,
            "sequence": next(self._sequence),
            "phase": phase,
            "request_index": index,
            "attempt_number": attempt,
            "pass_number": self.pass_number,
            "load_step": step,
            "is_final": is_final,
            "is_warmup": phase == PHASE_WARMUP,
            "status": RESULT_SUCCEEDED if response.ok else RESULT_FAILED,
            "error_type": response.error.type if response.error else None,
            "error_message": response.error.message[:MAX_ERROR_MESSAGE_CHARS]
            if response.error
            else None,
            "http_status": response.http_status,
            "latency_ms": timing.latency_ms if timing else None,
            "ttft_ms": timing.ttft_ms if timing else None,
            "request_metadata": {
                "request_id": str(response.request_id),
                "stream": state.stream,
                "concurrency": concurrency,
                "temperature": req.temperature,
                "top_p": req.top_p,
                "max_tokens": req.max_tokens,
                "seed": req.extra_body.get("seed"),
                "message_count": len(req.messages),
                "tool_count": len(req.tools or []),
                "prompt_chars": spec.prompt_chars,
                "retryable_error": response.error.retryable if response.error else None,
            },
            "response_data": {
                "content": (content[:limit] if store else None)
                if response.content is not None
                else None,
                "content_chars": len(content) if response.content is not None else None,
                "content_truncated": store and len(content) > limit,
                "reasoning_content": (reasoning[:limit] if store else None)
                if response.reasoning_content is not None
                else None,
                "reasoning_chars": len(reasoning)
                if response.reasoning_content is not None
                else None,
                "tool_calls": [t.model_dump(mode="json") for t in response.tool_calls]
                if store
                else [],
                "tool_call_count": len(response.tool_calls),
                "finish_reason": response.finish_reason,
                "model_reported": response.model,
                "response_id": response.response_id,
                "warnings": response.warnings,
                "stored": store,
                # Identity of the answer, for consistency across repeated cases.
                "content_sha256": _answer_hash(response) if response.ok else None,
                **(
                    {"simulation": _stored_simulation(simulation, store, limit)}
                    if simulation
                    else {}
                ),
            },
            "performance_metrics": {
                "streamed": timing.streamed if timing else state.stream,
                "latency_ms": timing.latency_ms if timing else None,
                "ttft_ms": timing.ttft_ms if timing else None,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "token_count_source": usage.source,
                **derived,
            },
            "started_at": timing.started_at if timing else started,
            "completed_at": timing.ended_at if timing else completed,
            "created_at": utcnow(),
        }

    async def _execute(
        self, state: ModelState, phase: str, index: int, step: int | None, concurrency: int
    ) -> None:
        spec = self.cases[index % len(self.cases)]
        key = (phase, index)
        attempts_allowed = 1 if phase == PHASE_WARMUP else 1 + state.max_retries
        for k in range(attempts_allowed):
            attempt = state.next_attempt.get(key, 0) + 1
            request = spec.request.model_copy(update={"request_id": uuid.uuid4()})
            started = utcnow()
            state.in_flight += 1
            try:
                if state.stream:
                    response = await state.adapter.generate_stream(request)
                else:
                    response = await state.adapter.generate(request)
            finally:
                state.in_flight -= 1
            retry = (
                not response.ok
                and response.error is not None
                and response.error.retryable
                and k + 1 < attempts_allowed
                and not self._stop.is_set()
            )
            completed = utcnow()
            evaluate = (
                phase == PHASE_MEASURED and not retry and response.ok and bool(self.evaluators)
            )
            output = _model_output(response) if evaluate else None
            simulation = None
            if output is not None and self.simulate_tools and needs_simulation(spec.case, output):
                simulation = await self._simulate(state, spec, response)
                output.simulation = simulation
            row = self._record(
                state,
                spec,
                phase=phase,
                index=index,
                attempt=attempt,
                step=step,
                concurrency=concurrency,
                is_final=not retry,
                response=response,
                started=started,
                completed=completed,
                simulation=simulation,
            )
            evals = (
                await self._evaluate(state, spec, row["id"], output, self.inline_evaluators)
                if output is not None
                else []
            )
            if output is not None and any(
                spec.case.get("type") in e.case_types for e in self.deferred_evaluators
            ):
                state.pending_judge.append((row["id"], spec, output))
            # Appended together (no await in between), so a cancelled task records neither.
            self._buffer.append(row)
            self._eval_buffer.extend(evals)
            state.next_attempt[key] = attempt
            if phase == PHASE_WARMUP:
                state.warmup_completed += 1
                return
            if k > 0:
                state.retries += 1
            if not retry:
                state.finals[index] = Final(
                    row["status"],
                    row["latency_ms"],
                    row["ttft_ms"],
                    row["performance_metrics"].get("output_tokens_per_second"),
                )
                if response.ok:
                    state.pass_succeeded += 1
                return
            await self._sleep(min(state.backoff_seconds * (2**k), MAX_BACKOFF_SECONDS))
            if self._stop.is_set():
                return

    async def _simulate(
        self, state: ModelState, spec: CaseSpec, response: ModelResponse
    ) -> dict[str, Any]:
        """Simulated tool execution (spec §5.7 mode B): answer each tool call with the case's
        fixture result (or a validation error) and let the model continue, up to
        ``max_tool_rounds`` follow-up requests. No tool is ever executed. Follow-ups are not
        part of the performance measurement."""
        max_rounds = int(self.eval_config.get("max_tool_rounds", 3))
        messages = list(spec.request.messages)
        rounds: list[dict[str, Any]] = []
        current = response
        for round_no in range(1, max_rounds + 1):
            calls = []
            for i, tc in enumerate(current.tool_calls):
                call_id = tc.id or f"call_{round_no}_{i}"
                calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": tc.name or "",
                            "arguments": tc.arguments_raw or json.dumps(tc.arguments or {}),
                        },
                    }
                )
            tool_messages = []
            results = []
            for tc, call in zip(current.tool_calls, calls, strict=True):
                sim = simulate_tool_result(spec.case, _tool_output(tc))
                payload = sim["result"]
                text = (
                    payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                )
                tool_messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
                results.append(
                    {
                        "name": tc.name,
                        "arguments": tc.arguments if tc.arguments is not None else tc.arguments_raw,
                        "fixture_matched": sim["fixture"],
                        "valid": sim["ok"],
                        "result": text[:MAX_TOOL_RESULT_CHARS],
                    }
                )
            messages = [
                *messages,
                {"role": "assistant", "content": current.content, "tool_calls": calls},
                *tool_messages,
            ]
            request = spec.request.model_copy(
                update={"messages": messages, "request_id": uuid.uuid4()}
            )
            follow = await state.adapter.generate(request)
            rounds.append(
                {
                    "round": round_no,
                    "tool_results": results,
                    "latency_ms": follow.timing.latency_ms if follow.timing else None,
                    "ok": follow.ok,
                    "error": follow.error.message[:300] if follow.error else None,
                    "next_tool_calls": [tc.name for tc in follow.tool_calls],
                }
            )
            base = {"mode": "simulated", "executed": False, "rounds": rounds}
            if not follow.ok:
                return base | {
                    "completed": False,
                    "error_kind": "request_failed",
                    "stop_reason": "Follow-up request failed: "
                    + (follow.error.message[:200] if follow.error else "unknown error"),
                    "final_content": None,
                }
            if not follow.tool_calls:
                return base | {"completed": True, "final_content": follow.content or ""}
            current = follow
        return {
            "mode": "simulated",
            "executed": False,
            "rounds": rounds,
            "completed": False,
            "error_kind": "max_rounds",
            "stop_reason": (
                f"The model was still calling tools after {max_rounds} simulated round(s)."
            ),
            "final_content": None,
        }

    async def _evaluate(
        self,
        state: ModelState,
        spec: CaseSpec,
        result_id: uuid.UUID,
        output: ModelOutput,
        evaluators: list[Evaluator],
    ) -> list[dict[str, Any]]:
        if not evaluators:
            return []
        context = EvaluationContext(
            config=self.eval_config, embedder=self.embedder, judge=self.judge
        )
        outcomes = await evaluate_case(spec.case, output, evaluators, context)
        now = utcnow()
        return [
            {
                "id": uuid.uuid4(),
                "run_id": self.run_id,
                "run_model_id": state.run_model_id,
                "run_case_result_id": result_id,
                "suite": o.suite,
                "evaluator_name": o.evaluator,
                "evaluator_version": o.evaluator_version,
                "status": o.status,
                "score": o.score,
                "passed": o.passed,
                "label": o.label,
                "rationale": o.reason,
                "evidence": o.evidence,
                "metadata_": o.metadata
                | (
                    {"score_scale": {"min": o.score_scale[0], "max": o.score_scale[1]}}
                    if o.score_scale
                    else {}
                ),
                "created_at": now,
            }
            for o in outcomes
        ]

    async def _pool(
        self,
        state: ModelState,
        indices: Iterator[int],
        concurrency: int,
        step: int | None,
        deadline: float | None = None,
    ) -> None:
        """Keep up to ``concurrency`` requests in flight, dispatching indices in order."""

        async def lane() -> None:
            while not self._stop.is_set():
                if deadline is not None and time.monotonic() >= deadline:
                    return
                index = next(indices, None)
                if index is None:
                    return
                await self._execute(state, PHASE_MEASURED, index, step, concurrency)

        lanes = [asyncio.create_task(lane()) for _ in range(concurrency)]
        stopped = asyncio.create_task(self._stop.wait())
        pending: set[asyncio.Task[Any]] = set(lanes)
        try:
            # Return when every lane is done, the run is stopped, or a lane fails.
            while pending and not stopped.done():
                done, pending = await asyncio.wait(
                    pending | {stopped}, return_when=asyncio.FIRST_COMPLETED
                )
                pending.discard(stopped)
                if any(t is not stopped and t.exception() is not None for t in done):
                    break
        finally:
            stopped.cancel()
            for task in lanes:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*lanes, return_exceptions=True)
        for task in lanes:
            if not task.cancelled() and task.exception() is not None:
                raise task.exception()  # type: ignore[misc]
        if self._stop.is_set():
            raise StopRun

    async def _resumed_judge_items(
        self, state: ModelState, skip: set[uuid.UUID]
    ) -> tuple[list[tuple[uuid.UUID, CaseSpec, ModelOutput]], list[tuple[uuid.UUID, CaseSpec]]]:
        """Results from earlier passes that still lack judge-suite evaluations. Those whose full
        text was stored can be judged; the others cannot."""
        judged = (
            select(EvaluationResult.id)
            .where(
                EvaluationResult.run_case_result_id == RunCaseResult.id,
                EvaluationResult.suite.in_(JUDGE_SUITES),
            )
            .exists()
        )
        async with self.factory() as session:
            rows = await session.execute(
                select(
                    RunCaseResult.id, RunCaseResult.dataset_case_id, RunCaseResult.response_data
                ).where(
                    RunCaseResult.run_model_id == state.run_model_id,
                    RunCaseResult.phase == PHASE_MEASURED,
                    RunCaseResult.is_final.is_(True),
                    RunCaseResult.status == RESULT_SUCCEEDED,
                    ~judged,
                )
            )
        specs = {s.dataset_case_id: s for s in self.cases}
        types = {t for e in self.deferred_evaluators for t in e.case_types}
        ready: list[tuple[uuid.UUID, CaseSpec, ModelOutput]] = []
        lost: list[tuple[uuid.UUID, CaseSpec]] = []
        for result_id, case_id, data in rows:
            spec = specs.get(case_id)
            if result_id in skip or spec is None or spec.case.get("type") not in types:
                continue
            data = data or {}
            if data.get("stored") and not data.get("content_truncated"):
                ready.append((result_id, spec, ModelOutput(content=data.get("content"))))
            else:
                lost.append((result_id, spec))
        return ready, lost

    async def _judge_phase(self, state: ModelState) -> None:
        """Run the deferred (judge-backed) evaluators for one model's results."""
        if not self.deferred_evaluators or self._stop.is_set():
            return
        items, state.pending_judge = state.pending_judge, []
        ready, lost = await self._resumed_judge_items(state, {i[0] for i in items})
        items += ready
        now = utcnow()
        for result_id, spec in lost:
            for e in self.deferred_evaluators:
                if spec.case.get("type") not in e.case_types:
                    continue
                self._eval_buffer.append(
                    {
                        "id": uuid.uuid4(),
                        "run_id": self.run_id,
                        "run_model_id": state.run_model_id,
                        "run_case_result_id": result_id,
                        "suite": e.suite,
                        "evaluator_name": e.name,
                        "evaluator_version": e.version,
                        "status": "failed",
                        "score": None,
                        "passed": None,
                        "label": None,
                        "rationale": "The run was resumed and this response was not stored in "
                        "full (RUN_STORE_RESPONSES / RUN_RESPONSE_MAX_CHARS), so it could not be "
                        "judged.",
                        "evidence": [],
                        "metadata_": {"method": "judge", "deferred": True},
                        "created_at": now,
                    }
                )
        if not items:
            return
        state.phase = "judging"
        state.judge_planned = len(items)
        state.judge_completed = 0
        semaphore = asyncio.Semaphore(self.judge_concurrency)

        async def judge_one(result_id: uuid.UUID, spec: CaseSpec, output: ModelOutput) -> None:
            async with semaphore:
                if self._stop.is_set():
                    return
                evals = await self._evaluate(
                    state, spec, result_id, output, self.deferred_evaluators
                )
                self._eval_buffer.extend(evals)
                state.judge_completed += 1

        work = asyncio.ensure_future(asyncio.gather(*(judge_one(*i) for i in items)))
        stopped = asyncio.ensure_future(self._stop.wait())
        try:
            await asyncio.wait({work, stopped}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopped.cancel()
            if not work.done():
                work.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await work
        if not work.cancelled() and work.exception() is not None:
            raise work.exception()  # type: ignore[misc]

    def _todo(self, state: ModelState, indices: Iterable[int], retry_failed: bool) -> list[int]:
        out = []
        for i in indices:
            final = state.finals.get(i)
            if final is None or (retry_failed and final.status == RESULT_FAILED):
                out.append(i)
        return out

    async def _run_model(
        self, rm: RunModel, steps: list[Step], warmup: int, retry_failed: bool
    ) -> None:
        state = self._models[rm.id]
        self._current = state
        work: list[tuple[Step, Iterator[int], float | None]] = []
        for st in steps:
            if st.count is None:  # uncapped sustained
                if self.pass_number == 1:
                    work.append((st, count(st.start), st.duration_seconds))
                elif retry_failed:
                    failed = self._todo(state, sorted(state.finals), True)
                    work.append((st, iter(failed), None))
                continue
            todo = self._todo(state, range(st.start, st.start + st.count), retry_failed)
            if not todo:
                continue
            duration = st.duration_seconds if self.pass_number == 1 else None
            work.append((st, iter(todo), duration))
        if not work:
            await self._judge_phase(state)
            self._model_status[rm.id] = "completed"
            return

        self._model_status[rm.id] = "running"
        async with self.factory() as session:
            await session.execute(
                update(RunModel)
                .where(RunModel.id == rm.id)
                .values(status="running", started_at=rm.started_at or utcnow())
                .execution_options(synchronize_session=False)
            )
            await session.commit()

        if warmup:
            state.phase = PHASE_WARMUP
            state.warmup_completed = 0
            for i in range(warmup):
                if self._stop.is_set():
                    raise StopRun
                await self._execute(state, PHASE_WARMUP, i, None, 1)

        state.phase = PHASE_MEASURED
        state.measured_started = time.monotonic()
        for st, indices, duration in work:
            state.current_step = st.step
            state.current_concurrency = st.concurrency
            deadline = time.monotonic() + duration if duration else None
            await self._pool(state, indices, st.concurrency, st.step, deadline)
        await self._judge_phase(state)
        state.phase = "done"
        self._model_status[rm.id] = "completed"
        async with self.factory() as session:
            await session.execute(
                update(RunModel)
                .where(RunModel.id == rm.id)
                .values(status="completed", completed_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            await session.commit()

    # ------------------------------------------------------------------ finalize

    async def _aggregate(self, session: AsyncSession, run: BenchmarkRun) -> dict[str, Any]:
        rows, summary = await compute_metric_rows(session, run)
        await session.execute(
            delete(RunAggregateMetric).where(RunAggregateMetric.run_id == self.run_id)
        )
        if rows:
            await session.execute(insert(RunAggregateMetric), rows)
        return summary

    async def _finalize(self, run: BenchmarkRun, cancelled: bool) -> str:
        await self._set_status(FINALIZING)
        async with self.factory() as session:
            run = await load_run(session, self.run_id)
            summary = await self._aggregate(session, run)
            if cancelled or run.status == CANCEL_REQUESTED:
                status = CANCELLED
            elif summary["failed_requests"] or summary["not_completed"]:
                status = COMPLETED_WITH_ERRORS
            else:
                status = COMPLETED
            error_summary = None
            if status != COMPLETED:
                parts = []
                if summary["failed_requests"]:
                    parts.append(f"{summary['failed_requests']} measured request(s) failed")
                if summary["not_completed"]:
                    parts.append(f"{summary['not_completed']} not completed")
                error_summary = {
                    **summary,
                    "message": ("; ".join(parts) or "Cancelled") + ".",
                }
            for rm in run.models:
                rm_status = self._model_status.get(rm.id)
                if rm_status in (None, "pending", "running"):
                    rm_status = "cancelled" if status == CANCELLED else "completed"
                rm.status = rm_status
                if rm.completed_at is None and rm_status == "completed":
                    rm.completed_at = utcnow()
            await self._owned_update(
                session,
                status=status,
                completed_at=utcnow(),
                error_summary=error_summary,
                retry_failed_requested=False,
                progress=self._run_progress() | {"phase": "done"},
                worker_id=None,
                lease_expires_at=None,
            )
            await session.commit()
        return status

    async def _fail(self, message: str) -> None:
        async with self.factory() as session:
            await self._owned_update(
                session,
                status=FAILED,
                completed_at=utcnow(),
                error_summary={"type": "run_error", "message": message[:MAX_ERROR_MESSAGE_CHARS]},
                worker_id=None,
                lease_expires_at=None,
            )
            await session.commit()

    async def _release_for_resume(self) -> None:
        """Graceful shutdown: hand the run back to the queue for the next worker."""
        async with self.factory() as session:
            await session.execute(
                update(BenchmarkRun)
                .where(
                    BenchmarkRun.id == self.run_id,
                    BenchmarkRun.worker_id == self.worker_id,
                    BenchmarkRun.status != CANCEL_REQUESTED,
                )
                .values(status=QUEUED, worker_id=None, lease_expires_at=None)
                .execution_options(synchronize_session=False)
            )
            # A cancel requested meanwhile stays active; release the lease so it is picked up.
            await session.execute(
                update(BenchmarkRun)
                .where(BenchmarkRun.id == self.run_id, BenchmarkRun.worker_id == self.worker_id)
                .values(worker_id=None, lease_expires_at=None)
                .execution_options(synchronize_session=False)
            )
            await session.commit()

    # ------------------------------------------------------------------ main

    async def execute(self) -> str | None:
        """Execute (or resume) the run. Returns the terminal status, or None if handed back."""
        log = logger.bind(run_id=str(self.run_id), worker_id=self.worker_id)
        async with self.factory() as session:
            run = await load_run(session, self.run_id)
            if run.worker_id != self.worker_id:
                return None
            if run.status == CANCEL_REQUESTED and run.pass_count == 0:
                await self._finalize(run, cancelled=True)
                return CANCELLED
            self.pass_number = run.pass_count + 1
            retry_failed = run.retry_failed_requested
            values: dict[str, Any] = {"pass_count": self.pass_number}
            if run.started_at is None:
                values["started_at"] = utcnow()
            await self._owned_update(session, **values)
            last_seq = await session.scalar(
                select(func.max(RunCaseResult.sequence)).where(RunCaseResult.run_id == self.run_id)
            )
            await session.commit()
        self._sequence = count((last_seq or 0) + 1)
        log.info("run_execution_started", pass_number=self.pass_number, retry_failed=retry_failed)

        load = run.configuration_snapshot["load"]
        steps = steps_of(load)
        try:
            self.cases = await self._load_cases(run)
        except Exception as exc:
            log.warning("run_initialization_failed", error=str(exc)[:300])
            await self._fail(str(exc))
            return FAILED

        adapters: list[ModelAdapter] = []
        evaluation = run.configuration_snapshot.get("evaluation") or {}
        suites = [s for s in evaluation.get("suites") or [] if s in QUALITY_SUITES]
        self.eval_config = evaluation.get("config") or {}
        self.evaluators = evaluators_for(suites, self.eval_config) if suites else []
        self.simulate_tools = (
            "tool_calling" in suites and self.eval_config.get("tool_mode") == "simulated"
        )
        embedding_snapshot = evaluation.get("embedding_endpoint")
        if self.evaluators and embedding_snapshot:
            embed_adapter = self._adapter_from_snapshot(embedding_snapshot, None)
            adapters.append(embed_adapter)
            self.embedder = embed_adapter.embed
        self.inline_evaluators = list(self.evaluators)
        judge_snapshot = evaluation.get("judge_endpoint")
        judge_config = self.eval_config.get("judge") or {}
        if self.evaluators and judge_snapshot:
            judge_adapter = self._adapter_from_snapshot(judge_snapshot, None)
            adapters.append(judge_adapter)
            self.judge = Judge(
                adapter=judge_adapter,
                identity={
                    "endpoint": judge_snapshot.get("name"),
                    "endpoint_id": judge_snapshot.get("endpoint_id"),
                    "model_identifier": judge_snapshot.get("model_identifier"),
                },
                temperature=float(judge_config.get("temperature") or 0.0),
                max_tokens=int(judge_config.get("max_tokens") or 1024),
            )
            self.judge_concurrency = int(judge_config.get("concurrency") or 4)
            self.deferred_evaluators = [e for e in self.evaluators if e.suite in JUDGE_SUITES]
            self.inline_evaluators = [e for e in self.evaluators if e.suite not in JUDGE_SUITES]
        done = asyncio.Event()
        control: asyncio.Task[None] | None = None
        try:
            async with self.factory() as session:
                for rm in run.models:
                    adapter, rc = self._adapter_for(rm)
                    adapters.append(adapter)
                    state = ModelState(
                        run_model_id=rm.id,
                        adapter=adapter,
                        stream=rc.stream,
                        max_retries=rc.max_retries,
                        backoff_seconds=rc.retry_backoff_seconds,
                        planned=load.get("requests"),
                        warmup_planned=int(load.get("warmup_requests") or 0),
                    )
                    await self._existing(session, state)
                    self._models[rm.id] = state
                    self._model_status[rm.id] = rm.status
            await self._set_status(RUNNING)
            control = asyncio.create_task(self._control_loop(done))
            for rm in run.models:
                if self._stop.is_set():
                    raise StopRun
                await self._run_model(
                    rm, steps, int(load.get("warmup_requests") or 0), retry_failed
                )
        except StopRun:
            pass
        except Exception as exc:
            log.exception("run_execution_error")
            done.set()
            if control:
                await control
            await self.flush()
            await self._fail(f"Internal error during execution: {type(exc).__name__}: {exc}")
            return FAILED
        finally:
            done.set()
            if control:
                with contextlib.suppress(Exception):
                    await control
            for adapter in adapters:
                with contextlib.suppress(Exception):
                    await adapter.close()

        await self.flush()
        if self.judge is not None:
            log.info("judge_usage", calls=self.judge.calls, failures=self.judge.failures)
        if self.stop_reason == "lease_lost":
            log.warning("run_abandoned_lease_lost")
            return None
        if self.stop_reason == "shutdown":
            await self._release_for_resume()
            log.info("run_released_for_resume")
            return None
        status = await self._finalize(run, cancelled=self.stop_reason == "cancel")
        log.info("run_execution_finished", status=status)
        return status


# ------------------------------------------------------------------------------- helpers


async def compute_metric_rows(
    session: AsyncSession, run: BenchmarkRun
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Aggregate metrics for every model of a run from its stored results.

    Returns ``run_aggregate_metrics`` rows (overall, plus one group per ramp step) and a summary
    of failures used for the run's status. Used at finalization and for live metrics.
    """
    load = run.configuration_snapshot["load"]
    steps = steps_of(load)
    stream = bool(load.get("stream", True))
    failed = 0
    not_completed = 0
    errors: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    evaluation = run.configuration_snapshot.get("evaluation") or {}
    quality_suites = [s for s in evaluation.get("suites") or [] if s in QUALITY_SUITES]
    for rm in run.models:
        observations, by_step = await load_observations(session, rm.id)
        if quality_suites:
            for m in await quality_metrics(session, rm.id, observations):
                rows.append(_metric_row(run.id, rm.id, None, m))
        groups: list[tuple[int | None, list[performance.Observation], int | None]] = [
            (None, observations, load.get("requests"))
        ]
        if len(steps) > 1:
            groups += [(s.step, by_step.get(s.step, []), s.count) for s in steps]
        for step, obs, planned in groups:
            metrics = performance.compute(obs, planned_requests=planned, streamed=stream)
            if step is None:
                lookup = performance.as_lookup(metrics)
                failed += int(lookup["requests"]["failed"] or 0)
                not_completed += int(lookup["requests"].get("not_completed") or 0)
                for m in metrics:
                    if m.name == "errors":
                        for k, v in m.metadata.get("by_type", {}).items():
                            errors[k] = errors.get(k, 0) + v
            concurrency = next((s.concurrency for s in steps if s.step == step), None)
            for m in metrics:
                meta = dict(m.metadata)
                if step is not None:
                    meta["concurrency"] = concurrency
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "run_id": run.id,
                        "run_model_id": rm.id,
                        "load_step": step,
                        "metric_name": m.name,
                        "aggregation": m.aggregation,
                        "metric_value": m.value,
                        "metric_unit": m.unit,
                        "sample_count": m.sample_count,
                        # ORM attribute name (the column is "metadata").
                        "metadata_": meta,
                        "created_at": utcnow(),
                    }
                )
    return rows, {"failed_requests": failed, "not_completed": not_completed, "errors": errors}


async def load_run(session: AsyncSession, run_id: uuid.UUID) -> BenchmarkRun:
    run = await session.scalar(
        select(BenchmarkRun)
        .where(BenchmarkRun.id == run_id)
        .options(selectinload(BenchmarkRun.models), selectinload(BenchmarkRun.datasets))
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise LookupError(f"run {run_id} not found")
    return run


async def load_observations(
    session: AsyncSession, run_model_id: uuid.UUID
) -> tuple[list[performance.Observation], dict[int | None, list[performance.Observation]]]:
    """Measured attempts of one model as metric observations (warm-up excluded)."""
    rows = await session.execute(
        select(
            RunCaseResult.request_index,
            RunCaseResult.attempt_number,
            RunCaseResult.pass_number,
            RunCaseResult.is_final,
            RunCaseResult.status,
            RunCaseResult.error_type,
            RunCaseResult.load_step,
            RunCaseResult.started_at,
            RunCaseResult.completed_at,
            RunCaseResult.performance_metrics,
        ).where(
            RunCaseResult.run_model_id == run_model_id,
            RunCaseResult.phase == PHASE_MEASURED,
        )
    )
    observations: list[performance.Observation] = []
    by_step: dict[int | None, list[performance.Observation]] = {}
    for idx, attempt, pass_no, is_final, status, error_type, step, start, end, perf in rows:
        perf = perf or {}
        o = performance.Observation(
            request_index=idx,
            attempt_number=attempt,
            pass_number=pass_no,
            is_final=is_final,
            succeeded=status == RESULT_SUCCEEDED,
            started_at=start,
            completed_at=end,
            error_type=error_type,
            latency_ms=perf.get("latency_ms"),
            ttft_ms=perf.get("ttft_ms"),
            generation_ms=perf.get("generation_ms"),
            input_tokens=perf.get("input_tokens"),
            output_tokens=perf.get("output_tokens"),
            token_source=perf.get("token_count_source") or "unavailable",
            output_tokens_per_second=perf.get("output_tokens_per_second"),
            itl_mean_ms=perf.get("itl_mean_ms"),
        )
        observations.append(o)
        by_step.setdefault(step, []).append(o)
    return observations, by_step


def _metric_row(
    run_id: uuid.UUID, run_model_id: uuid.UUID, step: int | None, m: performance.MetricValue
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "run_id": run_id,
        "run_model_id": run_model_id,
        "load_step": step,
        "metric_name": m.name,
        "aggregation": m.aggregation,
        "metric_value": m.value,
        "metric_unit": m.unit,
        "sample_count": m.sample_count,
        "metadata_": dict(m.metadata),
        "created_at": utcnow(),
    }


async def quality_metrics(
    session: AsyncSession, run_model_id: uuid.UUID, observations: list[performance.Observation]
) -> list[performance.MetricValue]:
    """Quality aggregates for one model from its evaluation rows (final successful requests)."""
    rows = await session.execute(
        select(
            EvaluationResult.evaluator_name,
            EvaluationResult.suite,
            EvaluationResult.status,
            EvaluationResult.score,
            EvaluationResult.passed,
            EvaluationResult.label,
            EvaluationResult.metadata_,
        ).where(EvaluationResult.run_model_id == run_model_id)
    )
    eval_rows = [quality.EvalRow(*r) for r in rows]
    evaluated = await session.scalar(
        select(func.count(func.distinct(EvaluationResult.run_case_result_id))).where(
            EvaluationResult.run_model_id == run_model_id
        )
    )
    finals = performance.final_outcomes(observations)
    successful = sum(1 for o in finals.values() if o.succeeded)
    hashes: dict[str, list[str]] = {}
    answer_rows = await session.execute(
        select(RunCaseResult.case_identifier, RunCaseResult.response_data).where(
            RunCaseResult.run_model_id == run_model_id,
            RunCaseResult.phase == PHASE_MEASURED,
            RunCaseResult.is_final.is_(True),
            RunCaseResult.status == RESULT_SUCCEEDED,
        )
    )
    for identifier, data in answer_rows:
        digest = (data or {}).get("content_sha256")
        if digest:
            hashes.setdefault(identifier, []).append(digest)
    return quality.compute(
        eval_rows,
        evaluated_requests=evaluated or 0,
        successful_requests=successful,
        hashes_by_case=hashes,
    )


def _tool_output(tc: Any) -> ToolCallOutput:
    return ToolCallOutput(
        name=tc.name,
        arguments=tc.arguments if isinstance(tc.arguments, dict) else None,
        arguments_raw=tc.arguments_raw,
        arguments_error=tc.arguments_error,
        id=tc.id,
    )


def _model_output(response: ModelResponse) -> ModelOutput:
    return ModelOutput(
        content=response.content,
        tool_calls=[_tool_output(tc) for tc in response.tool_calls],
        finish_reason=response.finish_reason,
    )


def _answer_hash(response: ModelResponse) -> str:
    calls = [[tc.name, tc.arguments_raw] for tc in response.tool_calls]
    payload = json.dumps([response.content or "", calls], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stored_simulation(simulation: dict[str, Any], store: bool, limit: int) -> dict[str, Any]:
    """The simulation transcript as stored: final text follows the response-storage settings."""
    out = dict(simulation)
    final = simulation.get("final_content")
    if final is not None:
        out["final_content"] = final[:limit] if store else None
        out["final_content_chars"] = len(final)
    if not store:
        out["rounds"] = [{**r, "tool_results": []} for r in simulation.get("rounds", [])]
    return out
