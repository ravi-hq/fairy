"""OpenTelemetry → Honeycomb (traces + logs).

No-op when HONEYCOMB_API_KEY is unset, so unit tests and local dev without
credentials behave identically to today. Honeycomb routes each `service.name`
into its own dataset by default, so the web service emits to one dataset and
the worker to another by setting `OTEL_SERVICE_NAME`.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

logger = logging.getLogger(__name__)

HONEYCOMB_OTLP_ENDPOINT = "https://api.honeycomb.io"
TRACER_NAME = "agent_on_demand"
PSYCOPG_INSTRUMENTATION_SCOPE = "opentelemetry.instrumentation.psycopg"

_otel_initialized = False


def _make_orphan_psycopg_filter(wrapped: SpanProcessor) -> SpanProcessor:
    """Wrap `wrapped` so orphan psycopg CLIENT spans are dropped before export.

    The Procrastinate worker loop (poll / heartbeat / LISTEN / abort-poll, plus
    the unnamed BEGIN/COMMIT statements that surface as `name="<dbname>_xxxx"`)
    runs outside any task span. Auto-instrumented psycopg turns each statement
    into a single-span trace, which floods Honeycomb (~7.3k orphan spans/hour)
    while carrying no debugging signal. Real in-task SQL is parented by the
    per-task span set up around `execute_turn`, so anything still parentless
    at this point is polling we don't want.
    """
    from opentelemetry.sdk.trace import SpanProcessor
    from opentelemetry.trace import SpanKind

    class _OrphanPsycopgFilter(SpanProcessor):
        def on_start(self, span: Span, parent_context: Context | None = None) -> None:
            wrapped.on_start(span, parent_context)

        def on_end(self, span: ReadableSpan) -> None:
            if _is_orphan_psycopg_client_span(span):
                return
            wrapped.on_end(span)

        def shutdown(self) -> None:
            wrapped.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return wrapped.force_flush(timeout_millis)

    def _is_orphan_psycopg_client_span(span: ReadableSpan) -> bool:
        if span.kind != SpanKind.CLIENT:
            return False
        scope = span.instrumentation_scope
        if scope is None or scope.name != PSYCOPG_INSTRUMENTATION_SCOPE:
            return False
        parent = span.parent
        return parent is None or not parent.is_valid

    return _OrphanPsycopgFilter()


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
    batch_processor = BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint=f"{HONEYCOMB_OTLP_ENDPOINT}/v1/traces",
            headers=headers,
        )
    )
    tracer_provider.add_span_processor(_make_orphan_psycopg_filter(batch_processor))
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
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))
    # Attaching the OTel handler disables Python's implicit stderr fallback,
    # so nothing from app loggers reaches platform log collectors (Render, etc.)
    # unless we put a StreamHandler back ourselves.
    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, LoggingHandler)
        for h in root.handlers
    ):
        stream = logging.StreamHandler()
        stream.setLevel(logging.INFO)
        stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(stream)

    _instrument_libraries()
    _otel_initialized = True


def _instrument_libraries() -> None:
    """Attach auto-instrumentations. Each is wrapped so a single missing
    optional dep can't take the whole process down."""
    try:
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        # `/health` is polled every few seconds by Render's health check; without
        # this filter it dominates Honeycomb traffic and crowds out real spans.
        DjangoInstrumentor().instrument(excluded_urls="health")
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
