# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added — Milestone 7 (context-window and quantization experiments)
- **Context-window suite** (`context_window`): cases carry the *requested* context length, and
  results are grouped by it. Per length: requests, success rate, overflow rate, out-of-memory
  rate, TTFT and latency p50/p95, estimated input tokens, **server-reported** input tokens (with
  their `token_count_source`), the actual / estimated ratio, suspected truncation (the server
  reports under 50% of the estimate), needle retrieval accuracy (overall and by early / middle /
  late position), and multi-fact recall. Requested, estimated, and actual sizes are always kept
  apart. The evaluation framework version is now `3`.
- **Needle dataset generator**: `POST /datasets/generate` builds deterministic
  needle-in-a-haystack and multi-fact retrieval datasets (requested lengths of 128 to 524,288
  tokens, depths, facts per case, repeats, seed). Sizes are estimated at 4 characters per token,
  and each case records the estimator. The same parameters always give the same content hash.
  `long_context` cases accept `expected_answers` (multi-fact), `context_length`, and
  `context_estimate`.
- New evaluator `needle_retrieval`: passes when all expected values appear in the answer. The
  score is the share found, the label is retrieved / partial / missed, and the position bucket
  comes from `answer_location`.
- Adapter `openai_compatible` 1.1.0 classifies server errors as `context_overflow` (not
  retried) or `out_of_memory`, using only the server's message.
- **Structured endpoint metadata**: the well-known model fields (family, revision,
  parameter_count, quantization_method, weight_precision, weight_bits, activation_precision,
  kv_cache_dtype) and deployment fields (serving_backend, backend_version, gpu_type, gpu_count,
  gpu_memory_gb, tensor/pipeline parallel size, max_model_len, gpu_memory_utilization,
  max_num_seqs, max_num_batched_tokens, server_args, driver/CUDA version, cpu, ram_gb) are
  type-checked. Blank values are dropped, so unknown stays unknown. Nothing is inferred from
  model names.
- **Optional GPU telemetry**: an endpoint can name a Prometheus exporter (`telemetry.url`):
  NVIDIA DCGM exporter, nvidia_gpu_exporter, or vLLM `/metrics`. It is scraped every
  `interval_seconds` while the model is measured. Samples go to `resource_samples` and become
  `gpu.memory_used_mb` (max, mean), `gpu.memory_total_mb`, `gpu.utilization_pct`, `gpu.power_w`,
  `gpu.temperature_c`, `gpu.kv_cache_usage_pct`, and `telemetry.samples`. A missing or failing
  exporter never fails a run; its metrics are null, with the reason.
- **Deployment variants** in comparisons:
  - declared settings per model, with undeclared ones shown as unknown;
  - warnings when the model family differs or is unknown, quantization is not declared, the
    datasets differ, or no telemetry was collected;
  - key quality, latency, throughput, and GPU metrics side by side;
  - context-window metrics per requested length.

  Quantization itself is not a suite: run the same datasets on each variant, then compare the
  runs.
- Reports gain a **Context window** section (retrieval accuracy and TTFT by requested length;
  requested, estimated, and server-reported tokens) and a **Resources** section (GPU memory,
  utilization, power, and KV cache over time). The configuration section lists the variant
  fields, including unknown ones.
- Migration `0007`: `run_aggregate_metrics.context_length`,
  `model_endpoints.telemetry_configuration`, and the `resource_samples` table.
- API: `POST /datasets/generate` and `GET /runs/{id}/telemetry`; `telemetry` on endpoints,
  `context_length` on metric rows, and `variants` in comparison results.
- CLI: `dataset generate-needle`. `run results` prints the context-window table.
- UI:
  - a Generate tab on Datasets;
  - model-variant and telemetry fields on the endpoint form;
  - Context window and Resource telemetry cards on the run page;
  - a Deployment variants card on Compare;
  - the Context window evaluation type in the wizard.
- Mock server: `X-Mock-Mode: needle`, `X-Mock-Needle-Reach`, `X-Mock-Max-Context`,
  `X-Mock-Prompt-Token-Cap`, and a fake DCGM / vLLM `GET /metrics` page.

