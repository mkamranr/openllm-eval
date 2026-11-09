# Bundled sample datasets

All files in this directory are **original synthetic content written for OpenLLM-Eval** and are
dedicated to the public domain under **CC0-1.0**
(<https://creativecommons.org/publicdomain/zero/1.0/>). Company names, people, addresses, codes,
and policies are fictional. `long_context_needle.jsonl` was generated with a fixed random seed
(see `metadata.generator` in each record).

They are small on purpose: they demonstrate the record formats and give a quick smoke test of an
endpoint. They are **not** a benchmark and should not be used to rank models.

| File | Type | Cases |
|---|---|---|
| `general_qa.jsonl` | `qa` | 12 |
| `tool_calling.jsonl` | `tool_call` | 8 |
| `rag_grounding.jsonl` | `rag` | 6 |
| `structured_output.jsonl` | `structured_output` | 4 |
| `long_context_needle.jsonl` | `long_context` | 3 |

Install them from the UI (**Datasets → Add sample datasets**), with the API
(`POST /api/v1/datasets/samples/{sample_id}/install`), or validate one locally with
`openllm-eval dataset validate app/datasets/samples/general_qa.jsonl`.
