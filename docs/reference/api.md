# REST API reference

Base path: `/api/v1`. The machine-readable schema is served at `/api/openapi.json`. In the
Compose deployment, the UI's nginx proxies `/api`, so the API is reachable on both port 3000
and port 8000.

## Conventions

- **Authentication**: every route except `/health*`, `/version` and `/auth/providers`,
  `/auth/login` and `/auth/negotiate` needs a signed-in user. Scripts send an API token as
  `Authorization: Bearer oel_…`. The UI uses the `oel_session` cookie; with a cookie, requests
  other than GET and HEAD must also send `X-OpenLLM-CSRF: 1`. GET and HEAD need the `viewer`
  role, other methods `editor`, and `/users`, `/settings/auth` and `/audit` need `admin`.
  Errors: `401 not_authenticated`, `403 forbidden`, `403 password_change_required` (only
  `/auth/me`, `/auth/change-password` and `/auth/logout` work until the password is changed),
  `403 csrf_failed`, `429 account_locked`.

- **JSON** in and out. Dataset uploads also accept `multipart/form-data`.
- **Pagination**: list endpoints take `limit` and `offset` and return
  `{"items": [...], "total": n, "limit": l, "offset": o}`.
- **Errors** share one envelope, and every response carries an `X-Request-ID` header:

  ```json
  {"error": {"code": "validation_error", "message": "…", "request_id": "…", "details": [{"loc": ["body", "load", "concurrency"], "msg": "…"}]}}
  ```

  Status codes: `401` not signed in, `403` not allowed, `404` not found, `409` conflict (for example a duplicate name, or deleting a
  dataset in use), `422` validation error, `503` not ready.
- **Nulls**: a metric or score that could not be measured is `null`, with a reason in its
  `metadata`. It is never `0`.
- **Secrets** never appear in responses. Endpoints expose only their `secret_reference` name.

## Health

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness, always 200 |
| GET | `/health/ready` | Database, Redis, and worker heartbeat. Returns 503 when not ready. With tool execution enabled, also `tool_runner` (informational) |
| GET | `/version` | Framework and API versions |

## Authentication

| Method | Path | Description |
|---|---|---|
| GET | `/auth/providers` | Public. Which sign-in options to show: password, directory, Windows single sign-on |
| POST | `/auth/login` | `{"username", "password"}` → the user (`/auth/me` shape), and sets the session cookie. Needs `X-OpenLLM-CSRF: 1` |
| GET | `/auth/negotiate` | Kerberos (SPNEGO) sign-in: `401` with `WWW-Authenticate: Negotiate`, then the browser retries with a ticket |
| POST | `/auth/logout` | End the current session |
| GET | `/auth/me` | The signed-in user: `username`, `role`, `source`, `auth_method`, `must_change_password`, `auth_enabled` |
| POST | `/auth/change-password` | `{"current_password", "new_password"}`; local accounts only. Ends your other sessions |
| GET / POST | `/auth/tokens` | Your API tokens, or create one (`{"name", "expires_in_days"}`, null = never). The `token` is in the response only once |
| DELETE | `/auth/tokens/{id}` | Revoke a token |

## Users and authentication settings (admin)

| Method | Path | Description |
|---|---|---|
| GET / POST | `/users` | List, or create a local user (`username`, `role`, optional `password`; without one, `initial_password` is generated and returned once) |
| GET / PATCH / DELETE | `/users/{id}` | Read; change `display_name`, `email`, `role`, `role_pinned`, `disabled`; or delete. `409 last_admin` protects the last active administrator |
| POST | `/users/{id}/reset-password` | Local users only. Returns `temporary_password` (unless one was given) and requires a change at the next sign-in |
| DELETE | `/users/{id}/sessions` | Sign the user out everywhere |
| GET / PUT | `/settings/auth` | LDAP/AD and Kerberos settings. Secrets are write-only: send `ldap.bind_password` / `kerberos.keytab_base64` to set them, `clear_…` to remove them; responses only say `bind_password_set` / `keytab_set` |
| POST | `/settings/auth/test-connection` | Service bind and search-base check, with the saved or the submitted settings |
| POST | `/settings/auth/test-user` | `{"username", "password"}` → the DN, groups and role the user would get; no session is created |
| GET | `/audit` | Audit events (`actor`, `action`, `since`, `until`, `limit` ≤ 500, `offset`) |