### Security — Milestone 7
- Telemetry URLs pass the same outbound network policy as model endpoints: they are validated
  when saved and checked before every scrape. Redirects are not followed, proxy variables are
  ignored, responses are capped at 4 MB, and URLs with embedded credentials are rejected.
  `redact_endpoints` also hides telemetry URLs in reports.

### Added — Milestone 6 (reports and comparison)
- Reports for finished runs, in five formats: a self-contained **HTML report** (no scripts and
  no external resources, so it works offline; light and dark themes), a **JSON export** (all
  the data behind the report), a **results CSV** (one row per recorded attempt), a **metrics
  CSV**, and a **Markdown summary**. Reports are stored in the database as immutable
  snapshots, with their sha256 and options (`reports` table, migration `0006`).
- The HTML report contains warnings, a per-model summary, charts, every metric (with the reason
  where a value is unavailable), failed requests and failed quality checks, the configuration
  (models, effective generation settings, declared model and hardware metadata, dataset
  versions and hashes, load, evaluators and versions), the methodology (warm-up exclusion,
  outcome and error-rate rules, the percentile method, throughput window, token-count sources,
  metric and evaluator versions), and how to reproduce the run (CLI YAML and API body).
- Charts are server-rendered inline SVG with native tooltips: latency, TTFT, and output tokens/s
  histograms (one panel per model), throughput and error rate over time, quality pass rate by
  evaluator, quality vs latency and quality vs throughput (model-labeled points), and input
  size vs latency. Missing values are never drawn as zero.
- Options `redact_content` (leave out generated text, error messages, rationales, and claim
  evidence) and `redact_endpoints` (replace endpoint URLs). Reports cover up to
  `REPORT_MAX_RESULTS` attempts (default 50000) and say when they are truncated.
- **Run comparison**: the models of up to 10 runs side by side against a baseline. Each metric
  shows its delta and relative change and whether that is better or worse for that metric.
  Every configuration difference is listed with a severity, and the result is labeled
  `equivalent_configuration`, `partially_controlled`, or `not_directly_comparable` — overall,
  for performance, and for quality. Critical: dataset content, prompt-builder, metric,
  evaluator, judge, and evaluation settings. Major: generation (temperature, top_p, max_tokens,
  seed), load profile, concurrency, request count, streaming. Minor: warm-up, timeout, retries,
  suites. Expected differences between the models themselves are shown but don't lower the label.
- Per-case changes against the baseline: pass→fail and fail→pass per evaluator, and new and fixed
  request errors, using the first measured request of each case.
- Comparisons can be saved (`comparisons` table) and exported as HTML, CSV, or JSON. A saved
  comparison is recomputed when read, and reports when a compared run has been deleted.
- **Re-run**: `POST /runs/{id}/rerun` creates a run with the same endpoints, dataset versions,
  suites, evaluation, generation, and load settings.
- API: `POST/GET /runs/{id}/reports`, `GET /reports`, `GET/DELETE /reports/{id}`,
  `GET /reports/{id}/download`, `GET /compare`, `GET /compare/export`, `POST/GET /comparisons`,
  `GET/DELETE /comparisons/{id}`, `GET /comparisons/{id}/export`, `POST /runs/{id}/rerun`.
- CLI: `report generate`, `report list`, `report download`, `compare` (also `run compare`), and
  `run rerun`. `run create --wait` now saves the formats listed under `report.formats` in the config
  (`--report-dir`).
- UI: a Reports card on the run page (format, redaction options, open, download, delete),
  **Compare** and **Re-run** buttons, a Reports page, and a Compare Runs page (run picker,
  baseline, comparability badges, differences, metric deltas by family, per-case changes,
  export, saved comparisons). The quality-failures view filters by evaluator.

### Security — Milestone 6
- Report downloads are served with `Content-Security-Policy: default-src 'none';
  style-src 'unsafe-inline'; img-src data:; sandbox`, `nosniff`, and `no-store`. Every value
  in HTML reports is escaped, and CSV cells that start with a formula character are prefixed
  with `'`.
