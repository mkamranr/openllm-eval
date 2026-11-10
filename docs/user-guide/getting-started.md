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

On a first install, `deploy.sh` ends by printing the administrator's sign-in:
`admin` and a generated password. Sign in, and choose a new password. Then add users under
Settings → Users, or connect Active Directory under Settings → Authentication. See
[Authentication and roles](authentication.md).

For the CLI, run `openllm-eval auth login` once; it stores an API token for later commands.

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
| RAG evaluation | rag | faithfulness, answer correctness, answer relevance (judge), context relevance, precision, and recall, citation precision and recall, abstention |
| Hallucination check | rag, long_context | each claim classified as supported, unsupported, contradicted, or unverifiable |

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

For RAG and hallucination checks:

- **Without a judge** (the default), claims are checked lexically: a claim is supported when
  enough of its words appear in one passage (or two), and contradicted when it states a number
  that the closest evidence contradicts and no passage mentions. Paraphrases often come out as
  *unverifiable* and are flagged for review. Answer relevance needs a judge and is skipped.
- **LLM judge (optional)**: pick any registered endpoint. It extracts and verifies claims and
  rates relevance. Questions, contexts, and answers are sent to it. Judging runs after each
  model has been measured, so it never slows the measured requests; the run page shows
  "Judging n/N". A judge can be wrong: every verdict keeps its rationale and quoted evidence,
  and a quote that is not in the passage is flagged.
- **Faithfulness pass threshold** (default 0.8). A contradicted claim always fails the case.

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
- **Evidence and claims** (RAG and hallucination runs): claim-level rates pooled over all
  answers, with supported, unsupported, contradicted, and unverifiable claims kept apart, and
  abstention precision and recall. The card says whether a judge was used, and warns when the
  judge is also a model under test.
- **Results**: every attempt, with tabs for failures, quality failures, and warm-up. Click a
  case to see the request, the response, each evaluator's verdict with its reason and evidence,
  each claim with its verdict, evidence excerpt, and source, and the simulated tool
  transcript.
- **Configuration**: the immutable snapshot of what ran, including endpoint settings (secret
  references only), dataset versions and hashes, evaluator versions, and the profile.

How to interpret the numbers:

- "—" means the value could not be measured; hover over it for the reason. Examples: TTFT
  without streaming, token throughput when the server did not report token counts, or a pass
  rate with no applicable cases.
- Warm-up requests are never included in any metric.
- Failed requests are not evaluated for quality. `evaluated requests` shows how many were.
- Faithfulness compares claims with the supplied contexts only. A supported claim is not proven
  true, and the contexts themselves may be wrong.
- After **Retry failed**, throughput becomes unavailable, because the measurement is no longer
  one contiguous window.

## 7. Save evaluation profiles

**Evaluation Profiles** stores a set of evaluation types with their quality, load, and
generation settings. You can create a profile on that page, with **Save as profile** in the
wizard, through the API, or reference one by name in a CLI config (`profile: nightly-quality`).
A run copies the profile's settings when it is created, so editing a profile never changes past
runs.

## 8. Reports and comparison

On a finished run's page, the **Reports** card generates:

- **HTML report**: a single file with charts, every metric, failures, the configuration, the
  methodology, and how to reproduce the run. It has no scripts and loads nothing, so you can
  archive it or email it and open it offline.
- **JSON export** (everything in the report), **results CSV** (one row per attempt), **metrics
  CSV**, and a **Markdown summary**.

Turn on **Leave out generated text** or **Hide endpoint URLs** before sharing a report outside
your team. All reports are listed under **Reports**.

**Compare** on a run page (or **Compare Runs** in the sidebar) puts models of several runs side by
side against a baseline. Start by reading the comparability label:

- **Equivalent configuration**: same datasets, versions, and settings. Differences come from the
  models (or from the server between runs).
- **Partially controlled**: generation or load settings differ; deltas mix model and setting.
- **Not directly comparable**: datasets or evaluation methods differ, so the numbers measure
  different things.

Each difference is listed, and **Per-case changes** shows which cases went from pass to fail.
There is no significance test, so treat small differences on small datasets with care. **Re-run**
repeats a run with the same configuration, which is the easiest way to check how much results vary
from run to run.

From the CLI: `openllm-eval report generate <run-id> -f html` and
`openllm-eval compare <run-a> <run-b>`.

## 9. Context-window and quantization experiments

**Context window.** Generate a needle dataset (**Datasets → Generate**): choose the requested
context lengths, the needle depths, and a seed. Keep the lengths within your server's
`max_model_len`, or overflow is all you will measure. Run the dataset with the **Context
window** evaluation type.

For each requested length, the run page shows the success and overflow rates, TTFT, and needle
retrieval (overall and by early, middle, or late position). It also shows three token counts,
kept apart:

- the requested length;
- the estimate (4 characters per token);
- the input tokens the server reported.

When the server reports far fewer tokens than estimated, the result is flagged as possible
truncation. A request that succeeds at a length doesn't prove the model uses that much context,
so read retrieval alongside it.

**Quantization.** Register one endpoint per variant (for example FP16, AWQ 4-bit, GGUF Q4). On
each, fill in **Model variant (declared)**: model family, quantization method, weight precision
and bits, and KV-cache dtype. Leave unknown fields blank: they stay unknown and are never guessed
from the model name. Run the same datasets on every variant, then compare the runs.

The **Deployment variants** card shows:

- the declared settings side by side;
- quality and performance next to each other;
- context-window results per length;
- warnings when the variants aren't comparable (different family, undeclared quantization,
  different datasets).

**GPU telemetry (optional).** To see memory savings, add a Prometheus exporter URL under
**Resource telemetry** on the endpoint. You can use the NVIDIA DCGM exporter
(`http://gpu-host:9400/metrics`), nvidia_gpu_exporter, or vLLM's own `/metrics` for KV-cache
usage. Allow the exporter's host in the network policy (`ENDPOINT_ALLOWED_HOSTS`). It is scraped
while the model is measured. If it is missing or fails, the run continues and the GPU metrics
show why they are unavailable.

## Next steps

- Automate runs with the [CLI](../reference/cli.md).
- Build your own datasets with the [dataset format](datasets.md).
- Integrate through the [REST API](../reference/api.md).
