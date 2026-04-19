install:
	uv sync --all-extras

dev:
	uv run python manage.py runserver 0.0.0.0:8777

# Procrastinate worker. Session execution runs here; `make dev` only handles HTTP.
# Requires Postgres (Procrastinate does not support SQLite).
worker:
	uv run procrastinate --app=agent_on_demand.session_service.tasks.procrastinate_app worker

# Unit + integration tests (e2e suite excluded)
test:
	uv run pytest tests/ -v --ignore=tests/e2e

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

fmt:
	uv run ruff format && ruff check --fix
