install:
	uv sync --all-extras

dev:
	uv run python manage.py runserver 0.0.0.0:8777

# Unit + integration tests (e2e suite excluded)
test:
	uv run pytest tests/ -v --ignore=tests/e2e

# E2E tests against a running Fairy deployment.
# Required:  FAIRY_API_TOKEN
# Optional:  FAIRY_API_URL (default http://localhost:8777 — matches `make dev`)
#            E2E_RUNTIMES  (default "claude"; comma-separated)
#            E2E_TIMEOUT   (default 180)
FAIRY_API_URL ?= http://localhost:8777
export FAIRY_API_URL

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

# Tool-enforcement matrix — ~11 real sessions across runtimes.
# Requires FAIRY_API_TOKEN. Respects E2E_RUNTIMES to subset runtimes.
test-e2e-tools:
	uv run pytest tests/e2e/test_agent_tools.py -v -m "tool_matrix"

# MCP-enforcement matrix — ~14 real sessions across runtimes.
# Requires FAIRY_API_TOKEN. The /test-mcp endpoint is served by the Fairy
# deployment when DEBUG=True or FAIRY_TESTING=1; set MCP_TEST_URL to override
# with a different test server.
test-e2e-mcp:
	uv run pytest tests/e2e/test_mcp_enforcement.py -v -m "mcp_matrix"

# Run everything — unit + e2e. E2E auto-skips if FAIRY_API_TOKEN is unset.
test-all:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/

fmt:
	uv run ruff format && ruff check --fix
