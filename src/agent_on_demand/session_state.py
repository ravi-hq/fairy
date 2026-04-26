"""Session state-machine predicates: which operations each status allows.

Sessions move `pending → running → {completed, failed, terminated}`. The
helpers below return a 409 JsonResponse when an operation is not legal in
the session's current state, or None to proceed.

The exact `detail` strings are part of the API contract — clients/SDKs
read them to decide whether to retry, refetch, or surface to the user.
Don't change the wording without coordinating with SDK consumers; tests
in tests/test_session_state.py pin them exactly.
"""

from __future__ import annotations

from django.http import JsonResponse


def check_can_accept_prompt(status: str) -> JsonResponse | None:
    """Reject a new prompt if the session is not in an accepting state.

    Accepts: ``pending`` and ``completed``.
    Rejects: ``running``, ``terminated``, ``failed`` (each with its own message).

    The same check runs twice on send_prompt: once before acquiring the row
    lock (fast-fail) and once after (race-safe). Using one function for
    both keeps the rejection messages identical between the two paths.
    """
    if status == "running":
        return JsonResponse({"detail": "Session is already running"}, status=409)
    if status == "terminated":
        return JsonResponse({"detail": "Session has been terminated"}, status=409)
    if status == "failed":
        return JsonResponse(
            {"detail": "Session has failed and cannot be resumed. Start a new session."},
            status=409,
        )
    return None


def check_can_terminate(status: str) -> JsonResponse | None:
    """Reject termination only if already terminated (idempotent-error)."""
    if status == "terminated":
        return JsonResponse({"detail": "Session is already terminated"}, status=409)
    return None


def check_can_delete(status: str) -> JsonResponse | None:
    """Reject delete while the session is active. ``pending`` has a
    provision_session_task in flight; deleting the row mid-provision either
    crashes the task or leaves the Sprite orphaned (pre_delete fired before
    the Sprite existed). ``running`` would also leave an orphaned Sprite.
    All other states — including terminated — are deletable.
    """
    if status in ("running", "pending"):
        return JsonResponse({"detail": "Cannot delete an active session"}, status=409)
    return None
