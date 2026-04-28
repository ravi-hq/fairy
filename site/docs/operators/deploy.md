# Deploy Guide

This guide covers running your own Agent on Demand instance in production.

## Prerequisites

- Python 3.11 or later
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- A [Sprites](https://sprites.dev) account and API token
- A database (SQLite for development; see note below for production)

!!! note "Database"
    The default configuration uses SQLite (`agent_on_demand.db` in the project root).
    For production deployments with multiple processes or replicas, set
    `DATABASE_URL` to a Postgres DSN (e.g. `postgres://user:pass@host:5432/aod`).
    The setting is parsed by `dj-database-url` and overrides the SQLite default.

## Environment variables

All configuration is passed through environment variables. The full list,
sourced from `src/config/settings.py`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | Yes (prod) | `dev-insecure-key-change-in-prod` | Django secret key for session signing — safe to rotate |
| `FIELD_ENCRYPTION_KEY` | Yes (prod) | Falls back to `DJANGO_SECRET_KEY` | KEK for encrypted `UserSpritesKey` / `UserRuntimeKey` rows — **durable; rotating requires a re-encrypt migration** |
| `DJANGO_DEBUG` | No | `true` | Set to `false` in production |
| `DJANGO_ALLOWED_HOSTS` | No | `*` | Comma-separated list of allowed host headers |
| `DATABASE_URL` | No | SQLite file in project root | Database DSN parsed by `dj-database-url` (e.g. `postgres://user:pass@host:5432/aod`) |
| `SPRITES_BASE_URL` | No | `https://api.sprites.dev` | Override the Sprites API base URL |
| `SPRITE_NAME_PREFIX` | No | `aod` | Prefix applied to all Sprite names created by this instance |
| `DEFAULT_TIMEOUT` | No | `600` | Default session timeout in seconds |

A minimal production `.env`:

```bash
DJANGO_SECRET_KEY=your-long-random-secret-key
FIELD_ENCRYPTION_KEY=your-separate-long-random-key
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=aod.example.com
```

## Installation

```bash
git clone https://github.com/ravi-hq/agent-on-demand
cd agent-on-demand
uv sync --all-extras   # or: pip install -e .
```

## Database migration

Apply all migrations before starting the server:

```bash
uv run python manage.py migrate
```

## Creating the first API token

Agent on Demand uses bearer tokens prefixed with `aod_` for authentication. Create the
first token via the Django shell:

```python
uv run python manage.py shell

# Inside the shell:
from django.contrib.auth.models import User
from agent_on_demand.models import APIKey

user = User.objects.create_user("admin", password=input("Set admin password: "))
_, raw_key = APIKey.create_key(user, "admin-key")
print(raw_key)   # aod_<random> — copy this now, it won't be shown again
```

Pass the token in the `Authorization` header:

```
Authorization: Bearer aod_<your-token>
```

## Running in production

Agent on Demand ships a WSGI entry point at `config.wsgi:application`. Any WSGI-compatible
server works:

```bash
# gunicorn (recommended)
pip install gunicorn
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4

# uvicorn in WSGI mode
pip install uvicorn
uvicorn config.wsgi:application --host 0.0.0.0 --port 8000
```

!!! note
    The `make dev` target (`uv run python manage.py runserver 0.0.0.0:8777`) is
    for local development only — do not use Django's development server in
    production.

## Sprites credentials

Agent on Demand authenticates to the Sprites platform using **per-user** tokens stored
encrypted at rest. Each user brings their own Sprites token and their own model API keys;
there are no shared/service-level credentials. At session creation time, Agent on Demand:

1. Looks up the caller's `UserBackendCredential(backend="sprites")` and decrypts the token.
2. Calls `SpritesClient(token=..., base_url=SPRITES_BASE_URL)` to obtain a client.
3. Creates a Sprite via `client.create_sprite(name)`, where the name is
   `{SPRITE_NAME_PREFIX}-{12-char-hex-id}`.
4. Writes a `run-agent.sh` wrapper script onto the Sprite's filesystem. The script
   exports the **per-user model API key** (e.g. the user's Anthropic key) and runs the
   agent CLI.
5. Model API keys are stored encrypted at rest in `UserCredential` rows and are never
   present in Sprites API calls — only inside the agent's execution environment.

If a user has no Sprites credential configured, session create, multi-turn prompt, and
session termination endpoints return `400 No backend credentials configured`.

To set a user's Sprites token (required before they can run sessions):

```python
from agent_on_demand.models import UserBackendCredential
from django.contrib.auth.models import User

user = User.objects.get(username="alice")
cred, _ = UserBackendCredential.objects.get_or_create(user=user, backend="sprites")
cred.set_token("your-sprites-api-token")
cred.save()
```

To set a user's model API key (required before they can run sessions on a given runtime):

```python
from agent_on_demand.models import UserCredential
from django.contrib.auth.models import User

user = User.objects.get(username="alice")
cred, _ = UserCredential.objects.get_or_create(user=user, kind="provider:anthropic")
cred.set_value("your-anthropic-api-key")
cred.save()
```

The `kind` field maps to the env var written into the session:

| `kind` | Env var written | Used by |
|--------|----------------|---------|
| `provider:anthropic` | `ANTHROPIC_API_KEY` | `claude`, `opencode` |
| `provider:openai` | `OPENAI_API_KEY` | `codex`, `opencode` |
| `provider:google` | `GEMINI_API_KEY` | `gemini`, `opencode` |
| `runtime_token:claude-oauth` | `CLAUDE_CODE_OAUTH_TOKEN` | `claude` (OAuth variant) |

## Health check

```
GET /health → {"status": "ok"}
```

No authentication required. Use this endpoint for load balancer or uptime
monitor checks.