- nginx no longer adds its UI policy to `/api/` responses, so a report gets one policy, not two.

### Added — Milestone 5 (RAG and evidence evaluation)
- Two new suites, `rag` (rag cases) and `hallucination` (rag and long_context cases). They read
  only the evidence supplied with each case: the retrieved contexts, or the paragraphs of a
  long-context document.
- Claim pipeline (`app/evaluators/evidence.py`): `ClaimExtractor` and `ClaimVerifier`
  interfaces. Every claim is classified as supported, unsupported, contradicted, or unverifiable
  (ambiguous, flagged for review) and stored with its evidence excerpt, source identifiers
  (passage, document, chunk, source, page), rationale, confidence (judge only), method, and a
  human review status (`not_reviewed`). The default implementation is lexical: word coverage
  decides support, claims may combine two passages, a number that no passage mentions is a
  contradiction, and a polarity mismatch is sent to review rather than called a contradiction.
- RAG evaluators: faithfulness (supported / (supported + unsupported + contradicted);
  contradicted claims fail the case; not applicable when there are no claims), answer
  correctness, answer relevance (judge only; skipped without one), context relevance, context
  precision (average precision from `relevant` labels), context recall (reference claims found
  in the contexts), citation precision (the cited passage supports the claim), citation recall
  (expected citations cited), and abstention (`answerable: false` cases should be declined).
- Hallucination evaluator `claim_support`, plus claim-level metrics: `hallucination.*`
  (unsupported-claim rate, contradiction rate, unverifiable rate, claim support precision,
  evidence coverage, answers with contradictions, answers despite insufficient evidence,
  abstention precision and recall) and `rag.*` (pooled claim faithfulness, contradicted-claim
  rate). Missing reference data makes an evaluator `not_applicable`; nothing is scored without it.
- Optional LLM judge (`evaluation.judge`, opt-in, off by default): any registered endpoint,
  called through its adapter. It extracts and verifies claims and rates answer and context
  relevance, with a versioned prompt (`judge_prompt_version`). Case content is wrapped as
  untrusted data, replies must be a JSON object of a fixed shape, and quoted evidence is checked
  against the passage it names. A judge failure fails the evaluation; it never passes. With a
  judge, the RAG and hallucination evaluators run after each model's measurement with their own
  concurrency (`judge.concurrency`), so the judge never slows the measured requests. A resumed
  run judges earlier answers from their stored text, or records a failed evaluation when the
  text was not stored in full. The run snapshot records the judge endpoint (secret-free),
  whether it is also a model under test, and the evidence method.
- Dataset schema: `retrieved_contexts[].relevant` (optional relevance label). The RAG sample now
  carries relevance labels. No database migration: claims live in `evaluation_results.evidence`.
- Settings: `faithfulness_threshold` (0.8) and `support_threshold` (0.65, lexical).
- UI: RAG and Hallucination check are selectable in the wizard, with judge, faithfulness, and
  support settings and a data-sharing notice; an "Evidence and claims" card on the run page;
  judging progress; and a claims table (verdict, evidence, source) in the result drawer.
- CLI: `evaluation.judge.endpoint` takes an endpoint name; `run results` lists `rag.*` and
  `hallucination.*` metrics.
- Mock model server: answers judge prompts with deterministic verdicts, so the judge path works
  without a real judge model. The smoke test runs a RAG run that judges itself.

### Added — Milestone 4 (Deterministic evaluators and tool-calling)
- Evaluator framework (`app/evaluators/`): every evaluator has a name, a version, a suite, a
  kind, and the case types it applies to, and it returns a status (`completed`, `failed`,
  `skipped`, `not_applicable`, `requires_review`), a nullable score, pass/fail, a label, a
  rationale, and evidence. The registry is listed at `GET /api/v1/evaluators`. Each run snapshot
  records the evaluators with their versions, the evaluation framework version, and the
  quality metrics version.
- Answer quality: exact match, normalized match (Unicode NFKC, case, punctuation, English
  articles, whitespace), contains-reference (whole words), token F1 with a configurable pass
  threshold, a refusal-behaviour heuristic for safety cases, and optional embedding similarity
  through an `/embeddings` endpoint (reported as similarity, not correctness). Acceptable
  alternative answers come from `metadata.acceptable_answers`.
