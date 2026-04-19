from django.apps import AppConfig


class AgentOnDemandConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "agent_on_demand"
    label = "fairy"

    def ready(self):
        import agent_on_demand.signals  # noqa: F401 — register signal handlers

        from agent_on_demand.observability import init_otel, init_posthog

        init_otel()
        init_posthog()

        from django.db.backends.signals import connection_created

        def _set_sqlite_pragmas(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")

        connection_created.connect(_set_sqlite_pragmas)
