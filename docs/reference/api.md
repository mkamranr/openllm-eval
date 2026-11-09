# REST API reference

Base path: `/api/v1`. The machine-readable schema is served at `/api/openapi.json`. In the
Compose deployment, the UI's nginx proxies `/api`, so the API is reachable on both port 3000
and port 8000.

## Conventions

- **JSON** in and out. Dataset uploads also accept `multipart/form-data`.
- **Pagination**: list endpoints take `limit` and `offset` and return
  `{"items": [...], "total": n, "limit": l, "offset": o}`.
- **Errors** share one envelope, and every response carries an `X-Request-ID` header:

  ```json
  {"error": {"code": "validation_error", "message": "…", "request_id": "…", "details": [{"loc": ["body", "load", "concurrency"], "msg": "…"}]}}
  ```

  Status codes: `404` not found, `409` conflict (for example a duplicate name, or deleting a
  dataset in use), `422` validation error, `503` not ready.
- **Nulls**: a metric or score that could not be measured is `null`, with a reason in its
  `metadata`. It is never `0`.
- **Secrets** never appear in responses. Endpoints expose only their `secret_reference` name.

## Health

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness, always 200 |
| GET | `/health/ready` | Database, Redis, and worker heartbeat. Returns 503 when not ready |
| GET | `/version` | Framework and API versions |

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

## Datasets

| Method | Path | Description |
|---|---|---|
| GET | `/datasets/case-types` | Supported case types and their fields |
| POST | `/datasets/validate` | Validate a file without saving it |
| GET | `/datasets/samples` | Bundled sample datasets |
| POST | `/datasets/samples/{sample_id}/install` | Install a sample |
| GET / POST | `/datasets` | List or import (file + `name`, `dataset_type`) |
| GET / PATCH / DELETE | `/datasets/{id}` | |
| POST | `/datasets/{id}/duplicate` | Copy the current version into a new dataset |
| GET / POST | `/datasets/{id}/versions` | List versions or upload a new one |
| GET | `/datasets/{id}/versions/{version_id}` | |
| GET | `/datasets/{id}/preview` | Paged cases (`version_id`, `q`, `case_type`, `tag`) |
| GET | `/datasets/{id}/cases/{case_id}` | One case |
| GET | `/datasets/{id}/export` | Normalized JSONL; its SHA-256 equals the version's `content_hash` |

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
| GET | `/runs/{id}/results` | Attempts. Filters: `run_model_id`, `status`, `phase`, `final_only`, `q`, `evaluator`, `evaluation` (`passed`, `failed`, `not_applicable`, `skipped`, `requires_review`) |
| GET | `/runs/{id}/results/{result_id}` | One attempt with the full response and all evaluations |
| GET | `/runs/{id}/events` | Server-Sent Events (below) |

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
  "evaluation": {"tool_mode": "simulated", "max_tool_rounds": 3, "token_f1_threshold": 0.5, "allow_code_fence": true, "disabled_evaluators": [], "embedding": null}
}
```

With `profile_id`, any field you leave out is taken from the profile. Suites from later
milestones, `tool_mode: "integration"`, and datasets without case types for a selected quality
suite are rejected with a 422.

### Metric names

| Prefix | Examples |
|---|---|
| Performance | `requests` (`attempted`, `succeeded`), `error_rate`, `ttft_ms`, `latency_ms` (`p50`…`p99`), `inter_token_latency_ms`, `output_tokens_per_second`, `output_token_throughput`, `request_throughput` |
| `quality.` | `quality.<evaluator>` (`pass_rate`, `mean_score`), `quality.evaluated_requests` (`count`), `quality.output_consistency` |
| `tool.` | `tool.selection_accuracy`, `tool.argument_exact_match_accuracy`, `tool.call_precision`, `tool.call_recall`, `tool.hallucinated_tool_rate`, … |

Each metric row has `run_model_id`, `load_step` (null means the whole run), `metric_name`,
`aggregation`, `metric_value`, `metric_unit`, `sample_count`, and `metadata` (the definition,
plus the reason when the value is null).

### Events

`GET /runs/{id}/events` streams `text/event-stream`. Reconnecting with `Last-Event-ID` (or
`?after=<sequence>`) resumes without gaps.

| Event | Data |
|---|---|
| `run.progress` | Run status and per-model progress |
| `run.started`, `run.cancel_requested`, `run.completed`, `run.cancelled`, `run.failed` | Status transitions |
| `case.completed` / `case.failed` | One recorded attempt. `id` is the result sequence |
| `metric.updated` | Metrics were recomputed |
| `run.end` | The run finished. The stream closes |
