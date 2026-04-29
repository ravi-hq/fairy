"""Orphan-psycopg-span filter for the worker process.

The Procrastinate worker loop generates ~7.3k orphan psycopg CLIENT spans/hour
(poll/heartbeat/LISTEN/abort-poll plus unnamed BEGIN/COMMIT). Each becomes the
root of its own single-span trace in Honeycomb — pure noise. The filter
installed by `_make_orphan_psycopg_filter` drops exactly those spans before
they reach the BatchSpanProcessor; real in-task SQL is parented and unaffected.
"""

from __future__ import annotations

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, TraceFlags

from agent_on_demand.observability import _make_orphan_psycopg_filter


def _build_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Wire SimpleSpanProcessor → InMemorySpanExporter, behind the orphan filter,
    so each test sees exactly the spans the filter let through."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(_make_orphan_psycopg_filter(SimpleSpanProcessor(exporter)))
    return provider, exporter


def test_orphan_psycopg_client_span_is_dropped():
    provider, exporter = _build_provider()
    psycopg_tracer = provider.get_tracer("opentelemetry.instrumentation.psycopg")

    with psycopg_tracer.start_as_current_span("SELECT 1", kind=SpanKind.CLIENT):
        pass

    assert exporter.get_finished_spans() == ()


def test_parented_psycopg_client_span_is_exported():
    provider, exporter = _build_provider()
    app_tracer = provider.get_tracer("agent_on_demand")
    psycopg_tracer = provider.get_tracer("opentelemetry.instrumentation.psycopg")

    with app_tracer.start_as_current_span("execute_turn"):
        with psycopg_tracer.start_as_current_span("SELECT 1", kind=SpanKind.CLIENT):
            pass

    finished = exporter.get_finished_spans()
    names = sorted(span.name for span in finished)
    assert names == ["SELECT 1", "execute_turn"]


def test_orphan_non_psycopg_span_is_exported():
    """Filter targets psycopg only — orphan spans from other instrumentors
    (requests, django, etc.) must pass through unchanged."""
    provider, exporter = _build_provider()
    requests_tracer = provider.get_tracer("opentelemetry.instrumentation.requests")

    with requests_tracer.start_as_current_span("GET https://example.com", kind=SpanKind.CLIENT):
        pass

    finished = exporter.get_finished_spans()
    assert [span.name for span in finished] == ["GET https://example.com"]


def test_psycopg_client_span_with_invalid_parent_is_dropped():
    """Remote propagation can deliver an all-zero/malformed traceparent, which
    extracts to a SpanContext that is not None but `.is_valid == False`. That
    second branch of the orphan check needs its own coverage — `parent is None`
    isn't the only orphan shape."""
    provider, exporter = _build_provider()
    psycopg_tracer = provider.get_tracer("opentelemetry.instrumentation.psycopg")

    invalid_ctx = SpanContext(trace_id=0, span_id=0, is_remote=False, trace_flags=TraceFlags(0))
    invalid_parent = NonRecordingSpan(invalid_ctx)
    token = otel_context.attach(trace.set_span_in_context(invalid_parent))
    try:
        with psycopg_tracer.start_as_current_span("SELECT 1", kind=SpanKind.CLIENT):
            pass
    finally:
        otel_context.detach(token)

    assert exporter.get_finished_spans() == ()


def test_orphan_psycopg_internal_span_is_exported():
    """Filter targets CLIENT spans only — INTERNAL psycopg spans (e.g. wrapper
    spans the instrumentation may emit around connection setup) are not the
    polling-loop noise we're filtering."""
    provider, exporter = _build_provider()
    psycopg_tracer = provider.get_tracer("opentelemetry.instrumentation.psycopg")

    with psycopg_tracer.start_as_current_span("connect", kind=SpanKind.INTERNAL):
        pass

    finished = exporter.get_finished_spans()
    assert [span.name for span in finished] == ["connect"]
