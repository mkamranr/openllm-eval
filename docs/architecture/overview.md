# Architecture overview

OpenLLM-Eval is a **modular monolith**: one Python backend codebase deployed as two process types
(API and worker), plus a static React UI.

```
browser ──► frontend (nginx :8080 → host :3000)
              │  static UI + reverse proxy for /api
              ▼
            api (FastAPI/uvicorn :8000) ──► postgres (internal network only)
              │                         └─► redis    (internal network only)
            worker (python -m app.execution.worker) ──► redis, postgres
              │
              └─► model endpoints (vLLM, Ollama, hosted APIs) via adapters + network policy
```

## Compose services

| Service | Image | Purpose | Networks |
|---|---|---|---|
| `postgres` | `postgres:16.6-bookworm` | Durable metadata and results | internal |
| `redis` | `redis:7.4.1-bookworm` | Job coordination and worker heartbeats | internal |
| `migrate` | backend image | One-shot `alembic upgrade head` | internal |
| `api` | backend image | REST API under `/api/v1` | internal, egress |
| `worker` | backend image | Background evaluation runs (heartbeat only in M0) | internal, egress |
| `frontend` | `nginx-unprivileged` + built UI | UI and `/api` reverse proxy | internal, egress |
| `mock-llm` | backend image (profile `mock`) | Fake OpenAI-compatible server for demos and smoke tests | egress |

The `internal` network has `internal: true`, so Postgres and Redis have no host ports and no
outbound access. The API and worker also join `egress`, because model endpoints usually live on
other hosts.

## Backend layout (Milestone 4)

```
backend/app/
  main.py                 create_app(): settings, logging, middleware, error handlers, routers
  api/router.py           /api/v1 router
  api/routes/health.py    /health, /health/ready, /version
  api/routes/projects.py  /projects CRUD, archive/restore
  api/routes/endpoints.py /adapters, /endpoints CRUD, clone, test, metadata
  api/routes/datasets.py  /datasets CRUD, validate, versions, preview, cases, export, samples
  api/routes/runs.py      /runs CRUD, cancel, retry-failed, results, metrics, events (SSE)
  api/routes/evaluation.py  /evaluators, /profiles CRUD
  evaluators/base.py      Evaluator interface, EvaluationOutcome, ModelOutput
  evaluators/answer.py    exact / normalized / contains / token F1 / refusal / embedding similarity
  evaluators/structured.py  JSON parsing, JSON Schema conformance, expected-field match
  evaluators/tools.py     tool-call analysis, tool evaluators, fixture-only tool simulation
  evaluators/registry.py  registry, evaluation framework version, evaluate_case()
  metrics/quality.py      evaluation rows → quality.* and tool.* aggregates
  api/uploads.py          JSON or multipart upload parsing with size caps
  datasets/schema.py      dataset schema v1: case types as pydantic models
  datasets/validation.py  JSONL/CSV parsing, line-numbered issues, normalization, content hash
  datasets/samples/       bundled CC0 sample datasets
  adapters/types.py       GenerationRequest, ModelResponse, Timing, TokenUsage, AdapterError
  adapters/base.py        ModelAdapter interface, EndpointRuntimeConfig, AdapterContext
  adapters/openai_compatible.py  /chat/completions (stream + non-stream), /models
  adapters/registry.py    adapter_type → class
  core/network_policy.py  outbound URL/IP policy (SSRF protection)
  core/secrets.py         env:OPENLLM_SECRET_* references
  services/               project, endpoint, and dataset business logic
  testing/mock_openai.py  mock OpenAI-compatible server (tests, demos, smoke test)
  api/deps.py             dependencies that read shared resources from app.state
  core/config.py          Settings (pydantic-settings), DB URL normalisation
  core/logging.py         structlog JSON/console output with secret redaction
  core/middleware.py      X-Request-ID assignment, request logging
  core/exceptions.py      {"error": {code, message, request_id, details}} envelope
  core/lifecycle.py       lifespan: creates and disposes the DB engine and Redis client
  db/base.py              Declarative base, naming convention, portable UUID/JSON(B) types
  db/session.py           async engine and session factory
  db/models/              ORM models (projects, endpoints, datasets, runs, results, metrics)
  db/migrations/          Alembic environment (async) and versions
  execution/worker.py     worker process: heartbeat, claims queued runs, graceful hand-back
  execution/engine.py     RunExecutor: lease, warm-up, load profiles, retries, cancel, flush, finalize
  execution/plan.py       resolves a load profile into steps, with server limits
  execution/prompting.py  dataset case → GenerationRequest (versioned prompt builder)
  execution/queue.py      Redis wake-up signal for queued runs
  metrics/stats.py        percentiles (linear interpolation), mean, stddev
  metrics/performance.py  per-request observations → aggregate performance metrics
  execution/heartbeat.py  Redis heartbeat keys shared by the worker and the API
  schemas/                Pydantic API schemas
  cli/main.py             `openllm-eval` CLI (typer)
```

