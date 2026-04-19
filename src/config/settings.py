import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key-change-in-prod")

# Dedicated KEK for encrypted model fields (UserSpritesKey, UserRuntimeKey). Split
# from SECRET_KEY so session-signing keys can be rotated independently of the
# field-encryption key. Rotating this key still requires a re-encrypt migration.
FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY")

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "procrastinate.contrib.django",
    "agent_on_demand.apps.AgentOnDemandConfig",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "posthog.integrations.django.PosthogContextMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

ROOT_URLCONF = "config.urls"

WSGI_APPLICATION = "config.wsgi.application"

APPEND_SLASH = False

CORS_ALLOW_ALL_ORIGINS = True

# Local default points at the Postgres container in docker-compose.yml
# (`make db-up`). Override via DATABASE_URL for production (Render injects it)
# or to point at another DB. Procrastinate requires Postgres; SQLite fallback
# is only useful for running the Django test suite without Postgres running.
DATABASES = {
    "default": dj_database_url.config(
        default="postgres://agent_on_demand:agent_on_demand@localhost:5460/agent_on_demand",
        conn_max_age=600,
    )
}

# Procrastinate requires Postgres — skip its migrations on non-Postgres
# backends so `manage.py migrate` and the SQLite-backed test suite don't
# blow up on CREATE EXTENSION statements. Unit tests stub `execute_turn.defer`
# directly and don't need the Procrastinate schema.
if "postgresql" not in DATABASES["default"].get("ENGINE", ""):
    MIGRATION_MODULES = {"procrastinate": None}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# UI (session-cookie auth; API uses bearer tokens and is @csrf_exempt)
LOGIN_URL = "/ui/login"
LOGIN_REDIRECT_URL = "/ui/"
LOGOUT_REDIRECT_URL = "/ui/login"

# Sprites config
SPRITES_BASE_URL = os.environ.get("SPRITES_BASE_URL", "https://api.sprites.dev")
SPRITE_NAME_PREFIX = os.environ.get("SPRITE_NAME_PREFIX", "aod")
DEFAULT_TIMEOUT = int(os.environ.get("DEFAULT_TIMEOUT", "600"))
