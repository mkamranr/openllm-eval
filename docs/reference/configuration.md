# Configuration

All settings are environment variables. With Docker Compose they come from `.env` (copy
`.env.example`); model API keys go in `secrets.env` (copy `secrets.env.example`). Both files are
git-ignored. Apply changes with `bash scripts/deploy.sh`, or with
`docker compose up -d api worker` when only backend settings changed.

When the backend runs locally with `uv run`, it reads `backend/.env`.

## Deployment

| Variable | Default | Description |
|---|---|---|
| `BIND_ADDRESS` | `127.0.0.1` | Host address the published ports bind to. Use `0.0.0.0` for remote access, on a trusted network only (there is no authentication yet) |
| `FRONTEND_PORT` | `3000` | Web UI port on the host |
| `API_PORT` | `8000` | API port on the host |
| `ENVIRONMENT` | `production` (Compose) | `development`, `test`, or `production` |
| `OPENLLM_VERSION` | `0.0.1` | Image tag |

## Database and Redis

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | (required) | Applied only when the volume is first created. Wrap the value in single quotes if it contains `$` |
| `POSTGRES_DB` / `POSTGRES_USER` | `openllm_eval` / `openllm` | |
| `DATABASE_URL` | SQLite file (local) | Overrides the Postgres settings, e.g. `postgresql://user:pass@host:5432/db` |
| `REDIS_URL` | `redis://localhost:6379/0` | Set automatically in Compose |
| `DB_CONNECT_TIMEOUT_SECONDS` | `5` | |
| `REDIS_CONNECT_TIMEOUT_SECONDS` | `2` | |

## Model endpoint network policy

| Variable | Default | Description |
|---|---|---|
| `ENDPOINT_ALLOWED_HOSTS` | (empty) | Comma-separated hostnames, `*.suffix` patterns, IPs, or CIDRs. When set, **only** these destinations are allowed |
| `ENDPOINT_ALLOW_PRIVATE_NETWORKS` | `false` | Allow private and loopback addresses in general |
| `ENDPOINT_BLOCKED_HOSTS` | `postgres,redis` | Always blocked, as are cloud metadata and link-local addresses |
| `ENDPOINT_MAX_RESPONSE_BYTES` | `16777216` | Maximum bytes read from one model response |
| `CA_CERT_DIR` | (empty) | Directory that custom CA bundles must live in. Mount it into `api` and `worker` |

## Secrets

| Variable | Description |
|---|---|
| `OPENLLM_SECRET_<NAME>` | A model API key. Endpoints refer to it as `env:OPENLLM_SECRET_<NAME>`. Keys are never stored, returned, logged, or included in snapshots |

## Datasets

| Variable | Default | Description |
|---|---|---|
| `DATASET_MAX_UPLOAD_BYTES` | `26214400` (25 MB) | Keep it below nginx's 50 MB body limit |
| `DATASET_MAX_RECORDS` | `20000` | Records per version |
| `DATASET_MAX_RECORD_BYTES` | `4194304` (4 MB) | Bytes per normalized record |

## Runs

| Variable | Default | Description |
|---|---|---|
| `RUN_MAX_REQUESTS` | `100000` | Measured requests per model |
| `RUN_MAX_CONCURRENCY` | `256` | Upper limit for concurrency |
| `RUN_MAX_DURATION_SECONDS` | `86400` | Upper limit for sustained runs |
| `RUN_MAX_ENDPOINTS` | `10` | Models per run |
| `RUN_STORE_RESPONSES` | `true` | Store generated text with the results. When `false`, only timings and token counts are stored |
| `RUN_RESPONSE_MAX_CHARS` | `20000` | Stored text is truncated to this length. Evaluation always uses the full response |
| `RUN_LEASE_SECONDS` | `30` | Worker lease. A crashed worker's run is resumed after it expires |
| `RUN_PROGRESS_INTERVAL_SECONDS` | `1` | How often results are written and progress is published |
| `WORKER_POLL_INTERVAL_SECONDS` | `1` | Queue poll interval (Redis wake-ups make it faster) |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` / `WORKER_HEARTBEAT_TTL_SECONDS` | `5` / `15` | Worker liveness shown on the dashboard |

## Logging and CORS

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `LOG_FORMAT` | `json` | `json` for log aggregation, `console` for reading by eye. Secrets are redacted either way |
| `CORS_ORIGINS` | (empty) | Only needed when the UI is served from a different origin, e.g. `http://localhost:5173` in development |

## Mock model server

| Variable | Description |
|---|---|
| `MOCK_API_KEY` | Bearer key that the mock server requires (optional) |
| `MOCK_SCRIPT` | Path to a JSON list of scripted responses for repeatable quality demos (see the [developer setup](../developer-guide/setup.md)) |
