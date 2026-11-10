"""Mock OpenAI-compatible model server for tests, demos, and smoke checks.

Run with::

    python -m app.testing.mock_openai --host 0.0.0.0 --port 9000

and register ``http://<host>:9000/v1`` as an endpoint. It never calls a real model.

Behaviour is controlled per request with headers, which can be set as endpoint custom headers:

=============================  ==============================================================
``X-Mock-Mode``                ``echo`` (default), ``tool_call``, ``malformed``,
                               ``stream_error``, ``no_usage``
``X-Mock-Response``            fixed response text
``X-Mock-Status``              force an HTTP error status (e.g. 429, 500)
``X-Mock-First-Token-Delay-Ms``  delay before the first content chunk (stream) or response
``X-Mock-Chunk-Delay-Ms``      delay between subsequent stream chunks
``X-Mock-Tool-Args``           JSON object used as tool-call arguments in ``tool_call`` mode
``X-Mock-Tool-Name``           tool name to call in ``tool_call`` mode (default: the first tool)
``X-Mock-Fail-If-Contains``    fail (HTTP ``X-Mock-Fail-Status``, default 503) when the last
                               user message contains this text
``X-Mock-Fail-First``          fail the first N requests for each distinct last user message,
                               then succeed (exercises retries)
=============================  ==============================================================

After a tool call, a request whose last message has role ``tool`` gets a plain-text answer
that quotes the tool result (``Based on the tool result: ...``).

Scripted answers make quality evaluations deterministic: a JSON list of entries
``{"match": "text", "response": "...", "tool_calls": [{"name": ..., "arguments": {...}}],
"after_tools": "..."}``. The first entry whose ``match`` occurs in the last user message wins;
``after_tools`` answers the follow-up request after tool results. Scripts take precedence over
``X-Mock-Mode``.

``POST /v1/embeddings`` returns deterministic bag-of-words vectors (64 dimensions), so texts with
the same words are identical and unrelated texts are dissimilar.

Environment:

* ``MOCK_MODELS``: comma-separated model ids (default ``mock-model``).
* ``MOCK_API_KEY``: if set, requests must send ``Authorization: Bearer <key>``.
* ``MOCK_SCRIPT``: path to a JSON script file (see above).

Judge requests from OpenLLM-Eval (their system message carries [openllm-eval-judge:<task>])
get a deterministic mock verdict in the judge's JSON format (see mock_judge), so the LLM-judge
path can be tried without a real judge model.

Token counts are whitespace word counts (deterministic, not a real tokenizer).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.testing.mock_judge import judge_reply, judge_task

MAX_DELAY_MS = 60_000


def _delay(request: Request, header: str) -> float:
    try:
        return min(max(int(request.headers.get(header, "0")), 0), MAX_DELAY_MS) / 1000.0
    except ValueError:
        return 0.0


def _error(status: int, message: str, err_type: str = "mock_error") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type, "code": status}},
    )


def _word_count(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += len(content.split())
    return total


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


EMBEDDING_DIMENSIONS = 64


def _embedding(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for word in re.findall(r"\w+", text.casefold()):
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        vector[digest[0] % EMBEDDING_DIMENSIONS] += 1.0 if digest[1] % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def _load_script() -> list[dict[str, Any]]:
    path = os.environ.get("MOCK_SCRIPT")
    if not path:
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else []


def create_mock_app(
    models: list[str] | None = None,
    api_key: str | None = None,
    script: list[dict[str, Any]] | None = None,
) -> FastAPI:
    models = models or [
        m.strip() for m in os.environ.get("MOCK_MODELS", "mock-model").split(",") if m.strip()
    ]
    api_key = api_key if api_key is not None else os.environ.get("MOCK_API_KEY") or None
    attempts: dict[str, int] = {}
    script = script if script is not None else _load_script()
    app = FastAPI(title="OpenLLM-Eval mock OpenAI-compatible server", docs_url=None, redoc_url=None)

    def unauthorized(request: Request) -> JSONResponse | None:
        if api_key and request.headers.get("authorization") != f"Bearer {api_key}":
            return _error(401, "Invalid or missing API key", "authentication_error")
        return None

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def list_models(request: Request) -> Response:
        if (denied := unauthorized(request)) is not None:
            return denied
        created = int(time.time())
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": m,
                        "object": "model",
                        "created": created,
                        "owned_by": "openllm-eval-mock",
                        "max_model_len": 4096,
                    }
                    for m in models
                ],
            }
        )

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> Response:
        if (denied := unauthorized(request)) is not None:
            return denied
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "Request body must be JSON", "invalid_request_error")
        inputs = body.get("input")
        if isinstance(inputs, str):
            inputs = [inputs]
        if not isinstance(inputs, list) or not all(isinstance(t, str) for t in inputs):
            return _error(
                400, "input must be a string or a list of strings", "invalid_request_error"
            )
        return JSONResponse(
            {
                "object": "list",
                "model": body.get("model"),
                "data": [
                    {"object": "embedding", "index": i, "embedding": _embedding(t)}
                    for i, t in enumerate(inputs)
                ],
                "usage": {"prompt_tokens": sum(len(t.split()) for t in inputs), "total_tokens": 0},
            }
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        if (denied := unauthorized(request)) is not None:
            return denied
        if status := request.headers.get("x-mock-status"):
            if status.isdigit() and 400 <= int(status) <= 599:
                return _error(int(status), f"Mock forced HTTP {status}")
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "Request body must be JSON", "invalid_request_error")
        model = body.get("model")
        if model not in models:
            return _error(404, f"The model '{model}' does not exist", "not_found_error")
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            return _error(400, "messages must be a non-empty list", "invalid_request_error")

        fail_status = request.headers.get("x-mock-fail-status", "503")
        fail_status = int(fail_status) if fail_status.isdigit() else 503
        last_user = _last_user_text(messages)
        needle = request.headers.get("x-mock-fail-if-contains")
        if needle and needle in last_user:
            return _error(fail_status, "Mock failure for this case")
        fail_first = request.headers.get("x-mock-fail-first", "")
        if fail_first.isdigit():
            seen = attempts.get(last_user, 0)
            attempts[last_user] = seen + 1
            if seen < int(fail_first):
                return _error(fail_status, f"Mock transient failure (attempt {seen + 1})")

        mode = request.headers.get("x-mock-mode", "echo")
        task = judge_task(messages)
        text = request.headers.get("x-mock-response") or (
            f"Mock response to: {_last_user_text(messages)[:200]}"
        )
        after_tool = isinstance(messages[-1], dict) and messages[-1].get("role") == "tool"
        tool_calls: list[dict[str, Any]] = []
        entry = (
            None
            if task
            else next((e for e in script if str(e.get("match", "")) in last_user), None)
        )
        if task:
            text = judge_reply(task, messages)
        elif entry is not None:
            if after_tool:
                text = str(
                    entry.get("after_tools")
                    or f"Based on the tool result: {messages[-1].get('content', '')[:200]}"
                )
            elif entry.get("tool_calls"):
                tool_calls = [
                    {
                        "id": f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": c.get("name"),
                            "arguments": c["arguments"]
                            if isinstance(c.get("arguments"), str)
                            else json.dumps(c.get("arguments") or {}),
                        },
                    }
                    for c in entry["tool_calls"]
                ]
            else:
                text = str(entry.get("response", ""))
        elif after_tool:
            text = f"Based on the tool result: {str(messages[-1].get('content', ''))[:200]}"
        elif mode == "tool_call":
            tools = body.get("tools") or []
            name = request.headers.get("x-mock-tool-name") or (
                (tools[0].get("function") or {}).get("name") if tools else "unknown_tool"
            )
            args = request.headers.get("x-mock-tool-args", "{}")
            tool_calls = [
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }
            ]
        tool_call = tool_calls[0] if tool_calls else None
        words = text.split(" ")
        max_tokens = body.get("max_tokens")
        if isinstance(max_tokens, int) and max_tokens > 0:
            words = words[:max_tokens]

        prompt_tokens = _word_count(messages)
        completion_tokens = 0 if tool_call else len(words)
        usage = (
            None
            if mode == "no_usage"
            else {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        )
        completion_id = f"chatcmpl-mock-{uuid.uuid4().hex[:16]}"
        created = int(time.time())
        first_delay = _delay(request, "x-mock-first-token-delay-ms")
        chunk_delay = _delay(request, "x-mock-chunk-delay-ms")
        finish = "tool_calls" if tool_call else "stop"

        if not body.get("stream"):
            await asyncio.sleep(first_delay)
            if mode == "malformed":
                return Response("{not valid json", media_type="application/json")
            message: dict[str, Any] = {
                "role": "assistant",
                "content": None if tool_call else " ".join(words),
            }
            if tool_calls:
                message["tool_calls"] = tool_calls
            payload: dict[str, Any] = {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            }
            if usage:
                payload["usage"] = usage
            return JSONResponse(payload)

        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

        def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> str:
            data = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
            return f"data: {json.dumps(data)}\n\n"

        async def events() -> AsyncIterator[str]:
            # Role-only first event, sent immediately: this is NOT the first token.
            yield chunk({"role": "assistant", "content": ""})
            await asyncio.sleep(first_delay)
            if mode == "malformed":
                yield "data: {not valid json\n\n"
                return
            if tool_calls:
                yield chunk({"tool_calls": [{"index": i, **tc} for i, tc in enumerate(tool_calls)]})
            else:
                for i, word in enumerate(words):
                    if i:
                        await asyncio.sleep(chunk_delay)
                    yield chunk({"content": word if i == 0 else f" {word}"})
                    if mode == "stream_error" and i == 0:
                        failure = {"error": {"message": "Mock mid-stream failure"}}
                        yield f"data: {json.dumps(failure)}\n\n"
                        return
            yield chunk({}, finish)
            if include_usage and usage:
                usage_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [],
                    "usage": usage,
                }
                yield f"data: {json.dumps(usage_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    uvicorn.run(create_mock_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