- Structured output: JSON validity (raw, or inside a code fence when allowed; labeled
  separately), JSON Schema conformance (Draft 2020-12) with the violating paths, and
  expected-JSON field match (fraction of expected leaf fields).
- Tool calling: tool selection (correct, missing call, unnecessary call, wrong tools),
  argument names, argument schema validity, required arguments, argument values, call order,
  invented tools, final answer, and end-to-end task success. Run metrics:
  `tool.selection_accuracy`, argument accuracies, call precision and recall, and missing,
  unnecessary, invented-tool, and malformed-argument rates. Argument metrics count only cases
  where the expected tool was selected, so selection and argument errors stay separate.
- Simulated tool execution (`tool_mode: simulated`, the default): valid calls are answered with
  the dataset's fixture results (or a structured error), and the model is asked to continue,
  for up to `max_tool_rounds` rounds. **Nothing is executed.** `generation_only` inspects the
  calls without follow-up requests. Real tool execution ("integration" mode) is rejected. Follow-up
  requests are not part of the performance metrics.
- Evaluation runs in the worker on final, successful, measured attempts, using the full
  response. Results are stored in `evaluation_results` (migration `0005`) in the same
  transaction as the attempt. Failed requests are not evaluated; `quality.evaluated_requests`
  reports how many were. Quality rates count only completed evaluations; a rate with no
  applicable case is null with a reason, never 0. `quality.output_consistency` reports
  identical answers on repeated cases.
- Evaluation profiles (`evaluation_profiles`, migration `0005`): `/api/v1/profiles` CRUD. A run
  can start from a profile (`profile_id`); fields set in the request win, and the run snapshot
  copies the settings, so editing a profile never changes past runs.
- Runs validate that the selected datasets contain case types for each quality suite (422
  otherwise).
- Results API: `evaluator` and `evaluation` (`passed` / `failed`) filters, evaluation
  summaries on each result, and full evaluations on the result detail.
- UI: quality settings in the New Evaluation wizard (tool mode, F1 threshold, embedding
  endpoint, code-fence switch), "Start from a profile" and "Save as profile"; an Evaluation
  Profiles page; Quality and Tool-call metrics cards, a Quality failures tab, a Checks column,
  and per-request evaluations and the simulated tool transcript on the run page.
- CLI: `profile:` and `evaluation:` keys in run configs (endpoint names for
  `evaluation.embedding.endpoint`), and a quality section in `run results`.
- Mock model server: scripted responses (`MOCK_SCRIPT`), multiple tool calls, answers after tool
  results, and deterministic `/v1/embeddings`.
- Smoke test: the mock run also evaluates answer quality.

### Added — Milestone 3 (Run engine and performance metrics)
- Benchmark runs (migration `0004`): `benchmark_runs`, `run_datasets`, `run_models`,
  `run_case_results`, and `run_aggregate_metrics`. Each run stores an immutable, secret-free
  configuration snapshot: the effective request settings per endpoint (secret references only),
  generation and sampling mode, the resolved load plan, dataset versions with content hashes,
  and the framework, metrics, and prompt-builder versions.
- Run state machine: created, queued, initializing, running, finalizing, then completed,
  completed_with_errors, cancelled, or failed. A run is completed_with_errors when at least one
  measured request failed after its retries, or never ran.
- Worker queue backed by the database. A worker claims a run with an atomic conditional update
  and holds a renewable lease. Redis is only a wake-up signal. If a worker stops, it hands the
  run back to the queue; if it dies, another worker resumes the run once the lease expires.
  Resumed runs skip requests that already have a final result, so nothing is duplicated.
- Load profiles: smoke (at most 5 requests at concurrency up to 2), sequential, fixed
  concurrency, ramp (increasing concurrency steps, metrics per step), and sustained (hold N
  requests in flight for a duration, with an optional request cap). Endpoints run one after
  another, so models don't compete for the same server.
- Warm-up requests run sequentially before the measured phase, are never retried, are stored
  with `phase = warmup`, and are excluded from every aggregate.
