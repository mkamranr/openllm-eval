# Developer setup

There are two supported ways to run OpenLLM-Eval:

1. **Docker Compose.** This is how it is deployed and how the full stack (PostgreSQL, Redis,
   worker, and UI) is tested.
2. **Local processes, no Docker.** This is the fast loop for backend and frontend work. Tests use
   SQLite and an in-memory fake Redis, so no services are needed.

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| Docker Engine + Compose plugin | Compose **v2.24+** | Deployment, end-to-end smoke test |
| [uv](https://docs.astral.sh/uv/) | 0.11+ | Backend Python 3.12 environment and pinned dependencies |
| Node.js / npm | 20.18+ / 10+ | Frontend |
| bash + curl | any | `scripts/smoke-test.sh` |

You don't need a system Python 3.12. `uv sync --python 3.12` downloads a managed interpreter.

## 1. Docker Compose deployment (e.g. on a test server)

```bash
git clone <repo-url> openllm-eval && cd openllm-eval
cp .env.example .env
# Edit .env:
#   POSTGRES_PASSWORD=<strong password>      (required)
#   BIND_ADDRESS=0.0.0.0                     (only if you browse from another machine;
#                                             there is no auth yet, so use a trusted network)
bash scripts/deploy.sh          # docker compose up -d --build, then scripts/smoke-test.sh
```

### Upgrading to a new release

```bash
tar xzf openllm-eval-<version>.tar.gz && cd openllm-eval-<version>
cp ../<previous-folder>/.env ../<previous-folder>/secrets.env .   # secrets.env if you have one
bash scripts/deploy.sh          # add --mock to keep the mock model server
```

Every release uses the Compose project name `openllm-eval`, so the new folder upgrades the same
containers and volumes in place, and migrations run automatically. `deploy.sh` also handles
installs from releases before 0.0.1-m2, which ran under a project named after their folder. It
finds every container built from an `openllm-eval-*` image, stops and removes those containers and
their networks (freeing the ports), and copies their PostgreSQL volume into
`openllm-eval_postgres_data`. If both databases already exist, it asks before replacing one
(the replaced one is backed up to `openllm-eval_postgres_data_backup_<timestamp>`). Without a
terminal it keeps the current one. To choose explicitly, use `--take-data-from <old-project>` or
`--keep-new-data`. Old volumes are kept, and the script prints the `docker volume rm` command
for them. It refuses to start if another container or process holds `API_PORT` or `FRONTEND_PORT`.

**Passwords:** any characters work, because the backend receives the DB settings separately and
escapes them. If the password contains `$`, wrap it in single quotes in `.env`
(`POSTGRES_PASSWORD='P@ss$word'`); otherwise Compose treats `$word` as a variable. PostgreSQL
applies the password only when its volume is first created. On an empty install, change it with
`docker compose down -v && docker compose up -d --build`.

**Ports:** `FRONTEND_PORT` (default 3000) is the UI. `API_PORT` (default 8000) publishes the API
for the CLI, scripts, and the smoke test. The UI doesn't use it, because nginx reaches the API
over the internal network. Either can be changed in `.env` if the port is taken.

For a remote server, run the smoke test on the server itself, or point it at the host:

```bash
API_URL=http://<server>:8000 FRONTEND_URL=http://<server>:3000 SKIP_COMPOSE_CHECKS=1 \
  bash scripts/smoke-test.sh
```

The containers start in this order:

1. `postgres` and `redis` start and become healthy.
2. `migrate` runs `alembic upgrade head` once and exits with code 0.
3. `api` starts after the migration succeeds. It serves on `:8000` and has a health check on
   `/api/v1/health`.
4. `worker` starts and writes a Redis heartbeat every 5 s, with a TTL of 15 s.
5. `frontend` (nginx) starts after `api` is healthy. It serves the UI on `:3000` and proxies
   `/api` to the API.

Open http://localhost:3000 (or `http://<server>:3000`). The dashboard should show **API**,
**Database**, **Redis**, and **Worker** as OK.

Useful commands:

```bash
docker compose ps -a                     # migrate should show "Exited (0)"
docker compose logs -f api worker        # JSON logs; secrets are redacted
docker compose exec api openllm-eval health --api-url http://localhost:8000
docker compose down                      # stop (keeps data)
docker compose down -v                   # stop and DELETE database/redis volumes
```

### Connecting model endpoints (Milestone 1)

**1. Allow the destination.** By default the network policy blocks private and loopback
addresses, which is where self-hosted model servers usually live. In `.env`, choose one:

```bash
ENDPOINT_ALLOWED_HOSTS=vllm.lan,10.0.5.0/24,mock-llm   # recommended: exclusive allowlist
# or
ENDPOINT_ALLOW_PRIVATE_NETWORKS=true                    # any private address (trusted networks)
```

`postgres`, `redis`, and cloud metadata addresses stay blocked either way. A model server running
on the Docker host itself is reachable as `host.docker.internal` only if you add
`extra_hosts: ["host.docker.internal:host-gateway"]` to the api and worker services; otherwise
use the host's LAN IP.

**2. Store API keys (if the server needs one).**

```bash
cp secrets.env.example secrets.env
echo "OPENLLM_SECRET_VLLM_KEY=your-key" >> secrets.env
docker compose up -d api worker          # reload the env
```

In the endpoint form, set **API key secret reference** to `env:OPENLLM_SECRET_VLLM_KEY`. The UI
warns if the referenced variable isn't set on the server.

**3. Register and test.** Use **Models / Endpoints → Add endpoint**, or the API:

```bash
curl -s -XPOST localhost:8000/api/v1/endpoints -H 'Content-Type: application/json' -d '{
  "name": "local-vllm", "base_url": "http://vllm.lan:8000/v1",
  "model_identifier": "Qwen/Qwen2.5-7B-Instruct",
  "secret_reference": "env:OPENLLM_SECRET_VLLM_KEY",
  "deployment_metadata": {"gpu": "RTX 4090", "quantization": "awq"}}'

docker compose exec api openllm-eval endpoint list --api-url http://localhost:8000
docker compose exec api openllm-eval endpoint test local-vllm --api-url http://localhost:8000
```

The test calls `/models` (it reports whether the model is listed), then sends one short
streaming chat request. It reports latency, TTFT, token usage as reported by the server, the
HTTP status, and a typed error on failure (`connection_error`, `timeout`, `http_status` for e.g. a 401,
`destination_blocked`, …).

**Mock server for demos.** `docker compose --profile mock up -d` starts `mock-llm`, which serves
`mock-model` and `mock-model-q4` at `http://mock-llm:9000/v1` (no key needed). Allow it with
`ENDPOINT_ALLOWED_HOSTS=mock-llm`. When mock-llm is running, the smoke test also registers, tests,
and deletes a mock endpoint (force it with `SMOKE_MOCK=1`).

**Custom CA certificates.** Set `CA_CERT_DIR=/certs` in `.env` and mount
your CA bundle there. Endpoints can then set a CA path inside that directory. Without
`CA_CERT_DIR`, custom CA paths are rejected.

| Variable | Default | Notes |
|---|---|---|
| `ENDPOINT_ALLOW_PRIVATE_NETWORKS` | `false` | Allow RFC 1918, loopback, and ULA destinations |
| `ENDPOINT_ALLOWED_HOSTS` | *(empty)* | Exclusive allowlist: hostnames, `*.suffix`, CIDRs |
| `ENDPOINT_BLOCKED_HOSTS` | `postgres,redis` | Always blocked |
| `ENDPOINT_MAX_RESPONSE_BYTES` | `16777216` | Per-response cap |
| `CA_CERT_DIR` | *(unset)* | Directory that custom CA files must live in |
| `OPENLLM_SECRET_*` | — | In `secrets.env`; referenced as `env:NAME` |

### Datasets (Milestone 2)

Datasets are JSONL (one JSON object per line) or CSV files. Each record needs an `id`, a `type`
(or the dataset's type is used), and either `input` or `messages`; the other fields depend on the
type (see `GET /api/v1/datasets/case-types` and the bundled samples in
`backend/app/datasets/samples/`). Every upload is validated first. Errors and warnings carry line
numbers, and an invalid file creates nothing.

- **UI:** Datasets → *Built-in samples* (install with one click) or *Import* (upload or paste,
  **Validate**, then **Import**). Open a dataset to browse its records, upload a new version, or
  export it.
- **API** (multipart or a JSON body with `content`):

  ```bash
  curl -F name=my-qa -F dataset_type=qa -F tags=smoke,net -F file=@qa.csv \
       http://localhost:8000/api/v1/datasets
  curl -F file=@qa-v2.jsonl -F version_label=v2 http://localhost:8000/api/v1/datasets/<id>/versions
  curl -F file=@qa.csv http://localhost:8000/api/v1/datasets/validate        # dry run
  ```

- **CLI:** `openllm-eval dataset validate qa.csv` works offline. `openllm-eval dataset import
  qa.csv --name my-qa` and `openllm-eval dataset list` talk to the API (`--api-url`).

**Versions and hashes.** Every upload creates a new immutable version. Its `content_hash` is the
SHA-256 of the normalized JSONL export, so you can verify a version independently:

```bash
curl -s http://localhost:8000/api/v1/datasets/<id>/export | sha256sum   # equals content_hash
```

The same records give the same hash whether they came from CSV or JSONL, and whatever the key
order or whitespace. Record order matters. Uploading content identical to an existing version of
the same dataset is rejected with 409.

| Variable | Default | Notes |
|---|---|---|
| `DATASET_MAX_UPLOAD_BYTES` | `26214400` (25 MB) | Keep below nginx's 50 MB body limit |
| `DATASET_MAX_RECORDS` | `20000` | Records per version |
| `DATASET_MAX_RECORD_BYTES` | `4194304` (4 MB) | Per normalized record |

### Benchmark runs (Milestone 3)

Start a run from **New Evaluation**. Pick models, select **Performance**, set the load, choose
datasets, and click **Start Evaluation**. The run page updates live: progress, recent requests,
metrics, and every stored attempt.

The **worker** service executes runs. One worker runs one benchmark at a time, and further runs
wait in the queue. If the dashboard shows no worker heartbeat, runs stay queued. Check
`docker compose logs worker`.

Load profiles:

| Profile | What it does |
|---|---|
| `smoke` | At most 5 requests at concurrency of 2 or less: checks that everything works |
| `sequential` | One request at a time: clean per-request latency |
| `fixed_concurrency` | Keeps `concurrency` requests in flight until `requests` are done |
| `ramp` | Steps through increasing concurrency (`ramp_concurrency`), `requests_per_step` each, with metrics per step |
| `sustained` | Holds `concurrency` requests in flight for `duration_seconds`; `requests` is an optional cap |

How measurements work:

- `requests` is per model and defaults to one per dataset case. Request *i* uses case
  *i mod case count*.
- Warm-up requests run first, one at a time, and are excluded from all metrics.
- Endpoints are benchmarked one after another.
- TTFT needs streaming. Without it, TTFT is reported as unavailable, not 0.
- Token throughput needs server-reported token counts. If any request lacks them, it is
  reported as unavailable.
- Retries are stored as separate attempts. A request counts as failed only after its last
  attempt.
- **Retry failed** re-runs only failed or unfinished requests. Throughput is then unavailable,
  because the measurement is no longer one contiguous window.

From the CLI (inside the api container, or locally against a running API):

```bash
cat > bench.yaml <<'EOF'
name: vllm-baseline
endpoints: [local-vllm]          # endpoint names (or ids)
dataset:
  name: "Sample: general Q&A"   # or id / version_id, or path: ./cases.jsonl
evaluation:
  suites: [performance]          # add answer_quality, structured_output, tool_calling (M4)
generation:
  temperature: 0
  max_tokens: 256
  stream: true
load:
  profile: fixed_concurrency
  requests: 100
  concurrency: 8
  warmup_requests: 5
EOF
openllm-eval run create --config bench.yaml --wait
openllm-eval run results <run-id>
```

Settings (in `.env`): `RUN_MAX_REQUESTS` (100000 per model), `RUN_MAX_CONCURRENCY` (256),
`RUN_STORE_RESPONSES` (true), and `RUN_RESPONSE_MAX_CHARS` (20000).

### Quality evaluation and profiles (Milestone 4)

Select **Answer quality**, **Structured output**, or **Tool-calling** in the wizard (or list them
in `evaluation.suites`). Performance metrics are recorded on every run. Each suite reads the case
types it understands:

| Suite | Case types | Evaluators |
|---|---|---|
| `answer_quality` | qa, chat, instruction, long_context, safety | exact_match, normalized_match, contains_reference, token_f1, refusal_behavior (safety), embedding_similarity (optional) |
| `structured_output` | structured_output | json_valid, json_schema, json_expected_match |
| `tool_calling` | tool_call | tool_selection, tool_argument_names, tool_argument_schema, tool_required_arguments, tool_argument_values, tool_sequence, tool_hallucination, tool_final_answer, tool_task_success |

`GET /api/v1/evaluators` lists every evaluator with its version and definition. A run is
rejected (422) if none of its datasets has a case type for a selected quality suite.

How results are reported:

- Only successful, measured, final attempts are evaluated. `quality.evaluated_requests` says how
  many, and failed requests show up in the performance error metrics instead.
- Each evaluator has its own pass rate (`quality.<name>`, `pass_rate` and `mean_score`). There is
  no combined score. Cases where an evaluator does not apply are `not_applicable` and are left
  out of the rate; a rate with no applicable case is shown as "—" with a reason.
- Tool argument metrics count only cases where the expected tool was selected.
- Embedding similarity needs an endpoint that serves `/embeddings`; it measures similarity, not
  correctness.

Tool modes (`evaluation.tool_mode`):

- `simulated` (default): each valid call is answered with the matching fixture from the case's
  `tool_results` (or a structured error for unknown tools, malformed or schema-invalid
  arguments, or no matching fixture), and the model continues, up to `max_tool_rounds`
  (default 3). The final answer is then checked against `expected_final_answer`.
- `generation_only`: calls are checked as returned; final answers are not evaluated.
- Real tool execution is not available. `integration` is rejected.

Evaluation settings in a run config:

```yaml
profile: nightly-quality        # optional: start from a saved profile (name or id)
evaluation:
  suites: [performance, answer_quality, tool_calling]
  tool_mode: simulated
  max_tool_rounds: 3
  token_f1_threshold: 0.5
  allow_code_fence: true
  disabled_evaluators: [refusal_behavior]
  embedding:                    # optional
    endpoint: local-embeddings  # endpoint name or id
    threshold: 0.8
```

**Evaluation profiles** store suites, evaluation, generation, and load settings. Create them on
the **Evaluation Profiles** page, with **Save as profile** in the wizard's Review step, or via
`POST /api/v1/profiles`. A run that names a profile uses its settings for anything the request
does not set, and its snapshot copies them, so later profile edits don't change past runs.

The mock model server can be scripted for repeatable quality demos: set `MOCK_SCRIPT` to a JSON
file with a list of `{"match": "<text in the last user message>", "response": "...",
"tool_calls": [{"name": "...", "arguments": {...}}], "after_tools": "..."}` entries.

### Development stack with hot reload

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

This mounts `backend/app` into the api and worker containers (uvicorn `--reload`) and runs the
Vite dev server at http://localhost:5173. It requires Compose v2.24 or later for the `!reset` and
`!override` tags.

### Backup and restore (PostgreSQL)

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > backup.dump
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < backup.dump
```

## 2. Local development without Docker

### Backend

```bash
cd backend
uv sync --python 3.12          # creates .venv from uv.lock (includes dev tools)
uv run pytest                  # SQLite + fake Redis; no services required
uv run ruff check . && uv run ruff format --check .

uv run alembic upgrade head    # creates ./openllm_eval.db (SQLite) by default
uv run uvicorn app.main:app --reload
```

The backend reads its configuration from environment variables or `backend/.env`:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./openllm_eval.db` | `postgresql://…` is mapped to the async psycopg driver automatically |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | *(unset)* | Alternative to `DATABASE_URL`, used by Compose. When `POSTGRES_HOST` is set and `DATABASE_URL` is not, the URL is built with proper escaping |
| `REDIS_URL` | `redis://localhost:6379/0` | |
| `ENVIRONMENT` | `development` | `development` \| `test` \| `production` |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `json` | `console` for human-readable output |
| `CORS_ORIGINS` | *(empty)* | Comma-separated list. Not needed when using the Vite proxy |

Without Redis running locally, `GET /api/v1/health` returns 200, but `/api/v1/health/ready`
correctly returns **503** with `redis: error`.

To run the tests against real services:

```bash
TEST_DATABASE_URL=postgresql://openllm:pw@localhost:5432/openllm_test \
TEST_REDIS_URL=redis://localhost:6379/15 uv run pytest
```

Warning: the tests flush the Redis database given in `TEST_REDIS_URL` and migrate the test
database up and down. Never point them at real data.

### Frontend

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173, proxies /api to http://localhost:8000
npm test           # vitest
npm run typecheck && npm run lint
npm run build      # outputs dist/
```

Set `VITE_API_PROXY_TARGET` to proxy to a different API, for example
`VITE_API_PROXY_TARGET=http://server:8000 npm run dev`.

### CLI

```bash
cd backend
uv run openllm-eval version
uv run openllm-eval health --api-url http://localhost:8000   # exit 0 ready, 1 degraded, 2 unreachable
uv run openllm-eval endpoint list
uv run openllm-eval endpoint test local-vllm [--no-stream] [--json]   # exit 1 failed, 2 unreachable
```

### Mock model server (local)

```bash
cd backend
MOCK_API_KEY=dev uv run python -m app.testing.mock_openai --port 9000
# Register http://127.0.0.1:9000/v1 with secret reference env:OPENLLM_SECRET_MOCK, and start the
# API with ENDPOINT_ALLOW_PRIVATE_NETWORKS=true OPENLLM_SECRET_MOCK=dev.
```

## Database migrations

```bash
cd backend
uv run alembic revision --autogenerate -m "describe change"   # then review the generated file
uv run alembic upgrade head
uv run alembic downgrade -1
```

`tests/integration/test_migrations.py` checks three things: migrations reach `head`, the result
matches the ORM models (no autogenerate diff), and the migrations downgrade cleanly to `base`.

## Windows notes

- Run `scripts/smoke-test.sh` with Git Bash or WSL: `bash scripts/smoke-test.sh`.
- The `Makefile` targets need a POSIX shell. Each target is a single command, so you can also
  run it directly.
