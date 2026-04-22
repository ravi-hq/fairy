install:
	uv sync --all-extras

# Start the local Postgres container (docker-compose.yml). Blocks until ready.
db-up:
	docker compose up -d db
	@until docker compose exec -T db pg_isready -U agent_on_demand >/dev/null 2>&1; do \
		echo "waiting for postgres..."; sleep 1; \
	done
	uv run python manage.py migrate

db-down:
	docker compose down

# Reset local dev DB — destroys the data volume, recreates the container, migrates.
db-reset:
	docker compose down -v
	$(MAKE) db-up

dev:
	PYTHONPATH=src uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8777 --reload

# Procrastinate worker. Session execution runs here; `make dev` only handles HTTP.
# Requires the Postgres container to be up (`make db-up`).
worker:
	uv run python manage.py procrastinate worker

# Unit + integration tests (e2e suite excluded). Tests run against SQLite so
# Postgres doesn't need to be running; Procrastinate migrations are skipped
# on non-Postgres backends (see config/settings.py).
test:
	DATABASE_URL=sqlite:///test.db uv run pytest tests/ -v --ignore=tests/e2e

# E2E tests against a running agent-on-demand deployment.
# Required:  AOD_API_TOKEN
# Optional:  AOD_API_URL (default http://localhost:8777 — matches `make dev`)
#            E2E_RUNTIMES  (default "claude"; comma-separated)
#            E2E_TIMEOUT   (default 180)
AOD_API_URL ?= http://localhost:8777
export AOD_API_URL

# E2E_RUNTIMES ?= "claude,codex,gemini,claude-oauth"
# export E2E_RUNTIMES

# Parallelism for e2e — `loadfile` keeps tests in the same file on the same
# worker so per-runtime fixtures aren't recreated across workers. Override with
# E2E_WORKERS=1 for serial debugging.
E2E_WORKERS ?= auto

test-e2e:
	uv run pytest tests/e2e -v -n $(E2E_WORKERS) --dist loadfile

# Same as test-e2e but skips @slow tests (no real sessions spawned).
test-e2e-fast:
	uv run pytest tests/e2e -v -n $(E2E_WORKERS) --dist loadfile -m "not slow"

# Just the skills-materialization e2e tests (spawns one real session per runtime).
test-e2e-skills:
	uv run pytest tests/e2e/test_skills.py -v -n $(E2E_WORKERS) --dist loadfile

# Just the network-isolation e2e enforcement tests. Claude runtime only.
test-e2e-networking:
	uv run pytest tests/e2e/test_environments.py -v -k "limited_networking_blocks or limited_networking_allows"

# Just the MCP e2e tests. Spawns a real session with a stdio
# @modelcontextprotocol/server-everything MCP server attached.
test-e2e-mcp:
	uv run pytest tests/e2e/test_mcp.py -v -m "mcp_matrix"

# Run everything — unit + e2e. E2E auto-skips if AOD_API_TOKEN is unset.
test-all:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/

fmt-check:
	uv run ruff format --check src/ tests/

typecheck:
	uv run mypy

security:
	uv run pip-audit
	uv run bandit -r src/ -ll

fmt:
	uv run ruff format && ruff check --fix

# --- Python SDK (clients/python) ---------------------------------------------

SDK_DIR := clients/python

sdk-install:
	cd $(SDK_DIR) && uv pip install -e ".[dev]"

test-sdk:
	cd $(SDK_DIR) && uv run pytest -q

lint-sdk:
	cd $(SDK_DIR) && uv run ruff check src tests && uv run ruff format --check src tests

# Verify the SDK covers every endpoint in docs/openapi.yaml.
check-sdk-parity:
	uv run python scripts/check_sdk_parity.py
