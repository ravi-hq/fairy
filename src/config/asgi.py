import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

_django_app = get_asgi_application()

from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware  # noqa: E402

# `/health` is polled every few seconds by Render's health check; excluding it
# here mirrors the DjangoInstrumentor filter in observability.py so the ASGI
# middleware doesn't emit a span for every poll.
application = OpenTelemetryMiddleware(_django_app, excluded_urls="health")
