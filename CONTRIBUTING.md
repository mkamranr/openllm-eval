# Contributing to OpenLLM-Eval

Thank you for helping. The project is built milestone by milestone (see the roadmap in the
[README](README.md) and the [CHANGELOG](CHANGELOG.md)). Start with the
[architecture overview](docs/architecture/overview.md) for the parts your change touches.

## Development environment

See [docs/developer-guide/setup.md](docs/developer-guide/setup.md). In short:

```bash
cd backend && uv sync --python 3.12 && uv run pytest
cd frontend && npm ci && npm test
```

## Ground rules

- **Tests go with the change.** Every feature or fix includes tests. The default test suite must
  not need paid or external APIs.
- **Schema changes go through Alembic migrations.** Generate them with
  `uv run alembic revision --autogenerate -m "..."` and review the output.
  `tests/integration/test_migrations.py` fails if the models and migrations drift.
- **No secrets in logs, reports, API responses, or normal DB columns.**
- **No remote assets.** That means no CDNs, remote fonts, analytics, or telemetry. All UI and
  report assets are bundled locally.
- **All model calls go through adapters, and all evaluators implement the evaluator interface**
  (from Milestone 1 and 4 onward).
- **Missing data is `null`, never `0`.**
- **Pin dependencies.** Backend versions are locked in `backend/uv.lock`. Frontend versions are
  exact in `package.json` and locked in `package-lock.json`.

## Before opening a pull request

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd frontend && npm run typecheck && npm run lint && npm test && npm run build
```

Update `CHANGELOG.md` under *Unreleased* and update any docs your change affects.

## Reporting security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).