- Streaming and non-streaming execution through the adapter layer. Per-request timeouts, retries
  with exponential backoff (each attempt is its own stored row), and cancellation, which stops
  in-flight requests.
- Every attempt is stored: status, error type, HTTP status, latency, TTFT, token counts with
  their source, chunk timing, response text (`RUN_STORE_RESPONSES`, capped by
  `RUN_RESPONSE_MAX_CHARS`), and the request settings.
- Performance metrics (metrics version 1) computed from per-request observations: request
  counts, retries, error and timeout rates, errors by type, latency / TTFT / generation time /
  inter-token latency distributions (mean, stddev, min, p50, p90, p95, p99, max; linear
  interpolation), per-request output tokens per second, token totals, measurement window,
  request throughput, and output and total token throughput. A metric that can't be measured is
  stored as null with a reason, never as 0 (for example, TTFT without streaming, or token
  throughput when the server did not report token counts).
- API: `/api/v1/runs` list, summary, create, detail, delete, `cancel`, `retry-failed`, `results`
  (filter by model, status, phase, search), `results/{id}`, `metrics` (live while running), and
  `events`, a Server-Sent Events stream with progress, per-request, and lifecycle events that
  supports `Last-Event-ID`.
- Datasets and projects used by an active run can't be deleted (409).
- CLI: `openllm-eval run create --config bench.yaml [--wait]`, plus `run status [--watch]`,
  `run results`, `run list`, and `run cancel`.
- UI:
  - The New Evaluation wizard has performance settings: profile, requests, concurrency,
    warm-up, streaming, max tokens, temperature, timeout, retries, ramp steps, and duration.
    They are validated as you type. Start Evaluation creates the run and opens it.
  - New Benchmark Runs list.
  - New run page with live progress over SSE, per-model progress, a live request feed, a
    metrics table with reasons for unavailable values, per-step ramp tables, a results browser
    (all, failures, warm-up) with a detail drawer, and the configuration and methodology used.
  - The Dashboard shows recent evaluations.
- Mock model server: `X-Mock-Fail-If-Contains`, `X-Mock-Fail-First`, and `X-Mock-Fail-Status`
  headers to simulate failures.
- Smoke test: with the mock profile, runs a real benchmark (1 warm-up and 8 measured requests)
  through the worker and checks the metrics.

### Added — Milestone 2 (Datasets)
- Versioned datasets (migration `0003`): `datasets`, `dataset_versions`, and `dataset_cases`
  tables. Versions and cases are immutable (the ORM refuses updates). Each version records a
  SHA-256 `content_hash` of its normalized JSONL export, plus record, type, tag, and split counts.
- Dataset schema version 1 with eight case types: chat, instruction, qa, structured_output
  (the JSON Schema is checked, and so is the expected output), tool_call (expected calls are
  checked against the tool definitions), rag (citations must reference supplied documents),
  long_context, and safety. Unknown fields are kept and produce a warning.
- JSONL and CSV import with line-numbered errors and warnings. CSV supports JSON-valued columns
  and `metadata.x` columns. Limits on file size, record count, and record size; UTF-8 only.
- The content hash is reproducible across CSV and JSONL input, key order, and whitespace. Run
  `sha256sum` on an export and you get the stored hash.
- API: `/api/v1/datasets` CRUD, `validate` (dry run), `versions` (upload a new version, list,
  switch current), `preview` (filters for tag, type, and case id), `cases/{id}`, `export` (streamed
  JSONL with an `X-Content-SHA256` header), `duplicate`, `case-types`, and `samples` with install.
  Uploads accept JSON bodies or `multipart/form-data`.
- Five bundled sample datasets (original synthetic content, CC0-1.0): general QA, tool calling,
  RAG grounding, structured output, and long-context needle retrieval. They are installed on
  request, not automatically.
- CLI: `openllm-eval dataset validate PATH` (offline), `dataset list`, and `dataset import PATH`.
- Web UI: a **Datasets** page (my datasets, built-in samples, import with a validation report),
  a dataset detail page (records browser, versions with export and switch, details), and the
  **New Evaluation** wizard: Basic setup → Evaluation types → Datasets → Review. The draft
  persists in the browser between steps. "Start evaluation" stays disabled until Milestone 3.
