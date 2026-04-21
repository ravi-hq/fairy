import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

_django_app = get_asgi_application()

from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware  # noqa: E402

application = OpenTelemetryMiddleware(_django_app)
