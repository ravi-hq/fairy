import os

# Default to SQLite when no DATABASE_URL is configured (e.g. CI without Postgres).
# `make test` already exports DATABASE_URL=sqlite:///test.db; this is a safety net
# for direct `pytest` invocations and gate runners that don't set it.
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
