<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo-light.svg" alt="OpenLLM-Eval: benchmark beyond tokens per second" width="460">
  </picture>
</p>

<p align="center">
  <b>Self-hosted benchmarking and evaluation for LLM inference endpoints.</b><br>
  Measure latency and throughput <i>and</i> whether the answers are right, on your own hardware.
</p>

<p align="center">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-5b4cf0">
  <img alt="Python" src="https://img.shields.io/badge/python-3.12-7c4dff">
  <img alt="React" src="https://img.shields.io/badge/react-18-9b4dff">
  <img alt="Status" src="https://img.shields.io/badge/status-milestone%204-16a34a">
</p>

---

OpenLLM-Eval connects to any OpenAI-compatible endpoint (vLLM, llama.cpp, Ollama, TGI, LM Studio,
or a hosted API). It runs reproducible load tests and quality evaluations against versioned
datasets and stores every request, so each number can be traced back to the requests that
produced it. Everything runs on your own infrastructure: no telemetry, no CDN, no model downloads.

## Features

| Area | What you get |
|---|---|
| **Endpoints** | OpenAI-compatible adapter (streaming and non-streaming), connection tests, model discovery, and secret references so API keys never reach the database. The outbound network policy guards against SSRF. |
| **Datasets** | JSONL/CSV import with line-numbered validation, immutable versions with SHA-256 content hashes, 8 case types (chat, instruction, Q&A, structured output, tool call, RAG, long context, safety), and bundled sample sets. |
| **Performance** | Smoke, sequential, fixed-concurrency, ramp, and sustained load profiles. TTFT, latency percentiles (p50 to p99), inter-token latency, tokens/s, throughput, and error rates. Warm-up requests are excluded and retries are tracked. |
| **Answer quality** | Exact, normalized, contains-reference, and token-F1 matching; a refusal heuristic for safety cases; optional embedding similarity. |
| **Structured output** | JSON validity, JSON Schema (Draft 2020-12) conformance with violation paths, and expected-field match. |
| **RAG and hallucination** | Faithfulness, citation precision and recall, context relevance, precision and recall, and abstention on unanswerable questions. Every claim is classified as supported, unsupported, contradicted, or unverifiable, with its evidence and source. Lexical by default; an LLM judge is optional. |
| **Tool calling** | Tool selection measured separately from argument correctness; call precision and recall; invented-tool and malformed-argument rates; and **simulated** tool results from dataset fixtures, so final answers can be checked without executing anything. |
| **Runs** | A background worker with a database-backed queue and leases, live progress over SSE, cancel and retry-failed, and an immutable, secret-free configuration snapshot for every run. |
| **Profiles** | Reusable evaluation settings, available from the UI, the API, and the CLI. |
| **Interfaces** | A web UI (dashboard, New Evaluation wizard, run explorer), a REST API with OpenAPI, and the `openllm-eval` CLI with YAML run configs. |

Missing measurements are always reported as unavailable, with a reason, and never as `0`.

## Quick start

Requirements: Docker with Compose v2.

```bash
cp .env.example .env              # set POSTGRES_PASSWORD (and BIND_ADDRESS=0.0.0.0 for remote access)
bash scripts/deploy.sh            # builds, migrates, starts, and runs the smoke test
```

| | URL |
|---|---|
| Web UI | http://localhost:3000 |
| API readiness | http://localhost:8000/api/v1/health/ready |
| OpenAPI schema | http://localhost:8000/api/openapi.json |

**Try it without a GPU** using the bundled mock model server:

```bash
echo "ENDPOINT_ALLOWED_HOSTS=mock-llm" >> .env
bash scripts/deploy.sh --mock
# UI → Models / Endpoints → Add endpoint: base URL http://mock-llm:9000/v1, model mock-model → Test
```

Then open **New Evaluation**, pick the endpoint, select *Performance* and *Answer quality*, add
the *Sample: general Q&A* dataset, and start the run.

**Upgrading:** unpack a new release into any folder, copy `.env` (and `secrets.env`) across, and
run `bash scripts/deploy.sh`. The script stops the previous install's containers, keeps the data
volumes, and applies the database migrations.

## Run from the CLI

```yaml
# bench.yaml
name: vllm-baseline
endpoints: [local-vllm]
dataset:
  name: "Sample: general Q&A"
evaluation:
  suites: [performance, answer_quality]
generation:
  temperature: 0
  max_tokens: 256
load:
  profile: fixed_concurrency
  requests: 100
  concurrency: 8
  warmup_requests: 5
```

```bash
openllm-eval run create --config bench.yaml --wait
openllm-eval run results <run-id>
```

## Architecture

```
browser ──► frontend (nginx) ──/api──► api (FastAPI) ──► PostgreSQL
                                          │          └─► Redis (wake-up signal, heartbeats)
                                       worker ──► model endpoints (through adapters + network policy)
```

The backend is a modular monolith in Python 3.12 (FastAPI, async SQLAlchemy 2, Alembic). It is
deployed as two processes, an API and a worker. The UI is React 18 + TypeScript built with Vite
and served by nginx. See the [architecture overview](docs/architecture/overview.md).

## Documentation

| Guide | |
|---|---|
| [User guide](docs/user-guide/getting-started.md) | Endpoints, datasets, runs, quality evaluation, and profiles, step by step |
| [Dataset format](docs/user-guide/datasets.md) | Case types, fields, and JSONL/CSV examples |
| [Configuration](docs/reference/configuration.md) | Every `.env` setting |
| [CLI reference](docs/reference/cli.md) | `openllm-eval` commands and the run config format |
| [API reference](docs/reference/api.md) | REST endpoints, errors, and streaming events |
| [Developer setup](docs/developer-guide/setup.md) | Local development, deployment, migrations, and testing |
| [Architecture](docs/architecture/overview.md) · [Data model](docs/architecture/data-model.md) | How it fits together |

## Roadmap

- [x] **M0** Foundation: Compose stack, health checks, migrations, CI
- [x] **M1** Model endpoints, adapters, secret references, network policy
- [x] **M2** Versioned datasets and validation
- [x] **M3** Run engine and performance metrics
- [x] **M4** Deterministic evaluators, tool calling, evaluation profiles
- [x] **M5** RAG and evidence evaluation, optional LLM judge, hallucination indicators
- [ ] **M6** Run comparison and reports
- [ ] **M7** Quantization and context-window studies

See the [CHANGELOG](CHANGELOG.md) for details.

## Contributing and security

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability,
follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## License

[Apache-2.0](LICENSE)
