import atexit
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
        import agent_on_demand.sprites_patch  # noqa: F401 — sprites-py ws close_timeout fix

        from agent_on_demand.observability import init_otel

        init_otel()

        # posthog.capture() raises ValueError when api_key is unset; use a
        # placeholder + disabled=True so local dev and tests no-op cleanly.
        api_key = os.environ.get("POSTHOG_API_KEY")
        posthog.api_key = api_key or "disabled"
        posthog.host = os.environ.get("POSTHOG_HOST", DEFAULT_POSTHOG_HOST)
        posthog.disabled = not api_key
        # Install sys.excepthook + threading.excepthook so unhandled exceptions
        # outside a request/task context (management commands, signal handlers,
        # startup) still land in PostHog error tracking. The hook is wired when
        # the default client is constructed; posthog.setup() forces that here
        # instead of deferring to the first capture() call.
        posthog.enable_exception_autocapture = bool(api_key)
        if api_key:
            posthog.setup()
        # Render SIGTERMs both the web and worker processes on deploy. Gunicorn
        # and Procrastinate each catch SIGTERM, drain, and exit cleanly — atexit
        # runs on that clean exit and flushes PostHog's async buffer so events
        # from the final requests/tasks aren't dropped.
        if api_key:
            atexit.register(posthog.shutdown)

        from django.db.backends.signals import connection_created

        def _set_sqlite_pragmas(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")

        connection_created.connect(_set_sqlite_pragmas)
