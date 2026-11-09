# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately. Use GitHub's "Report a vulnerability" (private security
advisory) on this repository rather than opening a public issue. Include steps to reproduce,
affected versions, and the impact. We aim to acknowledge reports within 5 business days.

## Current security posture (Milestone 4)

- **No authentication yet.** By default, Compose binds published ports to `127.0.0.1`. If you set
  `BIND_ADDRESS=0.0.0.0`, the UI and API are reachable by anyone who can reach the host. Do this
  only on a trusted network.
- PostgreSQL and Redis sit on an internal Docker network with no published ports and no outbound
  access.
- The containers run as non-root users, with `no-new-privileges`.
- Logs redact authorization headers, API keys, tokens, passwords, and credentials embedded in URLs.
- API validation errors never echo submitted values back. Unhandled errors return a generic
  message.
- The UI is served with a strict Content-Security-Policy (`default-src 'self'`) and loads no remote
  assets.

### Model API keys

- Endpoints store a **reference** such as `env:OPENLLM_SECRET_VLLM_KEY`, never the key itself.
  Only variables with the `OPENLLM_SECRET_` prefix can be referenced, so an endpoint cannot read
  `POSTGRES_PASSWORD` or other server variables.
- Keys are read from `secrets.env`, which is loaded only into the api and worker containers and
  is git-ignored. Resolved values never appear in the database, API responses, logs, or adapter
  error messages (error text is scrubbed of the key value).
- Headers whose names look like credentials (`Authorization`, `*-Key`, `*-Token`, `Cookie`, …)
  are rejected in the plain `headers` setting. They must go in `secret_headers` as references.

### Outbound requests (SSRF protection)

Registering an endpoint lets a user make the server send HTTP requests, so every model request
goes through a network policy:

- Only `http`/`https` URLs, with no `user:password@` part. Redirects are never followed, and
  proxy environment variables are ignored.
- The Compose services `postgres` and `redis` (configurable with `ENDPOINT_BLOCKED_HOSTS`),
  cloud metadata addresses (`169.254.169.254`, `169.254.170.2`, `fd00:ec2::254`,
  `100.100.100.200`), link-local, multicast, unspecified, and reserved addresses are **always**
  blocked.
- Private and loopback addresses (10/8, 172.16/12, 192.168/16, 127/8, fc00::/7, …) are blocked
  unless `ENDPOINT_ALLOW_PRIVATE_NETWORKS=true`. When `ENDPOINT_ALLOWED_HOSTS` is set, it becomes
  an exclusive allowlist of hostnames, `*.suffix` patterns, and CIDRs, and listed hosts may be
  private.
- The hostname is resolved and **every** resolved address is checked before each request, not
  only at registration time.
- Response bodies are capped (`ENDPOINT_MAX_RESPONSE_BYTES`, default 16 MB) and requests have a
  total timeout.

**Residual risk:** between the policy's DNS lookup and httpx's own connection there is a short
window in which a DNS-rebinding attacker could return a different address. Pinning the connection
to the checked IP is planned. Until then, prefer `ENDPOINT_ALLOWED_HOSTS` on shared deployments.
Note that the app has **no authentication yet**: anyone who can reach the UI can register
endpoints and trigger requests within this policy.

### Dataset uploads

- Uploads are size-capped before parsing: `DATASET_MAX_UPLOAD_BYTES` (default 25 MB; the request
  body is read with a hard cap), `DATASET_MAX_RECORDS` (20,000), and `DATASET_MAX_RECORD_BYTES`
  (4 MB per record). nginx caps request bodies at 50 MB.
- Only UTF-8 JSONL and CSV are accepted. NUL bytes, NaN/Infinity, and duplicate JSON keys are
  rejected. Every record is validated against a strict schema, including any JSON Schema it
  embeds. At most 200 issues are reported.
- Dataset content is treated as **untrusted data**: it is never executed, evaluated, or used as a
  template. Tool definitions in datasets are descriptions only. The UI renders content as text
  (React escaping, no raw HTML), and long fields are truncated in previews.
- Versions are immutable, and each one is identified by a SHA-256 content hash anyone can
  recompute from the export.

### Benchmark runs

- Run snapshots, run results, events, and metrics never contain secrets. Endpoint snapshots keep
  only the secret reference name, and secret headers stay as references (as in M1).
- Model responses are stored for inspection. Set `RUN_STORE_RESPONSES=false` to keep only timing
  and token metadata. Stored text is capped by `RUN_RESPONSE_MAX_CHARS`.
- Server limits bound what one run can ask for: `RUN_MAX_REQUESTS`, `RUN_MAX_CONCURRENCY`,
  run duration, and endpoints per run. Outbound requests still pass through the network policy
  of M1.
- Runs send traffic to model servers you register. Benchmarking a server you don't operate can
  look like a load attack; only target endpoints you are allowed to load-test.

### Evaluation and tool calling

- **No tool is ever executed.** Tool-calling evaluation either inspects the calls
  (`generation_only`) or answers them with fixture results stored in the dataset (`simulated`).
  Arguments are validated against the tool's JSON Schema before a fixture is chosen. Real
  execution ("integration" mode) is rejected by the API.
- Dataset content, model outputs, and tool arguments are treated as untrusted data: they are
  parsed as JSON and compared, never evaluated as code. Fixture results sent back to the model
  are capped in size.
- Embedding similarity calls go through the same adapter, network policy, timeouts, and secret
  handling as chat requests, and the run snapshot stores only a secret-free endpoint snapshot.
- Evaluation evidence and rationales contain dataset and model text only, never secrets.

### Planned

Sandboxed tool execution ("integration" mode) and authentication are tracked in later milestones.
