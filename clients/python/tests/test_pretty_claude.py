from __future__ import annotations

import json

from aod import StreamEvent
from aod.pretty.claude import ClaudeFormatter


def _event(data: str, *, stream: str = "stdout", kind: str = "output", eid: int = 1) -> StreamEvent:
    return StreamEvent.from_payload({"type": kind, "id": eid, "stream": stream, "data": data})


def test_feed_splits_on_newlines_and_parses_assistant_text():
    fmt = ClaudeFormatter()
    msg = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello world"}]},
        }
    )
    lines = list(fmt.feed(msg + "\n"))
    assert lines == ["✉️  hello world"]


def test_feed_buffers_partial_lines():
    fmt = ClaudeFormatter()
    msg = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "partial"}]},
        }
    )
    assert list(fmt.feed(msg[:10])) == []
    assert list(fmt.feed(msg[10:])) == []
    assert list(fmt.feed("\n")) == ["✉️  partial"]


def test_flush_emits_trailing_unterminated_line():
    fmt = ClaudeFormatter()
    msg = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "dangling"}]},
        }
    )
    list(fmt.feed(msg))  # no trailing \n
    assert list(fmt.flush()) == ["✉️  dangling"]


def test_system_init_line():
    fmt = ClaudeFormatter()
    msg = json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "model": "claude-sonnet-4-5",
            "tools": list(range(27)),
            "mcp_servers": [{"name": "context7"}],
        }
    )
    out = list(fmt.feed(msg + "\n"))
    assert out == ["⚙️  Session init · model=claude-sonnet-4-5, tools=27, mcp=[context7]"]


def test_tool_use_bash():
    fmt = ClaudeFormatter()
    msg = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}]
            },
        }
    )
    out = list(fmt.feed(msg + "\n"))
    assert out == ["🔧 Bash · ls -la"]


def test_result_success():
    fmt = ClaudeFormatter()
    msg = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "duration_ms": 12300,
            "num_turns": 15,
            "total_cost_usd": 0.0234,
        }
    )
    out = list(fmt.feed(msg + "\n"))
    assert out == ["✨ Done · agent 12.3s, 15 turns, tokens $0.0234"]


def test_subagent_output_is_indented():
    fmt = ClaudeFormatter()
    msg = json.dumps(
        {
            "type": "assistant",
            "parent_tool_use_id": "toolu_abc",
            "message": {"content": [{"type": "text", "text": "nested"}]},
        }
    )
    out = list(fmt.feed(msg + "\n"))
    assert out == ["  ✉️  nested"]


def test_user_tool_result_events_are_dropped():
    fmt = ClaudeFormatter()
    msg = json.dumps({"type": "user", "message": {"content": [{"type": "tool_result"}]}})
    assert list(fmt.feed(msg + "\n")) == []


def test_consume_filters_to_output_on_stdout():
    fmt = ClaudeFormatter()
    body = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    )

    # wrong event type: stage
    assert list(fmt.consume(StreamEvent.from_payload({"type": "stage", "id": 1}))) == []
    # wrong stream: stderr
    assert list(fmt.consume(_event(body + "\n", stream="stderr"))) == []
    # happy path
    assert list(fmt.consume(_event(body + "\n"))) == ["✉️  hi"]


def test_consume_across_multiple_events_joins_partial_lines():
    fmt = ClaudeFormatter()
    body = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "split"}]}}
    )
    mid = len(body) // 2
    assert list(fmt.consume(_event(body[:mid]))) == []
    assert list(fmt.consume(_event(body[mid:] + "\n"))) == ["✉️  split"]


def test_malformed_json_line_is_passed_through_unchanged():
    fmt = ClaudeFormatter()
    out = list(fmt.feed("not-json\n"))
    assert out == ["not-json"]
