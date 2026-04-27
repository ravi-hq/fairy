"""Direct unit tests for the SSE event formatters in `stream_format`.

Mutation-tested. Each test pins one mutation-killable property of the
SSE wire format:

  - ``format_event`` always emits ``type`` and ``id`` at the top level
    alongside the payload keys; SDKs parse these names verbatim.
  - ``format_chunk_events`` emits the ``stage``-only, ``turn_start +
    output``, and ``output``-only branches with the exact event types
    and payload keys; the ``last_turn_id`` is advanced if and only if
    a ``turn_start`` was emitted.
  - ``format_terminal_event`` produces ``terminated``/``error``/``exit``
    for the three terminal status combinations and returns ``None`` for
    non-terminal statuses; the event-type strings are pinned exactly
    so wrap-mutants like ``"XXterminatedXX"`` are caught.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them.
"""

from __future__ import annotations

import json

from agent_on_demand.stream_format import (
    format_chunk_events,
    format_event,
    format_terminal_event,
)


def _output_chunk(
    *,
    log_id: int = 7,
    stream: str = "stdout",
    data: str = "hi",
    turn_id: int | None = 42,
    turn_number: int | None = 1,
) -> dict:
    return {
        "id": log_id,
        "kind": "output",
        "stream": stream,
        "data": data,
        "stage": None,
        "state": None,
        "duration_ms": None,
        "turn_id": turn_id,
        "turn__turn_number": turn_number,
    }


def _stage_chunk(
    *,
    log_id: int = 3,
    stage: str = "create_sprite",
    state: str = "started",
    duration_ms: int | None = None,
    data: str = "",
) -> dict:
    return {
        "id": log_id,
        "kind": "stage",
        "stream": None,
        "data": data,
        "stage": stage,
        "state": state,
        "duration_ms": duration_ms,
        "turn_id": None,
        "turn__turn_number": None,
    }


# ---------- format_event ----------


def test_format_event_emits_type_id_and_payload_fields():
    """Top-level keys are exactly ``type``, ``id``, and the payload's
    own keys — pin so a mutant that drops or renames either built-in
    field has nothing to hide behind."""
    raw = format_event("output", 11, {"stream": "stdout", "data": "hi"})
    parsed = json.loads(raw)
    assert parsed == {"type": "output", "id": 11, "stream": "stdout", "data": "hi"}


def test_format_event_with_empty_payload_still_carries_type_and_id():
    raw = format_event("exit", 99, {})
    parsed = json.loads(raw)
    assert parsed == {"type": "exit", "id": 99}


def test_format_event_payload_can_override_nothing_when_keys_distinct():
    """A payload key that doesn't collide with ``type``/``id`` lands
    verbatim. Pins that the merge order is payload-after-builtins (a
    swap would let payload silently overwrite ``type``/``id``)."""
    raw = format_event("stage", 1, {"stage": "create_sprite", "state": "done"})
    parsed = json.loads(raw)
    assert parsed["type"] == "stage"
    assert parsed["id"] == 1
    assert parsed["stage"] == "create_sprite"
    assert parsed["state"] == "done"


# ---------- format_chunk_events: stage rows ----------


def test_stage_started_emits_one_stage_event_without_duration_or_message():
    chunk = _stage_chunk(state="started")
    events, new_turn_id = format_chunk_events(chunk, last_turn_id=None)
    assert len(events) == 1
    parsed = json.loads(events[0])
    assert parsed == {
        "type": "stage",
        "id": chunk["id"],
        "stage": "create_sprite",
        "state": "started",
    }
    assert new_turn_id is None


def test_stage_done_includes_duration_ms_when_set():
    chunk = _stage_chunk(state="done", duration_ms=12000)
    events, _ = format_chunk_events(chunk, last_turn_id=None)
    parsed = json.loads(events[0])
    assert parsed["duration_ms"] == 12000
    assert parsed["state"] == "done"
    assert "message" not in parsed


def test_stage_done_omits_duration_when_none():
    chunk = _stage_chunk(state="done", duration_ms=None)
    events, _ = format_chunk_events(chunk, last_turn_id=None)
    parsed = json.loads(events[0])
    assert "duration_ms" not in parsed


def test_stage_failed_with_data_includes_message():
    chunk = _stage_chunk(state="failed", duration_ms=500, data="permission denied")
    events, _ = format_chunk_events(chunk, last_turn_id=None)
    parsed = json.loads(events[0])
    assert parsed["state"] == "failed"
    assert parsed["duration_ms"] == 500
    assert parsed["message"] == "permission denied"


def test_stage_failed_with_empty_data_omits_message():
    """``data=""`` is falsy — the message branch must guard on truthiness,
    not just on ``state == "failed"``."""
    chunk = _stage_chunk(state="failed", data="")
    events, _ = format_chunk_events(chunk, last_turn_id=None)
    parsed = json.loads(events[0])
    assert "message" not in parsed


def test_stage_started_with_data_does_not_attach_message():
    """The message branch fires only on ``state == "failed"`` — pin so
    a mutant that drops the state guard is caught."""
    chunk = _stage_chunk(state="started", data="not-a-failure-message")
    events, _ = format_chunk_events(chunk, last_turn_id=None)
    parsed = json.loads(events[0])
    assert "message" not in parsed


def test_stage_event_does_not_change_last_turn_id():
    chunk = _stage_chunk(state="done", duration_ms=10)
    _, new_turn_id = format_chunk_events(chunk, last_turn_id=99)
    assert new_turn_id == 99


