# Tool sandbox (integration mode)

Tool-calling evaluations can answer the model's tool calls in three ways (spec §5.7):

| Mode | What happens to a tool call |
|---|---|
| **Inspect calls only** (`generation_only`) | Nothing. The call is evaluated as returned; no follow-up request is sent |
| **Simulated results** (`simulated`, default) | The dataset's `tool_results` fixture is returned to the model. Nothing runs |
| **Execute in sandbox** (`integration`) | The call is executed by an administrator-declared tool in the isolated **tool runner**, and the model continues with the real result |

Integration mode is **off by default**. It exists to test a model against real (test) tools,
for example a calculator, a lookup in a fixture database, or an HTTP call to a staging service.

## How it is isolated

```
worker ──(tools network, internal, token)──► tool-runner ──► script (fresh process, limits)
                                                         └──► HTTP test service (allowlisted)
```

- **A separate container** (`tool-runner`, Compose profile `tools`):
  - read-only root filesystem, and a 64 MB `/tmp`;
  - non-root user, all Linux capabilities dropped, `no-new-privileges`;
  - process, memory and CPU limits;
  - no Docker socket.
- **Networks.** It is attached only to the internal `tools` network, shared with the worker and
  the API. That network has no internet access and no route to PostgreSQL or Redis. It gets no
  `secrets.env`. A tool can reach the API there, as an anonymous client: the API still requires
  sign-in.
- **Only declared tools run.** Tools are declared in `tools/tools.yaml`, which is mounted
  read-only. They cannot be added or changed through the UI or the API, so a compromised
  account cannot add code. Review changes to `tools/` like code.
- **Every call is checked twice:**
  - The worker validates the arguments against the tool schema the dataset gave the model.
  - The runner validates them again against the declared `parameters`.
  - Calls to tools that are not declared are refused.
- **Script tools** run in a fresh `python -I -S` process for every call:
  - The arguments arrive as JSON on stdin; the result is one JSON value on stdout.
  - The process has an empty environment and a temporary working directory (deleted
    afterwards), in its own process group.
  - Limits: CPU time, memory (`TOOL_SCRIPT_MEMORY_MB`), file size, open files, and no core
    dumps.
  - On timeout, the whole process group is killed. Output over `max_output_bytes` is an error.
- **HTTP tools** only reach hosts in `TOOL_ALLOWED_HOSTS`:
  - The check is repeated, with DNS resolution, before every call.
  - Redirects are not followed, and proxy variables are ignored.
  - Responses are capped.
  - Headers must be literal and non-secret: `Authorization`, cookies and `env:` references
    are rejected.
- The **worker authenticates** to the runner with `TOOL_RUNNER_TOKEN`.
- Tool results are cut to 2,000 characters before the model sees them. Follow-up requests are
  never part of the performance metrics.

## Enable it

1. In `.env`, set `TOOL_EXECUTION_ENABLED=true`.
2. Deploy with the tool runner:

   ```bash
   bash scripts/deploy.sh --tools          # add --mock to try it with the mock model
   ```

   On the first `--tools` deploy, the script generates `TOOL_RUNNER_TOKEN` and copies
   `tools/tools.example.yaml` to `tools/tools.yaml`. That file declares two example script
   tools, `calculator` and `lookup_order`.
3. Check **Settings → Tools** (administrators). It shows whether the runner is reachable, the
   registry hash, and the declared tools. The readiness check (`/health/ready`) reports
   `tool_runner` too.
4. In **New Evaluation**, select *Tool calling*, then **Execute in sandbox** under Tool calls.
   The option is disabled, with the reason, while the runner is unavailable.

When a run is created, it is checked:

- The runner must be reachable and its `tools.yaml` valid.
- Every tool offered by the dataset's `tool_call` cases must be declared. Otherwise the run is
  rejected (422) with the missing names.

The run snapshot records the `tools.yaml` SHA-256 and each tool's `version`, so a report shows
exactly which tools answered.