Later milestones add judges (M5) and `reporting/` (M6). 

## Run execution

The API validates a run request, resolves the load plan, writes the run with its secret-free
snapshot as `queued`, and pushes a wake-up signal to Redis. A worker claims it with an atomic
conditional `UPDATE` and holds a lease. It then benchmarks each endpoint in turn: warm-up
requests first, sequentially, then the measured requests at the profile's concurrency. Each
attempt goes through the endpoint's adapter. Results are buffered and flushed about once a
second; each flush renews the lease and checks for cancellation. At the end, the worker computes
the aggregates from the stored per-request rows (warm-up excluded) and stores them with the
metrics version.

When a run has quality suites, the worker evaluates each final, successful, measured attempt as
it completes, using the full in-memory response rather than the truncated stored copy. For tool
cases in simulated mode it first answers the model's calls with dataset fixtures (validated
against the tool schemas; nothing is executed) and sends follow-up requests until the model
answers or `max_tool_rounds` is reached; those follow-ups are excluded from performance
metrics. Evaluation rows are buffered with their attempt and written in the same flush. At the
end, quality aggregates are computed from the stored evaluation rows. The UI follows a run through `GET /runs/{id}/events` (Server-Sent Events read
from the database), so any API replica can serve it.

## Key decisions

- **Async everywhere.** The stack uses SQLAlchemy 2 async with psycopg 3, `redis.asyncio`, and
  (from M1) `httpx.AsyncClient`. Network I/O is always bounded by timeouts.
- **Portable types.** `sqlalchemy.Uuid` and `JSON().with_variant(JSONB, "postgresql")` let the
  same models and migrations run on PostgreSQL in production and SQLite in fast local tests. CI
  runs the suite on both.
- **Readiness semantics.** `/health` is liveness and never touches dependencies. `/health/ready`
  returns 503 if the database or Redis is unreachable. Worker presence is *reported* but doesn't
  gate readiness, because the API can still serve reads with no worker running.
- **Worker and queue (M3).** Workers publish a heartbeat (`openllm:worker:heartbeat:<id>`, TTL
  15 s). No job library is used. The database is the queue: run status, plus a lease that a worker
  claims with a conditional `UPDATE`. This keeps queue state transactional with the results, makes
  resume after a crash a plain query, and means Redis loss can delay a run but never lose one.
  Redis only carries a wake-up signal. Each worker runs one benchmark at a time, so concurrent
  benchmarks don't distort each other's measurements.
- **Offline by default.** FastAPI's Swagger/ReDoc pages are disabled because they load JavaScript
  from a CDN. The raw OpenAPI schema is served at `/api/openapi.json`. A locally bundled docs UI
  can be added later.
- **Single origin.** The UI calls `/api/...` on its own origin, through the Vite proxy in dev and
  nginx in containers, so CORS is off by default.
- **All model calls go through adapters.** Services and the API never call model servers
  directly. An adapter receives an `AdapterContext` (network policy, secret resolver, size limit,
  optional test transport) and returns a `ModelResponse`. Failures are values
  (`ModelResponse.error` with a typed `AdapterError`), not exceptions, so a benchmark can record
  them per case.
- **Honest timing.** TTFT is measured only for streamed responses, from just before the request
  is sent to the first event that carries content, reasoning, or tool-call data. Role-only
  events don't count. Token counts carry a `source` (`server_reported` / `unavailable`) and are
  never invented.
- **Error envelope.** Every error has the same shape and includes the request ID. Validation
  errors omit the submitted input, and 500s never include exception text.
