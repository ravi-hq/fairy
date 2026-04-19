import os

import posthog
from django.apps import AppConfig


DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"


class AgentOnDemandConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "agent_on_demand"
    label = "fairy"

    def ready(self):
        import agent_on_demand.signals  # noqa: F401 — register signal handlers

        from agent_on_demand.observability import init_otel

        init_otel()

        # posthog.capture() raises ValueError when api_key is unset; use a
        # placeholder + disabled=True so local dev and tests no-op cleanly.
        api_key = os.environ.get("POSTHOG_API_KEY")
        posthog.api_key = api_key or "disabled"
        posthog.host = os.environ.get("POSTHOG_HOST", DEFAULT_POSTHOG_HOST)
        posthog.disabled = not api_key

        from django.db.backends.signals import connection_created

        def _set_sqlite_pragmas(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")

        connection_created.connect(_set_sqlite_pragmas)
