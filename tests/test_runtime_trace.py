"""Tests for `runtime_trace.RuntimeTraceEmitter` and the Claude adapter.

Stand up an in-memory OTel tracer provider per test (matching the pattern
in `test_otel_task_tracing.py`) so we can introspect the spans the emitter
creates: name, attributes, parent linkage, lifecycle.
"""

from __future__ import annotations

import json

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_on_demand.session_service.runtime_trace import (
    RuntimeTraceEmitter,
    _claude_adapter,
)


@pytest.fixture
def memory_tracer():
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


class FakeSpan:
    """Captures span events; ignores attribute writes after start."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def add_event(self, name, attributes=None):
        self.events.append((name, dict(attributes or {})))

    def set_attribute(self, key, value):
        pass


def _emitter(parent, runtime="claude", tracer=None):
    if tracer is None:
        tracer = trace.get_tracer("test")
    return RuntimeTraceEmitter(parent, runtime, tracer)


def _line(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Adapter unit tests — pure mapping, no tracer needed
# ---------------------------------------------------------------------------


def test_claude_adapter_assistant_text_event():
    actions = list(
        _claude_adapter(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hello world"}]},
            }
        )
    )
    assert actions == [("event", "claude.assistant_text", {"aod.length": 11})]


def test_claude_adapter_thinking_event():
    actions = list(
        _claude_adapter(
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "thinking": "ponder"}]},
            }
        )
    )
    assert actions == [("event", "claude.thinking", {"aod.length": 6})]


def test_claude_adapter_tool_use_starts_span():
    actions = list(
        _claude_adapter(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_abc",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        }
                    ]
                },
            }
        )
    )
    assert actions == [
        (
            "tool_start",
            "toolu_abc",
            "runtime.tool_use",
            {"aod.tool_name": "Bash", "aod.tool_id": "toolu_abc"},
        )
    ]


def test_claude_adapter_tool_use_without_id_is_skipped():
    """Without a tool_use_id we can't pair start↔end, so don't open a span."""
    actions = list(
        _claude_adapter(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
            }
        )
    )
    assert actions == []


def test_claude_adapter_user_tool_result_ends_span():
    actions = list(
        _claude_adapter(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_abc",
                            "is_error": False,
                        }
                    ]
                },
            }
        )
    )
    assert actions == [("tool_end", "toolu_abc", {"aod.is_error": False})]


def test_claude_adapter_user_tool_result_error_flag():
    actions = list(
        _claude_adapter(
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "x", "is_error": True}]
                },
            }
        )
    )
    assert actions[0][2]["aod.is_error"] is True


def test_claude_adapter_system_init_event():
    actions = list(
        _claude_adapter(
            {
                "type": "system",
                "subtype": "init",
                "model": "claude-sonnet-4-6",
                "tools": list(range(27)),
            }
        )
    )
    assert actions == [
        (
            "event",
            "claude.system.init",
            {
                "aod.subtype": "init",
                "aod.model": "claude-sonnet-4-6",
                "aod.tools_count": 27,
            },
        )
    ]


def test_claude_adapter_result_event_with_usage():
    actions = list(
        _claude_adapter(
            {
                "type": "result",
                "subtype": "success",
                "duration_ms": 12300,
                "num_turns": 15,
                "total_cost_usd": 0.0234,
            }
        )
    )
    assert actions == [
        (
            "event",
            "claude.result",
            {
                "aod.subtype": "success",
                "aod.duration_ms": 12300,
                "aod.num_turns": 15,
                "aod.total_cost_usd": 0.0234,
            },
        )
    ]


def test_claude_adapter_unknown_type_yields_nothing():
    assert list(_claude_adapter({"type": "task_progress"})) == []


# ---------------------------------------------------------------------------
# Emitter — end-to-end with real tracer
# ---------------------------------------------------------------------------


def test_emitter_starts_and_ends_tool_use_span(memory_tracer):
    parent = FakeSpan()
    emitter = _emitter(parent)
    emitter.feed(
        "stdout",
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Bash"},
                    ]
                },
            }
        ),
    )
    # Span has not ended yet — tool_result hasn't arrived.
    assert memory_tracer.get_finished_spans() == ()
    emitter.feed(
        "stdout",
        _line(
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]},
            }
        ),
    )
    spans = memory_tracer.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "runtime.tool_use"
    assert span.attributes["aod.tool_name"] == "Bash"
    assert span.attributes["aod.tool_id"] == "t1"
    assert span.attributes["aod.is_error"] is False


def test_emitter_finish_closes_orphan_tool_spans(memory_tracer):
    """A turn that aborts mid-tool (timeout, runtime crash) leaves a tool_use
    with no matching tool_result. finish() must end those spans so they
    actually export."""
    parent = FakeSpan()
    emitter = _emitter(parent)
    emitter.feed(
        "stdout",
        _line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "orphan", "name": "Read"}]},
            }
        ),
    )
    emitter.finish()
    spans = memory_tracer.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "runtime.tool_use"
    assert spans[0].attributes["aod.tool_status"] == "abandoned"


def test_emitter_buffers_partial_lines(memory_tracer):
    """A JSON object split across two feed() calls only fires after the
    newline arrives — otherwise the parser sees malformed JSON and drops."""
    parent = FakeSpan()
    emitter = _emitter(parent)
    payload = _line(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "split"}]},
        }
    )
    mid = len(payload) // 2
    emitter.feed("stdout", payload[:mid])
    assert parent.events == []
    emitter.feed("stdout", payload[mid:])
    assert parent.events == [("claude.assistant_text", {"aod.length": 5})]


def test_emitter_handles_multiple_lines_in_one_chunk(memory_tracer):
    parent = FakeSpan()
    emitter = _emitter(parent)
    a = _line({"type": "assistant", "message": {"content": [{"type": "text", "text": "a"}]}})
    b = _line({"type": "assistant", "message": {"content": [{"type": "text", "text": "bb"}]}})
    emitter.feed("stdout", a + b)
    assert parent.events == [
        ("claude.assistant_text", {"aod.length": 1}),
        ("claude.assistant_text", {"aod.length": 2}),
    ]


def test_emitter_drops_malformed_json_silently(memory_tracer):
    parent = FakeSpan()
    emitter = _emitter(parent)
    emitter.feed("stdout", b"not-json\n")
    emitter.feed("stdout", b"{also not valid}\n")
    assert parent.events == []
    assert memory_tracer.get_finished_spans() == ()


def test_emitter_ignores_stderr(memory_tracer):
    """stream-json is stdout-only on every supported runtime."""
    parent = FakeSpan()
    emitter = _emitter(parent)
    emitter.feed(
        "stderr",
        _line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "x"}]},
            }
        ),
    )
    assert parent.events == []


def test_emitter_rejects_non_string_runtime(memory_tracer):
    """Pinning the constructor's type guard. PR #286's wiring originally
    passed `spec.runtime` (a Runtime object) instead of `spec.runtime.name`,
    silently producing a no-op adapter. The TypeError makes that regression
    fail at construction instead of in production."""

    class FakeRuntime:
        name = "claude"

    parent = FakeSpan()
    with pytest.raises(TypeError, match="spec.runtime.name"):
        RuntimeTraceEmitter(parent, FakeRuntime(), trace.get_tracer("test"))


def test_emitter_unknown_runtime_is_a_noop(memory_tracer):
    parent = FakeSpan()
    emitter = _emitter(parent, runtime="opencode")  # no adapter wired yet
    emitter.feed(
        "stdout",
        _line({"type": "assistant", "message": {"content": []}}),
    )
    assert parent.events == []
    assert memory_tracer.get_finished_spans() == ()


def test_emitter_duplicate_tool_start_ignored(memory_tracer):
    """A second tool_use with the same id must not replace the first
    (would orphan the original span and lose its duration)."""
    parent = FakeSpan()
    emitter = _emitter(parent)
    block = _line(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "dup", "name": "Bash"}]},
        }
    )
    emitter.feed("stdout", block)
    emitter.feed("stdout", block)  # duplicate — ignored
    emitter.feed(
        "stdout",
        _line(
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "dup"}]},
            }
        ),
    )
    spans = memory_tracer.get_finished_spans()
    assert len(spans) == 1


def test_emitter_unknown_tool_result_silently_ignored(memory_tracer):
    parent = FakeSpan()
    emitter = _emitter(parent)
    emitter.feed(
        "stdout",
        _line(
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "ghost"}]},
            }
        ),
    )
    assert memory_tracer.get_finished_spans() == ()


def test_emitter_adapter_exception_does_not_propagate(memory_tracer, mocker):
    """A bug in an adapter must not abort the drain loop and fail the turn."""
    parent = FakeSpan()
    emitter = _emitter(parent)
    mocker.patch.object(emitter, "_adapter", side_effect=RuntimeError("adapter exploded"))
    emitter.feed(
        "stdout",
        _line({"type": "assistant", "message": {"content": []}}),
    )
    assert parent.events == []


def test_emitter_skips_blank_lines(memory_tracer):
    parent = FakeSpan()
    emitter = _emitter(parent)
    emitter.feed("stdout", b"\n\n\n")
    assert parent.events == []
