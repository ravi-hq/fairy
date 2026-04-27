"""Pure SSE-event formatting for the session log stream.

Extracted from `stream.py` so the per-chunk and terminal-event branching
gets direct, sync-only coverage under mutmut. The async generator in
`stream.py` still owns the ORM polling, heartbeat cadence, and
idle-limit logic — only the JSON-payload shaping lives here.
"""

from __future__ import annotations

import json


def format_event(event_type: str, log_id: int, payload: dict) -> str:
    """Serialise one SSE event payload to a JSON string.

    The wire format pins ``type`` and ``id`` at the top level alongside
    whatever keys the caller supplied in ``payload``. SDKs parse these
    field names verbatim, so the exact strings are part of the API
    contract.
    """
    return json.dumps({"type": event_type, "id": log_id, **payload})


def format_chunk_events(
    chunk: dict, last_turn_id: int | None
) -> tuple[list[str], int | None]:
    """Translate one ``AgentSessionLog`` row into SSE event strings.

    Returns ``(events, new_last_turn_id)``:

      - ``kind="stage"``: one ``stage`` event. ``duration_ms`` is
        included only when not None; ``message`` only when ``state``
        is ``"failed"`` and ``data`` is truthy. ``last_turn_id``
        passes through unchanged.
      - ``kind="output"``: when ``turn_id`` is set and differs from
        ``last_turn_id``, prepend a ``turn_start`` event before the
        ``output`` event and advance ``last_turn_id`` to ``turn_id``.
        Otherwise emit only the ``output`` event and leave
        ``last_turn_id`` alone.
    """
    log_id = chunk["id"]
    if chunk["kind"] == "stage":
        payload: dict = {"stage": chunk["stage"], "state": chunk["state"]}
        if chunk["duration_ms"] is not None:
            payload["duration_ms"] = chunk["duration_ms"]
        if chunk["state"] == "failed" and chunk["data"]:
            payload["message"] = chunk["data"]
        return [format_event("stage", log_id, payload)], last_turn_id

    events: list[str] = []
    turn_id = chunk["turn_id"]
    if turn_id is not None and turn_id != last_turn_id:
        events.append(
            format_event("turn_start", log_id, {"turn": chunk["turn__turn_number"]})
        )
        last_turn_id = turn_id
    events.append(
        format_event(
            "output",
            log_id,
            {
                "stream": chunk["stream"],
                "data": chunk["data"],
                "turn": chunk["turn__turn_number"],
            },
        )
    )
    return events, last_turn_id


def format_terminal_event(
    status: str, exit_code: int | None, last_id: int
) -> str | None:
    """Return the SSE event closing out a terminal session, or ``None``.

    Branches:
      - ``"terminated"`` → ``terminated`` event with a message.
      - ``"failed"`` with ``exit_code is None`` → ``error`` event.
      - ``"failed"`` with an exit code, or ``"completed"`` → ``exit``
        event carrying ``code``.
      - any other status (``"pending"``, ``"running"``) → ``None``,
        signalling the caller's loop should keep polling.
    """
    if status == "terminated":
        return format_event("terminated", last_id, {"message": "Session terminated"})
    if status == "failed" and exit_code is None:
        return format_event("error", last_id, {"message": "Session failed"})
    if status in ("completed", "failed"):
        return format_event("exit", last_id, {"code": exit_code})
    return None
