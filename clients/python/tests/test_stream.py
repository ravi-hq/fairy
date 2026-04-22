from __future__ import annotations

import httpx
import pytest

from aod import NotFoundError, StreamEvent
from aod.stream import _parse_data_line, iter_sse


def test_parse_data_line_extracts_type_id_and_extras():
    event = _parse_data_line('data: {"type":"output","id":7,"stream":"stdout","data":"hi"}')
    assert event is not None
    assert event.type == "output"
    assert event.id == 7
    assert event.extra == {"stream": "stdout", "data": "hi"}


def test_parse_data_line_ignores_non_data_lines():
    assert _parse_data_line("id: 12") is None
    assert _parse_data_line(": heartbeat") is None
    assert _parse_data_line("") is None
    assert _parse_data_line("data: ") is None


def test_parse_data_line_skips_malformed_json():
    assert _parse_data_line("data: {not json") is None


def test_iter_sse_skips_heartbeats_and_id_lines():
    body = (
        b'data: {"type":"start","session_id":"abc"}\n\n'
        b"id: 1\n"
        b": heartbeat\n\n"
        b'data: {"type":"output","id":1,"stream":"stdout","data":"hello"}\n\n'
        b'data: {"type":"exit","id":2,"code":0}\n\n'
    )
    response = httpx.Response(
        200,
        content=body,
        headers={"content-type": "text/event-stream"},
        request=httpx.Request("GET", "http://x/sessions/abc/stream"),
    )
    events = list(iter_sse(response))
    assert [e.type for e in events] == ["start", "output", "exit"]
    assert all(isinstance(e, StreamEvent) for e in events)
    assert events[2].extra == {"code": 0}


def test_iter_sse_maps_error_status():
    response = httpx.Response(
        404,
        json={"detail": "Session not found"},
        request=httpx.Request("GET", "http://x/sessions/abc/stream"),
    )
    with pytest.raises(NotFoundError):
        list(iter_sse(response))
