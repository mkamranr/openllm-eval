# Getting started

This guide takes you from an empty server to a finished evaluation, then explains how to read the
results.

## 1. Deploy

```bash
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD; set BIND_ADDRESS=0.0.0.0 to browse from another machine.
bash scripts/deploy.sh
```

`deploy.sh` builds the images, applies the database migrations, starts PostgreSQL, Redis, the
API, the worker, and the UI, and runs `scripts/smoke-test.sh`. Open http://localhost:3000. The
dashboard shows the status of the API, the database, Redis, and the worker.

> There is no authentication yet. Keep `BIND_ADDRESS=127.0.0.1`, or only expose the stack on a
> trusted network.

## 2. Connect a model endpoint

**Models / Endpoints → Add endpoint.**

| Field | Example |
|---|---|
| Base URL | `http://vllm.lan:8000/v1`, `http://ollama:11434/v1`, `https://api.openai.com/v1` |
| Model identifier | `Qwen/Qwen2.5-7B-Instruct`, `llama3`, `gpt-4o-mini` |
| Secret reference | `env:OPENLLM_SECRET_VLLM_KEY` (optional) |

API keys never go into the database. Put the key in `secrets.env` as `OPENLLM_SECRET_<NAME>=…`,
run `docker compose up -d api worker`, and reference it by name. Request defaults (timeouts,
retries, `max_tokens`, streaming, and extra headers) are set per endpoint.

Private addresses are blocked by default to prevent SSRF. For a model server on your network,
list it in `ENDPOINT_ALLOWED_HOSTS` (for example `vllm.lan,10.0.5.0/24`) or set
`ENDPOINT_ALLOW_PRIVATE_NETWORKS=true`. See [Configuration](../reference/configuration.md).

Click **Test** to send one request. The test reports latency, TTFT, token counts, and whether
the server reported its token usage.

## 3. Add a dataset

**Datasets → Import dataset** accepts a `.jsonl` or `.csv` file. Every record is validated, and the
report lists any problems with their line numbers before anything is saved. Each import creates
an immutable version with a SHA-256 content hash; importing again adds a new version.

To start quickly, open the **Built-in samples** tab and click **Install**. The bundled samples are general Q&A, structured output,
tool calling, RAG grounding, and a long-context needle test.

The format is described in [Dataset format](datasets.md).

## 4. Run an evaluation

**New Evaluation** is a four-step wizard. Your draft is saved in the browser as you go.

1. **Basic setup**: a name, an optional project, and one or more models.
2. **Evaluation types**: types marked *Available* can run now:
   - **Performance** metrics are recorded on every run.
   - **Answer quality**, **Structured output**, and **Tool-calling** add evaluators (step 5).
   - **Run settings** sets the load profile, request count, concurrency, warm-up, streaming,
     timeout, retries, temperature, and max tokens.
   - **Start from a profile** fills everything from a saved profile.
3. **Datasets**: datasets that match the selected types are marked *Suggested*.
4. **Review & Run**: check the summary, optionally **Save as profile**, then click **Start
   Evaluation**.

### Load profiles

| Profile | Use it for |
|---|---|
| Smoke | At most 5 requests, concurrency ≤ 2: a quick check that the setup works |
| Sequential | One request at a time: clean single-request latency |
| Fixed concurrency | N requests in flight until the request count is reached |
| Ramp | Increasing concurrency steps, with metrics per step: find the saturation point |
| Sustained | N requests in flight for a fixed duration: stability under load |

Each request uses the next dataset case in order (request *i* uses case *i mod case count*).
Endpoints are benchmarked one after another, so models never compete for the same server.

## 5. Quality evaluation

Each quality suite reads the case types it understands; other cases are skipped.

| Suite | Reads | Checks |
|---|---|---|
| Answer quality | qa, chat, instruction, long_context, safety | exact match, normalized match, contains reference, token F1, refusal behaviour (safety), embedding similarity (optional) |
| Structured output | structured_output | valid JSON, JSON Schema conformance, expected fields |
| Tool-calling | tool_call | tool selection, argument names, schema, required arguments and values, call order, invented tools, final answer, task success |

Settings:

- **Tool calls: Simulated results** (default). When the model calls a tool, the call is
  validated against the tool's schema and answered with the matching `tool_results` fixture
  from the dataset (or a structured error). The model is then asked to continue, for up to 3
  rounds, so its final answer can be checked. **Nothing is ever executed.**
- **Tool calls: Inspect calls only.** The calls are scored as returned, and final answers are
  not evaluated.
- **Token F1 pass threshold** (default 0.5).
- **Embedding similarity**: pick an endpoint that serves `/embeddings`. It measures similarity,
  not correctness.
- **Accept JSON inside a code fence**: fenced JSON counts as valid, but it is labeled
  separately.

A run is rejected if none of its datasets contains a case type for a selected quality suite.

## 6. Read the results

The run page updates live while the run executes.

- **Metrics by model**: request counts, error rate, TTFT, latency percentiles, inter-token
  latency, tokens/s, and throughput. Ramp runs add a table with one row per step.
- **Quality**: one pass rate and one mean score per evaluator. There is no combined score.
  *n* is the number of cases where the evaluator applied.
- **Tool-call metrics**: selection accuracy, argument accuracies, precision and recall, and
  error rates. Argument metrics count only cases where the right tool was chosen, so a wrong
  tool is not also counted as an argument error.
- **Results**: every attempt, with tabs for failures, quality failures, and warm-up. Click a
  case to see the request, the response, each evaluator's verdict with its reason and evidence,
  and the simulated tool transcript.
- **Configuration**: the immutable snapshot of what ran, including endpoint settings (secret
  references only), dataset versions and hashes, evaluator versions, and the profile.

How to interpret the numbers:

- "—" means the value could not be measured; hover over it for the reason. Examples: TTFT
  without streaming, token throughput when the server did not report token counts, or a pass
  rate with no applicable cases.
- Warm-up requests are never included in any metric.
- Failed requests are not evaluated for quality. `evaluated requests` shows how many were.
- After **Retry failed**, throughput becomes unavailable, because the measurement is no longer
  one contiguous window.

## 7. Save evaluation profiles

**Evaluation Profiles** stores a set of evaluation types with their quality, load, and
generation settings. You can create a profile on that page, with **Save as profile** in the
wizard, through the API, or reference one by name in a CLI config (`profile: nightly-quality`).
A run copies the profile's settings when it is created, so editing a profile never changes past
runs.

## Next steps

- Automate runs with the [CLI](../reference/cli.md).
- Build your own datasets with the [dataset format](datasets.md).
- Integrate through the [REST API](../reference/api.md).