# ---------- format_chunk_events: output rows ----------


def test_first_output_emits_turn_start_then_output_in_that_order():
    chunk = _output_chunk(log_id=10, turn_id=42, turn_number=1)
    events, new_turn_id = format_chunk_events(chunk, last_turn_id=None)
    assert len(events) == 2
    first = json.loads(events[0])
    second = json.loads(events[1])
    assert first["type"] == "turn_start"
    assert second["type"] == "output"
    assert new_turn_id == 42


def test_turn_start_event_carries_turn_number():
    chunk = _output_chunk(turn_id=42, turn_number=3)
    events, _ = format_chunk_events(chunk, last_turn_id=None)
    parsed = json.loads(events[0])
    assert parsed["turn"] == 3
    assert parsed["id"] == chunk["id"]


def test_output_event_payload_carries_stream_data_and_turn():
    chunk = _output_chunk(stream="stderr", data="oops", turn_id=42, turn_number=2)
    events, _ = format_chunk_events(chunk, last_turn_id=None)
    output = json.loads(events[-1])
    assert output["type"] == "output"
    assert output["stream"] == "stderr"
    assert output["data"] == "oops"
    assert output["turn"] == 2


def test_output_with_same_turn_id_emits_only_output_no_turn_start():
    chunk = _output_chunk(turn_id=42, turn_number=1)
    events, new_turn_id = format_chunk_events(chunk, last_turn_id=42)
    assert len(events) == 1
    parsed = json.loads(events[0])
    assert parsed["type"] == "output"
    assert new_turn_id == 42


def test_output_with_none_turn_id_emits_only_output():
    """Some early stage rows arrive without a turn association; the
    ``turn_id is None`` guard must short-circuit the ``turn_start`` emit."""
    chunk = _output_chunk(turn_id=None, turn_number=None)
    events, new_turn_id = format_chunk_events(chunk, last_turn_id=None)
    assert len(events) == 1
    parsed = json.loads(events[0])
    assert parsed["type"] == "output"
    assert new_turn_id is None


def test_output_with_none_turn_id_preserves_existing_last_turn_id():
    chunk = _output_chunk(turn_id=None, turn_number=None)
    _, new_turn_id = format_chunk_events(chunk, last_turn_id=7)
    assert new_turn_id == 7


def test_output_advancing_to_new_turn_id_emits_turn_start():
    """Transitioning from one turn to a different non-None turn must
    emit a fresh ``turn_start`` and update the cursor."""
    chunk = _output_chunk(turn_id=99, turn_number=4)
    events, new_turn_id = format_chunk_events(chunk, last_turn_id=42)
    assert len(events) == 2
    assert json.loads(events[0])["type"] == "turn_start"
    assert json.loads(events[0])["turn"] == 4
    assert json.loads(events[1])["type"] == "output"
    assert new_turn_id == 99


def test_output_event_id_matches_chunk_id():
    chunk = _output_chunk(log_id=555, turn_id=1, turn_number=1)
    events, _ = format_chunk_events(chunk, last_turn_id=1)
    assert json.loads(events[0])["id"] == 555


# ---------- format_terminal_event ----------


def test_terminated_status_returns_terminated_event():
    raw = format_terminal_event("terminated", None, last_id=12)
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed["type"] == "terminated"
    assert parsed["id"] == 12
    assert parsed["message"] == "Session terminated"


def test_terminated_status_ignores_exit_code():
    """Even if ``exit_code`` is set, ``terminated`` must win — pin so a
    mutant that re-routes through the ``exit``/``error`` branches is
    caught."""
    raw = format_terminal_event("terminated", 0, last_id=1)
    parsed = json.loads(raw)
    assert parsed["type"] == "terminated"


def test_failed_with_no_exit_code_returns_error_event():
    raw = format_terminal_event("failed", None, last_id=8)
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed["type"] == "error"
    assert parsed["id"] == 8
    assert parsed["message"] == "Session failed"


def test_failed_with_exit_code_returns_exit_event_with_code():
    raw = format_terminal_event("failed", 1, last_id=8)
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed["type"] == "exit"
    assert parsed["id"] == 8
    assert parsed["code"] == 1


def test_failed_with_nonzero_exit_code_carries_exact_code():
    raw = format_terminal_event("failed", 137, last_id=2)
    parsed = json.loads(raw)
    assert parsed["type"] == "exit"
    assert parsed["code"] == 137


def test_completed_returns_exit_event_with_code_zero():
    raw = format_terminal_event("completed", 0, last_id=4)
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed["type"] == "exit"
    assert parsed["id"] == 4
    assert parsed["code"] == 0


def test_running_status_returns_none():
    assert format_terminal_event("running", None, last_id=1) is None


def test_pending_status_returns_none():
    assert format_terminal_event("pending", None, last_id=1) is None


def test_unknown_status_returns_none():
    """Defensive: any non-terminal string must short-circuit so the
    caller's loop keeps polling."""
    assert format_terminal_event("queued", 0, last_id=1) is None


def test_terminal_event_types_are_pinned_exact_strings():
    """Pin the exact type strings — wrap mutants like ``XXterminatedXX``
    or ``XXexitXX`` lose their hiding place once these match exactly."""
    assert json.loads(format_terminal_event("terminated", None, 1))["type"] == "terminated"
    assert json.loads(format_terminal_event("failed", None, 1))["type"] == "error"
    assert json.loads(format_terminal_event("failed", 1, 1))["type"] == "exit"
    assert json.loads(format_terminal_event("completed", 0, 1))["type"] == "exit"