## Tools

| Method | Path | Description |
|---|---|---|
| GET | `/tools/status` | Integration mode: `enabled` (TOOL_EXECUTION_ENABLED), `reachable`, `detail`, `registry_sha256`, `registry_error`, and the declared `tools` (name, description, version, kind, parameters, timeout, output limit). Read-only: tools come from `tools/tools.yaml` |

## Projects

| Method | Path | Description |
|---|---|---|
| GET / POST | `/projects` | List (`include_archived`) or create |
| GET / PATCH / DELETE | `/projects/{id}` | Read, update or archive (`{"archived": true}`), or delete |

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/adapters` | Available adapter types |
| GET / POST | `/endpoints` | List (`project_id`, `enabled`) or create |
| GET / PATCH / DELETE | `/endpoints/{id}` | |
| POST | `/endpoints/{id}/clone` | Copy an endpoint |
| POST | `/endpoints/{id}/test` | Send one test request; returns latency, TTFT, tokens, and a response preview |
| GET | `/endpoints/{id}/metadata` | Models listed by the server (`/models`) |

`model_metadata` and `deployment_metadata` are free-form objects, but the well-known keys below
are type-checked and blank values are dropped. Unknown stays unknown, and nothing is inferred:

- model: `family`, `revision`, `parameter_count`, `quantization_method`, `weight_precision`,
  `weight_bits`, `activation_precision`, `kv_cache_dtype`;
- deployment: `serving_backend`, `backend_version`, `gpu_type`, `gpu_count`, `gpu_memory_gb`,
  `tensor_parallel_size`, `pipeline_parallel_size`, `max_model_len`, `gpu_memory_utilization`,
  `max_num_seqs`, `max_num_batched_tokens`, `server_args`, `driver_version`, `cuda_version`,
  `cpu`, `ram_gb`.

`telemetry` is optional; set it to `null` to remove it. Example: `{"url":
"http://gpu-host:9400/metrics", "interval_seconds": 2, "gpu_indices": [0, 1],
"timeout_seconds": 2}`. It names a Prometheus text page (NVIDIA DCGM exporter,
nvidia_gpu_exporter, or vLLM `/metrics`) that is scraped while the model is measured. The URL
must pass the network policy.

## Datasets

| Method | Path | Description |
|---|---|---|
| GET | `/datasets/case-types` | Supported case types and their fields |
| POST | `/datasets/validate` | Validate a file without saving it |
| GET | `/datasets/samples` | Bundled sample datasets |
| POST | `/datasets/samples/{sample_id}/install` | Install a sample |
| POST | `/datasets/generate` | Generate a context-window dataset (below) |
| GET / POST | `/datasets` | List or import (file + `name`, `dataset_type`) |
| GET / PATCH / DELETE | `/datasets/{id}` | |
| POST | `/datasets/{id}/duplicate` | Copy the current version into a new dataset |
| GET / POST | `/datasets/{id}/versions` | List versions or upload a new one |
| GET | `/datasets/{id}/versions/{version_id}` | |
| GET | `/datasets/{id}/preview` | Paged cases (`version_id`, `q`, `case_type`, `tag`) |
| GET | `/datasets/{id}/cases/{case_id}` | One case |
| GET | `/datasets/{id}/export` | Normalized JSONL; its SHA-256 equals the version's `content_hash` |

Generate body: `{"name": "needles", "params": {"context_lengths": [1024, 4096], "depths":
[0.1, 0.5, 0.9], "needles_per_case": 1, "repeats": 1, "seed": 0}}`.

- Lengths are requested sizes in tokens (128 to 524,288), estimated at 4 characters per token.
- `needles_per_case` above 1 makes multi-fact cases; the depths are then spread evenly.
- A dataset holds at most 1000 cases and 6,000,000 estimated tokens.
- Generation is deterministic: the same body gives the same content hash.

## Evaluation

| Method | Path | Description |
|---|---|---|
| GET | `/evaluators` | Registered evaluators: name, version, suite, kind, title, description, case types |
| GET / POST | `/profiles` | List or create evaluation profiles |
| GET / PATCH / DELETE | `/profiles/{id}` | |

## Runs

| Method | Path | Description |
|---|---|---|
| GET | `/runs` | List (`status` (repeatable), `q`) |
| GET | `/runs/summary` | Counts by status |
| POST | `/runs` | Create and queue a run (body below) |
| GET / DELETE | `/runs/{id}` | Run detail with its configuration snapshot, or delete a finished run |
| POST | `/runs/{id}/cancel` | Request cancellation |
| POST | `/runs/{id}/retry-failed` | Re-run failed or unfinished requests |
| GET | `/runs/{id}/metrics` | Aggregate metrics (`live` is true while the run is still running) |
| GET | `/runs/{id}/telemetry` | Resource samples (`run_model_id` filter) and per-model scrape status |
| GET | `/runs/{id}/results` | Attempts. Filters: `run_model_id`, `status`, `phase`, `final_only`, `q`, `evaluator`, `evaluation` (`passed`, `failed`, `not_applicable`, `skipped`, `requires_review`) |
| GET | `/runs/{id}/results/{result_id}` | One attempt with the full response and all evaluations |
| GET | `/runs/{id}/events` | Server-Sent Events (below) |
| POST | `/runs/{id}/rerun` | Create a new run with the same configuration (endpoints, dataset versions, suites, evaluation, generation, load) |

## Reports

| Method | Path | Description |
|---|---|---|
| POST | `/runs/{id}/reports` | Generate a report for a finished run (409 otherwise). Body: `{"format": "html" \| "json" \| "csv" \| "metrics_csv" \| "markdown", "redact_content": false, "redact_endpoints": false}` |
| GET | `/runs/{id}/reports` | Reports of one run |
| GET | `/reports` | All reports, newest first (`run_id`, `limit`, `offset`) |
| GET / DELETE | `/reports/{id}` | Report metadata (filename, size, sha256, options, result counts), or delete it |
| GET | `/reports/{id}/download` | The file (`inline=true` to view in the browser). Served with a restrictive CSP |

A report is generated once and stored; it doesn't change if the run is later retried. The JSON
export holds everything the other formats are rendered from: run, configuration snapshot,
models, datasets, metrics, results (with evaluations), chart series, failures, warnings,
telemetry samples (thinned to 2000 per model, without per-GPU detail), and reproduction
instructions.

## Comparison

| Method | Path | Description |
|---|---|---|
| GET | `/compare?run_ids=a,b[&baseline=]` | Compare without saving. `baseline` is a run id (its first model) or a run-model id; default: the first run's first model |
| GET | `/compare/export?run_ids=…&format=html\|csv\|json` | Download a comparison |
| POST | `/comparisons` | Save: `{"name", "description", "project_id", "run_ids", "baseline"}`. Returns the computed result |
| GET | `/comparisons` | Saved comparisons (`project_id`) |
| GET / DELETE | `/comparisons/{id}` | Saved comparison, recomputed on read (`result`), or `error` if a run was deleted |
| GET | `/comparisons/{id}/export?format=` | Download a saved comparison |

The result lists `subjects` (one per model of each run), `comparability` (`overall`,
`performance`, `quality`, and `differences` with `severity` critical / major / minor / info),
`metrics` (per subject: `value`, `delta`, `relative_change` (null when the baseline is 0 or
missing), `improved`, `unavailable_reason`), `transitions` (per evaluator: pass→fail and
fail→pass cases; new and fixed errors; lists capped at 200, totals exact), `variants`
(declared model and deployment fields per subject, with `null` meaning unknown; `warnings`;
`key_metrics`; and `context` metrics per requested length), and `notes`. Context-window rows
are left out of `metrics` because their names repeat per length.

### Create a run

```json
{
  "name": "nightly",
  "endpoint_ids": ["<uuid>"],
  "datasets": [{"dataset_id": "<uuid>", "version_id": null}],
  "suites": ["performance", "tool_calling"],
  "profile_id": null,
  "generation": {"temperature": 0, "max_tokens": 256},
  "load": {"profile": "fixed_concurrency", "requests": 50, "concurrency": 4, "warmup_requests": 2, "stream": true},
  "evaluation": {"tool_mode": "simulated", "max_tool_rounds": 3, "token_f1_threshold": 0.5, "allow_code_fence": true, "disabled_evaluators": [], "embedding": null, "faithfulness_threshold": 0.8, "support_threshold": 0.65, "judge": {"endpoint_id": "<uuid>", "concurrency": 4, "max_tokens": 1024, "temperature": 0}}
}
```

Suites: `performance`, `answer_quality`, `structured_output`, `tool_calling`, `rag`,
`hallucination`, and `context_window`. `quantization` is rejected with an explanation: compare
runs of the same datasets on each variant instead. With `profile_id`, any field you leave out
is taken from the profile. Unknown suites, datasets without case types for a selected
quality suite, and a `judge` without the `rag` or `hallucination` suite are rejected with a 422.
`tool_mode` is `simulated` (default), `generation_only`, or `integration`. Integration needs
the tool sandbox: it is rejected with a 422 when `TOOL_EXECUTION_ENABLED` is off
(`tool_execution_disabled`), when the runner is down (`tool_runner_unavailable`) or its
`tools.yaml` is invalid (`tool_registry_invalid`), or when a tool offered by the datasets is not
declared (`tools_not_registered`, with `details.missing_tools`). The snapshot's
`evaluation.tool_registry` records the registry hash and the tool versions used.
`judge` is optional (`null` by default); the snapshot's `evaluation.judge_endpoint` and
`evidence_method` record what was used.

### Metric names

| Prefix | Examples |
|---|---|
| Performance | `requests` (`attempted`, `succeeded`), `error_rate`, `ttft_ms`, `latency_ms` (`p50`…`p99`), `inter_token_latency_ms`, `output_tokens_per_second`, `output_token_throughput`, `request_throughput` |
| `quality.` | `quality.<evaluator>` (`pass_rate`, `mean_score`), `quality.evaluated_requests` (`count`), `quality.output_consistency` |
| `tool.` | `tool.selection_accuracy`, `tool.argument_exact_match_accuracy`, `tool.call_precision`, `tool.call_recall`, `tool.hallucinated_tool_rate`, … |
| `rag.` | `rag.claim_faithfulness`, `rag.contradicted_claim_rate` (claims pooled over answers) |
| `context.` | One row per requested length (`context_length` set): `context.requests`, `success_rate`, `overflow_rate`, `oom_rate`, `latency_ms` / `ttft_ms` (`p50`, `p95`), `estimated_input_tokens`, `input_tokens` (server-reported; `token_count_source` in metadata), `actual_to_estimated`, `truncation_suspected`, `retrieval_accuracy` (`pass_rate`, `early`, `middle`, `late`), `fact_recall` |
| `gpu.` / `telemetry.` | Only when telemetry is configured: `gpu.memory_used_mb` (`max`, `mean`), `gpu.memory_total_mb`, `gpu.utilization_pct`, `gpu.power_w`, `gpu.temperature_c`, `gpu.kv_cache_usage_pct`, `telemetry.samples` (`failures`, `last_error` in metadata) |
| `hallucination.` | `hallucination.unsupported_claim_rate`, `contradiction_rate`, `unverifiable_claim_rate`, `claim_support_precision`, `evidence_coverage`, `answers_with_contradictions`, `answer_despite_insufficient_evidence`, `abstention_precision`, `abstention_recall` |

Each metric row has `run_model_id`, `load_step` (null means the whole run), `context_length`
(the requested length for `context.*` rows, otherwise null), `metric_name`,
`aggregation`, `metric_value`, `metric_unit`, `sample_count`, and `metadata` (the definition,
plus the reason when the value is null).

### Events

`GET /runs/{id}/events` streams `text/event-stream`. Reconnecting with `Last-Event-ID` (or
`?after=<sequence>`) resumes without gaps.

| Event | Data |
|---|---|
| `run.progress` | Run status and per-model progress (`phase` is `warmup`, `measured`, `judging`, or `done`) |
| `run.started`, `run.cancel_requested`, `run.completed`, `run.cancelled`, `run.failed` | Status transitions |
| `case.completed` / `case.failed` | One recorded attempt. `id` is the result sequence |
| `metric.updated` | Metrics were recomputed |
| `run.end` | The run finished. The stream closes |
