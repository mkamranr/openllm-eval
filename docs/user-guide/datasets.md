# Dataset format

Datasets are UTF-8 **JSONL** (one JSON object per line) or **CSV** files. Each record is one
test case. A dataset either declares a single case type, in which case records may omit
`type`, or is `mixed`, in which case every record states its own `type`.

Importing validates every record and reports problems with their line numbers. A valid import
becomes an immutable **version**. Its `content_hash` is the SHA-256 of the normalized JSONL
export, so the same records produce the same hash whether they came from CSV or JSONL. To check
a version yourself, run `sha256sum` on its export. Record order matters: it defines the case
order in runs.

## Common fields

| Field | Required | Description |
|---|---|---|
| `id` | yes | Unique within the version (up to 200 characters, no control characters) |
| `type` | in mixed datasets | One of the case types below |
| `input` | one of `input` / `messages` | The user prompt |
| `messages` | | A chat history: `[{"role": "system" \| "user" \| "assistant" \| "tool", "content": "…"}]` |
| `system_prompt` | | A system message prepended to the request |
| `reference_answer` | depends on type | The expected answer |
| `tags` | | A list of strings |
| `metadata` | | A free-form object. `metadata.acceptable_answers` lists alternative correct answers |
| `split` | | `train`, `dev`, or `test` |

## Case types

### `qa`
Question answering. Requires `reference_answer`.

```json
{"id": "qa-1", "type": "qa", "input": "What is the capital of Japan?", "reference_answer": "Tokyo", "metadata": {"acceptable_answers": ["Tōkyō"]}}
```

### `chat` and `instruction`
Open prompts. `reference_answer` is optional; answer-quality evaluators skip cases that don't
have one. `instruction` cases can list `constraints` (human-readable; they are not yet checked
automatically).

### `structured_output`
Requires `json_schema`. `expected_output` is optional and must itself match the schema.

```json
{"id": "json-1", "type": "structured_output", "input": "Extract name and email: 'Reach Maria at maria@example.com'", "system_prompt": "Return only JSON.", "json_schema": {"type": "object", "properties": {"name": {"type": "string"}, "email": {"type": "string"}}, "required": ["name", "email"], "additionalProperties": false}, "expected_output": {"name": "Maria", "email": "maria@example.com"}}
```

### `tool_call`

| Field | Description |
|---|---|
| `tools` | OpenAI-style function definitions. Each `parameters` is a JSON Schema |
| `expected_behavior` | `call_tool` (default), `no_tool`, `ask_clarification`, or `refuse` |
| `expected_tool_calls` | `[{"name", "arguments"}]`. Required for `call_tool` and must be empty otherwise |
| `ordered_calls` | `true` if the calls must happen in this order |
| `tool_results` | Fixtures for simulated execution: `{"name", "arguments" (or omit to match any call), "result"}` |
| `expected_final_answer` | Checked after the simulated tool round trip (whole-word, normalized) |

```json
{"id": "tool-1", "type": "tool_call", "input": "What's the weather in Abu Dhabi?", "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}], "expected_tool_calls": [{"name": "get_weather", "arguments": {"city": "Abu Dhabi"}}], "tool_results": [{"name": "get_weather", "result": {"temperature": 31, "condition": "sunny"}}], "expected_final_answer": "31"}
```

Tools are never executed. During a run, a valid call receives the fixture whose `arguments`
match. String comparison ignores case, and `1` matches `1.0`. A fixture without `arguments`
acts as a fallback. Anything else gets a structured error result.

### `rag`
Requires `retrieved_contexts` (`[{"text", "document_id", "chunk_id", "source", "page"}]`).
`expected_citations` must refer to documents in the contexts. Set `answerable: false` when the
contexts don't contain the answer. RAG evaluators arrive in Milestone 5.

### `long_context`
Requires `context` and `expected_answer`. `answer_location.approximate_position` (0 to 1) records
where the answer is located.

### `safety`
Requires `expected_behavior`: `refuse` or `comply`. The answer-quality suite checks it with a
refusal-phrase heuristic, which only covers English.

## CSV conventions

- The first row holds the column names; empty cells are omitted.
- `messages`, `metadata`, `tools`, `expected_tool_calls`, `tool_results`,
  `retrieved_contexts`, `expected_citations`, `json_schema`, and `answer_location` cells contain
  JSON.
- `tags` is comma-separated text or a JSON array. `constraints` is a single text value or a JSON
  array.
- `answerable` and `ordered_calls` accept `true`/`false`, `1`/`0`, and `yes`/`no`.
- Dotted columns such as `metadata.source` become nested fields.
- `expected_output` is parsed as JSON when possible and kept as text otherwise.

```csv
id,input,reference_answer,tags
q1,What is DNS?,It maps names to addresses,net
q2,What is TCP?,A reliable transport,"net,basic"
```

## Limits

Set in `.env`: `DATASET_MAX_UPLOAD_BYTES` (25 MB), `DATASET_MAX_RECORDS` (20,000), and
`DATASET_MAX_RECORD_BYTES` (4 MB).

## Validate before uploading

```bash
openllm-eval dataset validate cases.jsonl --type qa
```
