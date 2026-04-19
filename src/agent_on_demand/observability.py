"""OpenTelemetry → Honeycomb (traces + logs) and PostHog (product events).

Both subsystems are no-ops when their API keys are unset, so unit tests and
local dev without credentials behave identically to today. Honeycomb routes
each `service.name` into its own dataset by default, so the web service emits
to one dataset and the worker to another by setting `OTEL_SERVICE_NAME`.

Event-property safety: callers must pass only counts, lengths, IDs, and other
non-sensitive metadata. Never raw prompt text, env-var values, or repo URLs.
This module does not enforce that — it's the caller's responsibility.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

HONEYCOMB_OTLP_ENDPOINT = "https://api.honeycomb.io"
DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"
TRACER_NAME = "agent_on_demand"

_otel_initialized = False
_posthog_initialized = False


def init_otel(service_name: str | None = None) -> None:
    """Configure the OTel SDK + OTLP/HTTP exporter pointed at Honeycomb.

    Idempotent. No-op if HONEYCOMB_API_KEY is unset. Service name resolution
    order: explicit arg → OTEL_SERVICE_NAME env → "aod-web".
    """
    global _otel_initialized
    if _otel_initialized:
        return
    api_key = os.environ.get("HONEYCOMB_API_KEY")
    if not api_key:
        return

    from opentelemetry import trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resolved = service_name or os.environ.get("OTEL_SERVICE_NAME") or "aod-web"
    resource = Resource.create({"service.name": resolved})
    headers = {"x-honeycomb-team": api_key}

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{HONEYCOMB_OTLP_ENDPOINT}/v1/traces",
                headers=headers,
            )
        )
    )
    trace.set_tracer_provider(tracer_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(
                endpoint=f"{HONEYCOMB_OTLP_ENDPOINT}/v1/logs",
                headers=headers,
            )
        )
    )
    set_logger_provider(logger_provider)
    logging.getLogger().addHandler(
        LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    )

    _instrument_libraries()
    _otel_initialized = True


def _instrument_libraries() -> None:
    """Attach auto-instrumentations. Each is wrapped so a single missing
    optional dep can't take the whole process down."""
    try:
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        DjangoInstrumentor().instrument()
    except Exception:
        logger.warning("OTel Django instrumentation failed", exc_info=True)
    try:
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        PsycopgInstrumentor().instrument(enable_commenter=True)
    except Exception:
        logger.warning("OTel psycopg instrumentation failed", exc_info=True)
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()
    except Exception:
        logger.warning("OTel requests instrumentation failed", exc_info=True)


def get_tracer():
    """Always returns a tracer — no-op tracer when OTel hasn't been initialized."""
    from opentelemetry import trace

    return trace.get_tracer(TRACER_NAME)


def init_posthog() -> None:
    """Configure the PostHog client. Idempotent. No-op if POSTHOG_API_KEY unset."""
    global _posthog_initialized
    if _posthog_initialized:
        return
    api_key = os.environ.get("POSTHOG_API_KEY")
    if not api_key:
        return
    import posthog

    # `posthog.project_api_key` exists as a module attr in v7 but is unused
    # by `setup()`, which still reads `posthog.api_key`. Setting the wrong
    # one makes every `capture()` call raise ValueError at first invocation.
    posthog.api_key = api_key
    posthog.host = os.environ.get("POSTHOG_HOST", DEFAULT_POSTHOG_HOST)

    # Build the singleton client now, not lazily on first capture, so a bad
    # config (missing/invalid key, bad host) fails at process boot instead
    # of silently dropping the first N events.
    try:
        posthog.setup()
    except Exception:
        logger.exception("posthog.setup() failed — events will not be captured")
        return

    _posthog_initialized = True


def distinct_id_for_user(user) -> str:
    """Stable per-user identifier. Hashes user.id so PostHog never sees the
    raw db PK."""
    return hashlib.sha256(f"aod-user:{user.id}".encode()).hexdigest()[:32]


def track(
    event: str,
    user=None,
    properties: dict[str, Any] | None = None,
) -> None:
    """Capture a product-analytics event. No-op when PostHog isn't configured.

    Properties MUST contain only non-sensitive metadata (counts, lengths, IDs,
    runtime, model, status). Callers are responsible — see module docstring.
    """
    if not _posthog_initialized:
        return
    import posthog

    distinct = distinct_id_for_user(user) if user is not None else "aod-system"
    try:
        # posthog-python 7.x: capture(event, **kwargs) — distinct_id is a kwarg.
        # The old `capture(distinct, event, props)` positional form raises
        # TypeError because **kwargs doesn't accept positional args.
        posthog.capture(event, distinct_id=distinct, properties=properties or {})
    except Exception:
        # Surface at error so a bad signature/config is visible in logs;
        # we still swallow so a PostHog outage doesn't take the API down.
        logger.exception("posthog.capture failed for event=%s", event)
