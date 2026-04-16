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

test-e2e:
	uv run pytest tests/e2e -v

# Same as test-e2e but skips @slow tests (no real sessions spawned).
test-e2e-fast:
	uv run pytest tests/e2e -v -m "not slow"

# Run everything — unit + e2e. E2E auto-skips if FAIRY_API_TOKEN is unset.
test-all:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/

fmt:
	uv run ruff format && ruff check --fix
