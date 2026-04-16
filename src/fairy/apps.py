from django.apps import AppConfig


class FairyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fairy"

    def ready(self):
        from django.db.backends.signals import connection_created

        def _set_sqlite_pragmas(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")

        connection_created.connect(_set_sqlite_pragmas)
