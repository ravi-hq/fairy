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

# Mutation testing for danger-zone modules (auth.py, crypto.py). Asserts that
# every mutant is killed except a small documented set of known-equivalents.
# See scripts/check_mutmut.py for the equivalent-mutant allowlist.
mutation-test:
	rm -rf mutants/
	DATABASE_URL=sqlite:///test.db uv run python -m scripts.check_mutmut

# Render mutants/ into mutants/report.html — per-file/per-function kill-rate
# heatmap plus the unified diff for every surviving mutant. Run after
# `make mutation-test` (or any `mutmut run`).
mutation-report:
	@if [ ! -f mutants/mutmut-cicd-stats.json ]; then \
		echo "No mutmut data — run 'make mutation-test' first."; exit 1; \
	fi
	uv run python -m scripts.mutmut_report


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

# Print the e2e tests that map to the current branch's diff vs main.
# Read-only; doesn't run anything. Use to preview what `test-e2e-scoped` would do.
scope-e2e:
	uv run python -m scripts.scope_e2e

# Run only the e2e tests that map to the current branch's diff vs main.
# Computes scope via scripts/scope_e2e.py, sets E2E_RUNTIMES, invokes pytest.
# Skips silently if no e2e tests are in scope. Requires AOD_API_TOKEN.
test-e2e-scoped:
	@SCOPE=$$(uv run python -m scripts.scope_e2e --format=shell); \
	eval "$$SCOPE"; \
	if [ -z "$$TESTS" ]; then \
		echo "No e2e tests in scope for this change."; \
		exit 0; \
	fi; \
	echo "Running scoped e2e: $$TESTS (runtimes=$$RUNTIMES)"; \
	E2E_RUNTIMES=$$RUNTIMES uv run pytest $$TESTS -v -n $(E2E_WORKERS) --dist loadfile

# Run everything — unit + e2e. E2E auto-skips if AOD_API_TOKEN is unset.
test-all:
	uv run pytest tests/ -v

# Lint migrations introduced on the current branch (since it diverged from main)
# for safety issues like NOT NULL adds on populated tables, column drops/renames,
# and dangerous index changes. Existing migrations on main are grandfathered.
#
# CI sets BASE_SHA explicitly via `git merge-base origin/main HEAD`. Locally,
# `git merge-base main HEAD` works as long as your local main is up-to-date.
BASE_SHA ?= $(shell git merge-base main HEAD 2>/dev/null)
check-migrations:
	@if [ -z "$(BASE_SHA)" ]; then \
		echo "Could not compute BASE_SHA — pass BASE_SHA=<sha> or ensure 'main' branch exists locally"; \
		exit 1; \
	fi
	DATABASE_URL=sqlite:///test.db uv run python manage.py lintmigrations \
		--include-apps fairy --git-commit-id $(BASE_SHA) --project-root-path .

# Snapshot the JSON schemas of all pydantic request models in views/ and
# fail if they drift from docs/request_schemas.json. Catches accidental
# breaking changes (added/removed/renamed fields, type flips). After an
# intentional change, regenerate with `make snapshot-schemas`.
check-schemas:
	DATABASE_URL=sqlite:///test.db uv run python -m scripts.check_request_schemas

snapshot-schemas:
	DATABASE_URL=sqlite:///test.db uv run python -m scripts.check_request_schemas --write

lint:
	uv run ruff check src/ tests/

fmt-check:
	uv run ruff format --check src/ tests/

typecheck:
	uv run mypy

security:
	# CVE-2026-3219: pip itself, no fix version available as of 2026-04-26.
	# Revisit when pip publishes a fix and remove this ignore.
	uv run pip-audit --ignore-vuln CVE-2026-3219
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
