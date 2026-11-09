# Convenience targets (POSIX shell). On Windows, run the underlying commands directly
# or use Git Bash / WSL. See docs/developer-guide/setup.md.

.PHONY: help install backend-install frontend-install test backend-test frontend-test \
        lint fmt migrate api worker ui deploy up up-dev down logs smoke

help:
	@echo "install      Install backend (uv) and frontend (npm) dependencies"
	@echo "test         Run backend and frontend tests"
	@echo "lint         Lint backend (ruff) and frontend (tsc + eslint)"
	@echo "fmt          Format backend code"
	@echo "migrate      Apply database migrations (local backend)"
	@echo "api / worker / ui  Run components locally without Docker"
	@echo "deploy       Install or upgrade (stops older installs, keeps data, smoke test)"
	@echo "up / up-dev  Start the Docker Compose stack (production-like / hot reload)"
	@echo "down         Stop the stack (keeps volumes)"
	@echo "smoke        Run the post-deployment smoke test"

install: backend-install frontend-install
backend-install:
	cd backend && uv sync --python 3.12
frontend-install:
	cd frontend && npm ci

test: backend-test frontend-test
backend-test:
	cd backend && uv run pytest
frontend-test:
	cd frontend && npm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run typecheck && npm run lint

fmt:
	cd backend && uv run ruff check --fix . && uv run ruff format .

migrate:
	cd backend && uv run alembic upgrade head
api:
	cd backend && uv run uvicorn app.main:app --reload
worker:
	cd backend && uv run python -m app.execution.worker
ui:
	cd frontend && npm run dev

deploy:
	bash scripts/deploy.sh
up:
	docker compose up -d --build
up-dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
down:
	docker compose down
logs:
	docker compose logs -f
smoke:
	bash scripts/smoke-test.sh