- Projects show a dataset count. Deleting a project deletes its datasets.
- `DATASET_MAX_UPLOAD_BYTES`, `DATASET_MAX_RECORDS`, and `DATASET_MAX_RECORD_BYTES` settings.
- The smoke test imports a dataset, exports it, and verifies the hash.
- `scripts/deploy.sh` (`make deploy`) installs or upgrades the stack. It stops containers from
  older installs, including ones that ran under a different Compose project name, carries the
  database over (backing up any database it replaces), checks the ports, starts the new version,
  and runs the smoke test.

### Added — Milestone 1 (Endpoint management and the OpenAI-compatible adapter)
- `model_endpoints` table (migration `0002`): endpoints can be global or scoped to a project.
  Each records its request configuration, declared deployment metadata, tags, enabled flag, and
  the result of its last connection test.
- Model adapter interface (`ModelAdapter`), an adapter registry, and an `openai_compatible`
  adapter for vLLM, llama.cpp server, Ollama `/v1`, and hosted APIs. It supports streaming and
  non-streaming chat completions, SSE parsing, tool-call assembly, server-reported token usage,
  and a `/models` check.
- Timing: latency, time to first token (streaming only; the first event carrying content,
  reasoning, or tool-call data), generation time, and per-event offsets. Unknown values are
  `null`, never `0`.
- Secret references (`env:OPENLLM_SECRET_*`). Keys live in `secrets.env` and are never stored,
  returned, logged, or included in error messages.
- Outbound network policy (SSRF protection): http/https only, no credentials in URLs, no
  redirects, internal service names and cloud-metadata addresses always blocked, and private
  networks blocked unless allowed. DNS is rechecked before every request. Responses are size- and
  time-bounded.
- API: `GET /api/v1/adapters`; `/api/v1/projects` CRUD with archive and restore;
  `/api/v1/endpoints` CRUD with `clone`, `test`, and `metadata`.
- CLI: `openllm-eval endpoint list` and `openllm-eval endpoint test NAME_OR_ID`.
- Mock OpenAI-compatible server (`python -m app.testing.mock_openai`, Compose profile `mock`) for
  demos, contract tests, and the smoke test.
- Web UI redesign: a sidebar navigation grouped by section, a dashboard with real
  endpoint and health data, a **Models / Endpoints** page (filter, search, add or edit form,
  connection test with metrics, clone, enable or disable, delete), and a **Projects** page. The
  Inter font and the icons are bundled; the UI makes no remote requests.

### Fixed
- The database connection failed when `POSTGRES_PASSWORD` contained URL-special characters such
  as `@`. Compose now passes discrete `POSTGRES_*` settings, and the backend builds an escaped URL.
  `.env.example` documents single-quoting passwords that contain `$`.
- `scripts/smoke-test.sh` reads `API_PORT` / `FRONTEND_PORT` from `.env`.

### Added — Milestone 0 (Foundation)
- Repository skeleton and Apache-2.0 license.
- FastAPI backend with environment-based configuration (pydantic-settings).
- Structured JSON logging (structlog) with secret redaction and per-request IDs (`X-Request-ID`).
- Consistent API error envelope.
- `GET /api/v1/health`, `GET /api/v1/health/ready`, and `GET /api/v1/version`. The OpenAPI schema
  is served at `/api/openapi.json`.
- SQLAlchemy 2 async data layer and Alembic migrations (`0001`: `projects` table).
- Worker process with a Redis heartbeat (`python -m app.execution.worker`).
- `openllm-eval` CLI with `version` and `health` commands.
- React + TypeScript web UI: application shell with the main navigation and a dashboard
  showing system health.
- Docker Compose stack (frontend, api, worker, migrate, postgres, redis) and a dev override
  with hot reload.
- GitHub Actions CI: backend tests on PostgreSQL/Redis and SQLite, frontend tests and build,
  and an end-to-end Compose smoke test.
