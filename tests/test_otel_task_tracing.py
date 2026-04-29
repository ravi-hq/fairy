"""Tests for the `traced_task` decorator and `inject_carrier` helper.

These tests stand up an in-memory OTel tracer provider so we can introspect
the spans that the decorator produces — name, kind, attributes, parent
linkage, and error status.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from agent_on_demand.session_service.tracing import inject_carrier, traced_task


@pytest.fixture
def memory_tracer():
    """Replace the global tracer provider with an in-memory one for the test.

    OTel guards `set_tracer_provider` with a `Once`; we reset it both before
    and after so the swap takes effect and so the next test (or production
    code path) can re-set the provider.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "aod-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER_SET_ONCE._done = False
    trace._TRACER_PROVIDER = None
    trace.set_tracer_provider(provider)
    try:
        yield exporter
    finally:
        provider.shutdown()
        trace._TRACER_PROVIDER_SET_ONCE._done = False
        trace._TRACER_PROVIDER = None
        trace.set_tracer_provider(previous)


def test_decorator_creates_consumer_span_with_task_name(memory_tracer):
    @traced_task("provision_session")
    def my_task(*, session_id: str) -> str:
        return session_id

    result = my_task(session_id="sess-1")
    assert result == "sess-1"

    spans = memory_tracer.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "task provision_session"
    assert span.kind == SpanKind.CONSUMER


def test_decorator_sets_attributes_from_kwargs(memory_tracer):
    @traced_task("execute_turn")
    def my_task(*, session_id: str, turn_id: int, mode: str) -> None:
        return None

    my_task(session_id="sess-2", turn_id=42, mode="run")

    span = memory_tracer.get_finished_spans()[0]
    assert span.attributes["procrastinate.task.name"] == "execute_turn"
    assert span.attributes["aod.session_id"] == "sess-2"
    assert span.attributes["aod.turn_id"] == 42
    # Kwargs not in the attribute map shouldn't appear.
    assert "aod.mode" not in span.attributes


def test_decorator_sets_user_id_and_handle_when_present(memory_tracer):
    @traced_task("destroy_session")
    def my_task(*, user_id: int, handle: str) -> None:
        return None

    my_task(user_id=7, handle="sprite-abc")

    span = memory_tracer.get_finished_spans()[0]
    assert span.attributes["aod.user_id"] == 7
    assert span.attributes["aod.handle"] == "sprite-abc"


def test_decorator_strips_otel_carrier_before_calling_wrapped(memory_tracer):
    seen_kwargs: dict = {}

    @traced_task("execute_turn")
    def my_task(**kwargs) -> None:
        seen_kwargs.update(kwargs)

    my_task(session_id="s", _otel_carrier={"traceparent": "ignored"})

    assert "_otel_carrier" not in seen_kwargs
    assert seen_kwargs == {"session_id": "s"}


def test_decorator_attaches_span_to_carrier_parent(memory_tracer):
    """When a carrier is provided, the resulting span's parent.trace_id
    must match the carrier's trace context — that's what stitches the worker
    trace to the originating request."""
    tracer = trace.get_tracer("test")
    carrier: dict[str, str] = {}
    with tracer.start_as_current_span("parent") as parent:
        parent_trace_id = parent.get_span_context().trace_id
        TraceContextTextMapPropagator().inject(carrier)

    @traced_task("execute_turn")
    def my_task(*, session_id: str) -> None:
        return None

    my_task(session_id="s", _otel_carrier=carrier)

    task_spans = [s for s in memory_tracer.get_finished_spans() if s.name == "task execute_turn"]
    assert len(task_spans) == 1
    task_span = task_spans[0]
    assert task_span.parent is not None
    assert task_span.parent.trace_id == parent_trace_id


def test_decorator_records_exception_and_sets_error_status(memory_tracer):
    @traced_task("execute_turn")
    def boom(*, session_id: str) -> None:
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        boom(session_id="s")

    span = memory_tracer.get_finished_spans()[0]
    assert span.status.status_code == StatusCode.ERROR
    # `record_exception` adds an event with the exception type.
    event_names = [e.name for e in span.events]
    assert "exception" in event_names


def test_inject_carrier_returns_traceparent_inside_span(memory_tracer):
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("parent"):
        carrier = inject_carrier()
    assert "traceparent" in carrier


def test_inject_carrier_returns_empty_outside_span(memory_tracer):
    carrier = inject_carrier()
    assert carrier == {}
