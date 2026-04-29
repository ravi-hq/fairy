from __future__ import annotations

from aod import StreamEvent
from aod.pretty import ClaudeFormatter, Formatter, GenericFormatter, formatter_for


def _output(data: str, *, stream: str = "stdout") -> StreamEvent:
    return StreamEvent.from_payload({"type": "output", "id": 1, "stream": stream, "data": data})


def test_formatter_for_claude_returns_claude_formatter():
    assert isinstance(formatter_for("claude"), ClaudeFormatter)
    assert isinstance(formatter_for("claude-code"), ClaudeFormatter)
    assert isinstance(formatter_for("Claude-Code"), ClaudeFormatter)


def test_formatter_for_unknown_returns_generic():
    assert isinstance(formatter_for("codex"), GenericFormatter)
    assert isinstance(formatter_for("gemini"), GenericFormatter)
    assert isinstance(formatter_for("opencode"), GenericFormatter)
    assert isinstance(formatter_for(""), GenericFormatter)


def test_generic_formatter_yields_stdout_lines():
    fmt = GenericFormatter()
    lines = list(fmt.consume(_output("first line\nsecond line\n")))
    assert lines == ["first line", "second line"]


def test_generic_formatter_buffers_partial_lines():
    fmt = GenericFormatter()
    assert list(fmt.consume(_output("partial"))) == []
    assert list(fmt.consume(_output(" continued\n"))) == ["partial continued"]


def test_generic_formatter_flush_emits_unterminated():
    fmt = GenericFormatter()
    list(fmt.consume(_output("dangling")))
    assert list(fmt.flush()) == ["dangling"]


def test_generic_formatter_skips_stderr():
    fmt = GenericFormatter()
    assert list(fmt.consume(_output("err\n", stream="stderr"))) == []


def test_generic_formatter_skips_non_output_events():
    fmt = GenericFormatter()
    start = StreamEvent.from_payload({"type": "start", "id": None, "session_id": "s"})
    assert list(fmt.consume(start)) == []


def test_formatter_protocol_satisfied_by_both():
    """Both shipping formatters duck-type as Formatter."""
    assert isinstance(ClaudeFormatter(), Formatter)
    assert isinstance(GenericFormatter(), Formatter)
