# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately. Use GitHub's "Report a vulnerability" (private security
advisory) on this repository rather than opening a public issue. Include steps to reproduce,
affected versions, and the impact. We aim to acknowledge reports within 5 business days.

## Current security posture (Milestone 9)

- **Sign-in is required** for every API route except health checks and the sign-in endpoints
  (see [Authentication](#authentication-and-roles) below). By default, Compose still binds
  published ports to `127.0.0.1`. For access beyond a test network, put a TLS proxy in front of
  the UI.
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
Only editors and administrators can register endpoints and trigger requests within this
policy.

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

- **By default, no tool is ever executed.** Tool-calling evaluation either inspects the calls
  (`generation_only`) or answers them with fixture results stored in the dataset (`simulated`).
  Arguments are validated against the tool's JSON Schema before a fixture is chosen. Real
  execution (`integration`) is available only when an administrator enables the tool sandbox
  (below).
- Dataset content, model outputs, and tool arguments are treated as untrusted data: they are
  parsed as JSON and compared, never evaluated as code. Fixture results sent back to the model
  are capped in size.
- Embedding similarity calls go through the same adapter, network policy, timeouts, and secret
  handling as chat requests, and the run snapshot stores only a secret-free endpoint snapshot.
- Evaluation evidence and rationales contain dataset and model text only, never secrets.

### LLM judge

- The judge is **opt-in** per run and off by default. When enabled, the questions, retrieved
  contexts, and answers of the RAG and long-context cases are sent to the judge endpoint. Choose
  a judge you would trust with that data; a judge on your own infrastructure keeps it local.
- Judge requests go through the same adapter, network policy, timeouts, and secret handling as
  every other model call. The snapshot stores a secret-free judge endpoint snapshot.
- Prompt-injection hygiene: case content is wrapped in `<data>` blocks that it cannot close, the
  instructions tell the judge to treat it as data, and the reply must be a JSON object of a
  fixed shape. A malformed or failed reply fails the evaluation; it never passes. Quoted evidence
  is checked against the named passage. These measures reduce, but cannot rule out, a judge
  being manipulated by adversarial dataset or answer text, so verdicts keep their rationale for
  review.

### Reports

- HTML reports contain no scripts and load no external resources. Every value from the database
  (model output, dataset text, run names, error messages) is HTML-escaped.
- Report downloads are served with `Content-Security-Policy: default-src 'none';
  style-src 'unsafe-inline'; img-src data:; sandbox`, `X-Content-Type-Options: nosniff`, and
  `Cache-Control: no-store`. Even if escaping failed, the browser would run nothing.
- CSV cells that start with `=`, `+`, `-`, `@`, tab, or carriage return are prefixed with `'`, so
  spreadsheets don't evaluate them as formulas.
- Reports contain generated text and endpoint URLs unless they are generated with
  `redact_content` / `redact_endpoints`. API keys are never included; snapshots only ever hold
  a secret reference name.
- Reports are stored in the database and readable by every signed-in user (viewer and
  above). Downloads are recorded in the audit log. They are deleted with their run.

### Resource telemetry

- Exporter URLs go through the same outbound network policy as model endpoints. They are
  validated when the endpoint is saved and checked again before every scrape. URLs with
  embedded credentials are rejected, redirects are not followed, proxy environment variables
  are ignored, and responses are capped at 4 MB.
- Telemetry exporters are expected to be unauthenticated and on a trusted network. No secrets
  are sent to them.
- Scrape failures are recorded (a count and a short last-error message) and never fail a run.
  `redact_endpoints` replaces telemetry URLs in reports.

### Authentication and roles

- Roles `viewer` < `editor` < `admin` are enforced centrally in the API router, not route by
  route: reads need `viewer`, changes need `editor`, and users, authentication settings and the
  audit log need `admin`. A test checks that every non-public route rejects anonymous requests.
- **Passwords** are hashed with argon2id. The policy: at least 12 characters, at least 5
  different characters, and not containing the username. Unknown usernames are checked against
  a dummy hash, so timing does not reveal which accounts exist.
- **Sessions** are random tokens in an HttpOnly, SameSite=Lax cookie. The cookie is Secure on
  HTTPS. Only a SHA-256 hash is stored. Sessions have an idle and an absolute timeout.
  Changing a password ends the user's other sessions.
- **CSRF**: cookie-authenticated changes need the `X-OpenLLM-CSRF` header, and an `Origin`, when sent, must be
  this site or a trusted origin. Cross-site forms cannot set the header.
- **API tokens** (`oel_…`) are shown once and stored as SHA-256 hashes. They can expire and be
  revoked, and they act with their owner's current role. Disabled accounts' tokens stop working.
- **Lockout**: failures are counted per username and per client address, in Redis. If Redis is
  unavailable, the lockout is skipped (fails open) rather than blocking all sign-ins.
- **Bootstrap administrator**: created only when there are no users. It must change its password
  before any other request succeeds (`403 password_change_required`).
- **Directory secrets** (the LDAP bind password and the Kerberos keytab) are encrypted at rest
  with AES-256-GCM. The key is derived (HKDF) from `AUTH_SECRET_KEY`, and each value is bound to
  its purpose. They are write-only in the API, and never logged or returned.
- **LDAP**: LDAPS or StartTLS, with certificate verification on by default and an optional
  private CA. User input is escaped in search filters, and empty passwords are rejected (they
  would be an unauthenticated bind). Directory URLs may not point at this deployment's own
  services (`ENDPOINT_BLOCKED_HOSTS`). Unlike model endpoints, they may be on private networks.
- **Kerberos**: the keytab is written to a private (0600) temporary file only for the duration
  of a check.
- **Audit log**: sign-ins (including failures), sign-outs, password, user, role, token and
  settings changes, and report downloads. Details are redacted like logs.
- Logs redact session cookies, `oel_` tokens and `Negotiate` headers.
- `AUTH_ENABLED=false` turns all of this off (every request is an administrator). The UI and the
  readiness check warn about it.

**Residual risk:** the API trusts `X-Forwarded-For` from any address (`FORWARDED_ALLOW_IPS=*`),
so that the audit log and the per-address lockout see real client addresses behind the bundled
proxy. A client calling the API port directly can forge the header. Keep the API port bound to
`127.0.0.1`, or set `FORWARDED_ALLOW_IPS` to the proxy's address. Per-username lockout is not
affected.

### Tool sandbox (integration mode)

See the [tool sandbox guide](docs/user-guide/tool-sandbox.md).

- **Off by default.** It needs `TOOL_EXECUTION_ENABLED=true` and the `tool-runner` service
  (`deploy.sh --tools`). Runs asking for it are otherwise rejected with a 422.
- **Only administrator-declared tools run.** They are listed in `tools/tools.yaml`, which is
  mounted read-only. There is no UI or API to add or change tools. Each run records the file's
  SHA-256 and every tool version.
- **The tool-runner container:**
  - read-only root filesystem, a 64 MB `/tmp`;
  - non-root user, `cap_drop: ALL`, `no-new-privileges`;
  - process, memory and CPU limits;
  - no Docker socket;
  - no `secrets.env`;
  - attached only to the internal `tools` network: no internet, no PostgreSQL or Redis.
- The worker and the API authenticate to the runner with `TOOL_RUNNER_TOKEN`, compared in
  constant time.
- **Arguments** are validated twice: against the schema the dataset offered the model, and
  against the declared `parameters`.
- **Script tools** run in a fresh `python -I -S` process per call:
  - empty environment;
  - a temporary working directory, deleted afterwards;
  - its own process group, killed on timeout;
  - rlimits on CPU time, address space, file size, open files and core dumps;
  - output capped.
- **HTTP tools:**
  - only reach `TOOL_ALLOWED_HOSTS` (an empty allowlist blocks them all), checked with DNS
    before every call;
  - redirects are not followed, proxy variables are ignored, and responses are capped;
  - credential headers and `env:` references are rejected in `tools.yaml`.
- Tool results are cut to 2,000 characters before they reach the model.

**Residual risk:**

- Scripts share the runner container; they are isolated from each other by process, not by
  separate containers or a VM sandbox. Declared scripts are reviewed code kept in bounds by the
  limits, not arbitrary untrusted code.
- A tool can reach the API over the `tools` network, as an unauthenticated client.
- HTTP tools have the same DNS-rebinding window as model endpoints.
