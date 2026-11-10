# CLI reference

The `openllm-eval` command talks to a running API. Inside the containers, run
`docker compose exec api openllm-eval …`. Locally, run `cd backend && uv run openllm-eval …`.

Every command that talks to the API accepts `--api-url`, which defaults to `$OPENLLM_API_URL`
or `http://localhost:8000`. Most commands also accept `--json` for machine-readable output.

## General

| Command | Description |
|---|---|
| `openllm-eval version` | Framework version |
| `openllm-eval health [--timeout 5]` | API, database, Redis, and worker readiness. Exits non-zero when not ready |

## Endpoints

| Command | Description |
|---|---|
| `openllm-eval endpoint list` | Registered endpoints and their last test status |
| `openllm-eval endpoint test <name-or-id> [--prompt …] [--stream/--no-stream]` | Send one test request; prints latency, TTFT, and tokens |

## Datasets

| Command | Description |
|---|---|
| `openllm-eval dataset validate <file> [--type qa] [--format jsonl\|csv]` | Validate locally, without uploading |
| `openllm-eval dataset import <file> [--name …] [--type …] [--to <dataset>] [--label …]` | Import a new dataset, or add a version with `--to` |
| `openllm-eval dataset list` | Datasets with their current versions |

## Runs

| Command | Description |
|---|---|
| `openllm-eval run create --config run.yaml [--wait]` | Create a run. `--wait` follows progress until it finishes |
| `openllm-eval run status <run-id> [--watch]` | Status and progress |
| `openllm-eval run results <run-id>` | Performance, quality, tool, RAG, and hallucination metrics per model |
| `openllm-eval run list [--limit 20]` | Recent runs |
| `openllm-eval run cancel <run-id>` | Request cancellation |
| `openllm-eval run rerun <run-id> [--wait]` | New run with the same configuration |
| `openllm-eval compare <run-id> <run-id>… [--baseline <run-id>] [--json] [-o file.html\|.csv\|.json]` | Compare runs: comparability, configuration differences, key metrics with relative change, per-case regressions. Also `run compare` |

## Reports

| Command | Description |
|---|---|
| `openllm-eval report generate <run-id> [-f html\|json\|csv\|metrics_csv\|markdown] [-o path] [--redact-content] [--redact-endpoints]` | Generate a report for a finished run and save it (to a file, or into a directory under its own name) |
| `openllm-eval report list [--run <run-id>]` | Generated reports |
| `openllm-eval report download <report-id> [-o path]` | Download an earlier report |

## Run config (YAML)

```yaml
name: nightly-qwen                 # required
description: Optional text
project: support-bot               # optional (name or id)
profile: nightly-quality           # optional: start from a saved profile (name or id)

endpoints: [local-vllm, ollama]    # names or ids, benchmarked one after another

dataset:                           # one dataset…
  name: "Sample: general Q&A"      # or id:, version_id:, or path: ./cases.jsonl (imported first)
# datasets: [...]                  # …or a list of the same objects

evaluation:
  suites: [performance, answer_quality, structured_output, tool_calling, rag, hallucination]
  tool_mode: simulated             # or generation_only. Tools are never executed
  max_tool_rounds: 3               # 1-5
  token_f1_threshold: 0.5
  allow_code_fence: true
  disabled_evaluators: []          # e.g. [refusal_behavior]
  embedding:                       # optional embedding similarity
    endpoint: local-embeddings     # endpoint name or id; it must serve /embeddings
    threshold: 0.8
  faithfulness_threshold: 0.8      # rag / hallucination
  support_threshold: 0.65          # lexical claim checks
  judge:                           # optional LLM judge for rag / hallucination (opt-in)
    endpoint: judge-70b            # endpoint name or id; case content is sent to it
    concurrency: 4                 # judge requests in flight, after each model is measured
    max_tokens: 1024
    temperature: 0

generation:
  temperature: 0
  top_p: null
  max_tokens: 256
  seed: null
  stream: true                     # same as load.stream; streaming is required for TTFT

load:
  profile: fixed_concurrency       # smoke | sequential | fixed_concurrency | ramp | sustained
  requests: 100                    # per model; defaults to one per case
  concurrency: 8
  warmup_requests: 5
  request_timeout_seconds: 120     # overrides the endpoint default
  max_retries: 1
  # ramp:      ramp_concurrency: [1, 2, 4, 8]   requests_per_step: 20
  # sustained: duration_seconds: 300

report:                            # saved after the run with --wait (into --report-dir)
  formats: [html, json, csv]       # html | json | csv | metrics_csv | markdown
```

When a `profile` is given, only the keys you set here override it. Values that a profile or the
config leave unset fall back to each endpoint's defaults.

Exit codes: `0` on success, `1` when the API rejects the request (the message lists the offending
fields) or a run does not complete, and `2` when the API cannot be reached or the input is invalid.
