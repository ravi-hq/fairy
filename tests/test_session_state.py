"""Direct tests for the session state-machine predicates.

These pin the API contract for status-transition rejection responses.
SDKs read the `detail` strings to decide whether to retry, refetch, or
surface to the user.
"""

import json

from agent_on_demand.session_state import (
    check_can_accept_prompt,
    check_can_delete,
    check_can_interrupt,
    check_can_terminate,
)


def body(resp):
    return json.loads(resp.content)


# === check_can_accept_prompt ===


def test_accept_prompt_allows_pending():
    assert check_can_accept_prompt("pending") is None


def test_accept_prompt_allows_completed():
    assert check_can_accept_prompt("completed") is None


def test_accept_prompt_rejects_running():
    resp = check_can_accept_prompt("running")
    assert resp is not None
    assert resp.status_code == 409
    assert body(resp) == {"detail": "Session is already running"}


def test_accept_prompt_rejects_terminated():
    resp = check_can_accept_prompt("terminated")
    assert resp.status_code == 409
    assert body(resp) == {"detail": "Session has been terminated"}


def test_accept_prompt_rejects_failed():
    resp = check_can_accept_prompt("failed")
    assert resp.status_code == 409
    assert body(resp) == {
        "detail": "Session has failed and cannot be resumed. Start a new session."
    }


def test_accept_prompt_rejection_response_only_has_detail_key():
    """No extra fields leak into the error response."""
    for status in ("running", "terminated", "failed"):
        payload = body(check_can_accept_prompt(status))
        assert set(payload.keys()) == {"detail"}, status


def test_accept_prompt_unknown_status_treated_as_acceptable():
    """A status the helper doesn't recognize is allowed through; the caller
    is responsible for ensuring `status` is a valid value. This test pins
    that behavior so a future agent doesn't silently turn unknown statuses
    into 409s (which would be a breaking change for any new statuses)."""
    assert check_can_accept_prompt("queued") is None
    assert check_can_accept_prompt("") is None


# === check_can_terminate ===


def test_terminate_allows_pending():
    assert check_can_terminate("pending") is None


def test_terminate_allows_running():
    """A running session CAN be terminated — that's the whole point."""
    assert check_can_terminate("running") is None


def test_terminate_allows_completed():
    assert check_can_terminate("completed") is None


def test_terminate_allows_failed():
    assert check_can_terminate("failed") is None


def test_terminate_rejects_already_terminated():
    resp = check_can_terminate("terminated")
    assert resp is not None
    assert resp.status_code == 409
    assert body(resp) == {"detail": "Session is already terminated"}


def test_terminate_rejection_response_only_has_detail_key():
    payload = body(check_can_terminate("terminated"))
    assert set(payload.keys()) == {"detail"}


# === check_can_delete ===


def test_delete_rejects_pending():
    """A pending session has provision_session_task in flight; deleting the
    row mid-provision orphans the Sprite or crashes the task."""
    resp = check_can_delete("pending")
    assert resp is not None
    assert resp.status_code == 409
    assert body(resp) == {"detail": "Cannot delete an active session"}


def test_delete_allows_completed():
    assert check_can_delete("completed") is None


def test_delete_allows_failed():
    """Failed sessions are deletable — failure is terminal but the row should
    be removable."""
    assert check_can_delete("failed") is None


def test_delete_allows_terminated():
    """Terminated sessions are deletable; terminate then delete is a real flow."""
    assert check_can_delete("terminated") is None


def test_delete_rejects_running():
    resp = check_can_delete("running")
    assert resp is not None
    assert resp.status_code == 409
    assert body(resp) == {"detail": "Cannot delete an active session"}


def test_delete_rejection_response_only_has_detail_key():
    payload = body(check_can_delete("running"))
    assert set(payload.keys()) == {"detail"}


# === check_can_interrupt ===


def test_interrupt_allows_pending():
    """A pending session has a turn enqueued; interrupt cancels it
    before the worker runs it."""
    assert check_can_interrupt("pending") is None


def test_interrupt_allows_running():
    """A running session has an in-flight turn; interrupt SIGTERMs it."""
    assert check_can_interrupt("running") is None


def test_interrupt_rejects_completed():
    resp = check_can_interrupt("completed")
    assert resp is not None
    assert resp.status_code == 409
    assert body(resp) == {"detail": "Session has no active turn to interrupt"}


def test_interrupt_rejects_terminated():
    resp = check_can_interrupt("terminated")
    assert resp is not None
    assert resp.status_code == 409
    assert body(resp) == {"detail": "Session has been terminated"}


def test_interrupt_rejects_failed():
    resp = check_can_interrupt("failed")
    assert resp is not None
    assert resp.status_code == 409
    assert body(resp) == {"detail": "Session has failed"}


def test_interrupt_rejection_response_only_has_detail_key():
    for status in ("completed", "terminated", "failed"):
        payload = body(check_can_interrupt(status))
        assert set(payload.keys()) == {"detail"}, status


def test_interrupt_unknown_status_treated_as_acceptable():
    """Mirrors the unknown-status policy on check_can_accept_prompt: callers
    are responsible for ensuring the status is a known value, and the helper
    doesn't 409 on unrecognized states."""
    assert check_can_interrupt("queued") is None
    assert check_can_interrupt("") is None
