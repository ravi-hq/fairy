# Deploy Guide

This guide covers running your own fairy instance in production.

## Prerequisites

- Python 3.11 or later
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- A [Sprites](https://sprites.dev) account and API token
- A database (SQLite for development; see note below for production)

!!! note "Database"
    The default configuration uses SQLite (`fairy.db` in the project root). For
    production deployments with multiple processes or replicas, replace the
    `DATABASES` setting with a Postgres DSN via Django's standard
    `DATABASE_URL` / `dj-database-url` pattern.

## Environment variables

All configuration is passed through environment variables. The full list,
sourced from `src/config/settings.py`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | Yes (prod) | `dev-insecure-key-change-in-prod` | Django secret key — **change this in production** |
| `DJANGO_DEBUG` | No | `true` | Set to `false` in production |
| `DJANGO_ALLOWED_HOSTS` | No | `*` | Comma-separated list of allowed host headers |
| `SPRITES_TOKEN` | Yes | _(empty)_ | Sprites API token — fairy uses this to create and manage Sprites |
| `SPRITES_BASE_URL` | No | `https://api.sprites.dev` | Override the Sprites API base URL |
| `SPRITE_NAME_PREFIX` | No | `fairy` | Prefix applied to all Sprite names created by this instance |
| `DEFAULT_TIMEOUT` | No | `600` | Default session timeout in seconds |

A minimal production `.env`:

```bash
DJANGO_SECRET_KEY=your-long-random-secret-key
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=fairy.example.com
SPRITES_TOKEN=your-sprites-api-token
```

## Installation

```bash
git clone https://github.com/ravi-hq/fairy
cd fairy
uv sync --all-extras   # or: pip install -e .
```

## Database migration

Apply all migrations before starting the server:

```bash
uv run python manage.py migrate
```

## Creating the first API token

Fairy uses bearer tokens prefixed with `fairy_` for authentication. Create the
first token via the Django shell:

```python
uv run python manage.py shell

# Inside the shell:
from django.contrib.auth.models import User
from fairy.models import APIKey

user = User.objects.create_user("admin", password=input("Set admin password: "))
_, raw_key = APIKey.create_key(user, "admin-key")
print(raw_key)   # fairy_<random> — copy this now, it won't be shown again
```

Pass the token in the `Authorization` header:

```
Authorization: Bearer fairy_<your-token>
```

## Running in production

Fairy ships a WSGI entry point at `config.wsgi:application`. Any WSGI-compatible
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

Fairy authenticates to the Sprites platform using a single service-level token
set in `SPRITES_TOKEN`. At session creation time, fairy:

1. Calls `SpritesClient(token=SPRITES_TOKEN, base_url=SPRITES_BASE_URL)` to
   obtain a client.
2. Creates a Sprite via `client.create_sprite(name)`, where the name is
   `{SPRITE_NAME_PREFIX}-{12-char-hex-id}`.
3. Writes a `run-agent.sh` wrapper script onto the Sprite's filesystem. The
   script exports the **per-user runtime API key** (e.g. the user's Anthropic
   key) and runs the agent binary.
4. The runtime API key is stored encrypted at rest in the `UserRuntimeKey`
   table and is never present in Sprites API calls — only inside the agent's
   execution environment.

To set a user's runtime key (required before they can run sessions):

```python
from fairy.models import UserRuntimeKey
from django.contrib.auth.models import User

user = User.objects.get(username="alice")
urk, _ = UserRuntimeKey.objects.get_or_create(user=user, runtime="claude")
urk.set_api_key("your-anthropic-api-key")
urk.save()
```

## Health check

```
GET /health → {"status": "ok"}
```

No authentication required. Use this endpoint for load balancer or uptime
monitor checks.
