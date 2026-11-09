# OpenLLM-Eval backend

The FastAPI application, the evaluation worker, and the `openllm-eval` CLI.

To set up a development environment, see [../docs/developer-guide/setup.md](../docs/developer-guide/setup.md).

```bash
uv sync                     # create .venv and install pinned deps from uv.lock
uv run pytest               # run the test suite
uv run alembic upgrade head # apply database migrations
uv run uvicorn app.main:app --reload
```