## Declare tools

```yaml
tools:
  - name: calculator              # the function name the model calls
    description: Evaluates an arithmetic expression.
    version: "1"                  # recorded with runs; bump it when behaviour changes
    kind: script
    script: scripts/calculator.py # under tools/
    timeout_seconds: 5            # max 60
    max_output_bytes: 65536       # max 1 MiB
    parameters:                   # JSON Schema; non-matching calls are refused
      type: object
      properties:
        expression: {type: string, maxLength: 200}
      required: [expression]
      additionalProperties: false
```

A **script** reads its arguments and prints its result:

```python
import json, sys
args = json.load(sys.stdin)
print(json.dumps({"result": len(args["text"])}))
```

Scripts can use the Python standard library only. They can read files under `tools/` (it is
read-only) and write only to their temporary working directory. A tool-level error is just a
result, e.g. `{"error": "order not found"}`. The model sees it like any other result. A crash, a
timeout, or output that is not JSON is recorded as a failed tool call.

An **HTTP** tool sends the arguments to a test service:

```yaml
  - name: check_stock
    version: "1"
    kind: http
    http:
      url: http://inventory-test:8080/stock
      method: POST            # POST: JSON body; GET: query parameters
      headers: {X-Client: openllm-eval}
    parameters:
      type: object
      properties: {sku: {type: string}}
      required: [sku]
```

For an HTTP tool to work:

1. Add the host to `TOOL_ALLOWED_HOSTS`, e.g. `TOOL_ALLOWED_HOSTS=inventory-test`, or an IP
   or CIDR.
2. Give the runner a network path to the service. It has none by default. For a service in
   another Compose project, attach that service to this project's tools network
   (`openllm-eval_tools`):

   ```bash
   docker network connect openllm-eval_tools inventory-test
   ```

   Alternatively, add a `docker-compose.override.yml` that attaches `tool-runner` to the
   service's network. Only attach test services that are meant to be called by models.

The runner re-reads `tools.yaml` when it changes; no restart is needed. If the file is invalid,
the runner offers no tools, and Settings → Tools shows the error.

## Results

On the result page, an integration-mode attempt shows **Executed tool calls**. For each call, it
shows the arguments, the result the model received, the execution time, and whether it actually
ran. *Not executed* means the call was refused before running, for example for invalid
arguments or an unreachable runner. The tool-calling evaluators are the same as in simulated
mode. The final answer is judged on the model's answer after the real tool results.

If the runner fails during a run, each affected tool call is recorded with its error. The model
receives the error as the tool result, and the run continues.

## Settings

| Variable | Default | Description |
|---|---|---|
| `TOOL_EXECUTION_ENABLED` | `false` | Allow integration-mode runs (API and worker) |
| `TOOL_RUNNER_TOKEN` | generated by `deploy.sh --tools` | Shared secret between the worker/API and the runner |
| `TOOL_ALLOWED_HOSTS` | (empty) | Destinations HTTP tools may call. Empty = HTTP tools are blocked |
| `TOOLS_FILE` | `tools.yaml` | Registry file name under `tools/` |
| `TOOL_SCRIPT_MEMORY_MB` | `256` | Address-space limit of each script process |
| `TOOL_RUNNER_MAX_CONCURRENCY` | `8` | Tool calls executed at the same time |
| `TOOL_RUNNER_MEMORY` / `TOOL_RUNNER_CPUS` | `1g` / `1.0` | Container limits of the runner |

## Limits

- Scripts run as processes inside one container, not in separate containers or a VM sandbox
  (gVisor, Firecracker). Treat declared scripts as trusted code that the limits keep in bounds,
  not as untrusted code.
- Resource limits are POSIX features. They apply in the Linux container; on a Windows
  development machine, only timeouts and output caps apply.
- HTTP tools have the same short DNS-rebinding window as model endpoints (see SECURITY.md).
