from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from agent_on_demand import session_service
from agent_on_demand.analytics import capture as posthog_capture
from agent_on_demand.auth import require_api_key
from agent_on_demand.models import AgentSession, SessionTurn
from agent_on_demand.views._helpers import parse_request_body

from .schemas import PromptRequest
from .serializers import _serialize_session, _serialize_turn

# Resolve the session-state predicates and `stream_session_from_db` through
# the package namespace at call time. Tests patch them at
# `agent_on_demand.views.sessions.<name>`; going through `_pkg` is the only
# way for those patches to be observed without per-test module knowledge.
# All three predicates use the same indirection so the convention is uniform
# — adding a fourth predicate later won't silently bypass test patches.
from agent_on_demand.views import sessions as _pkg  # noqa: E402


@require_GET
@require_api_key
def get_session(request, session_id):
    """Return session metadata."""
    try:
        session = AgentSession.objects.get(pk=session_id, user=request.user)
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    return JsonResponse(_serialize_session(session))


@csrf_exempt
@require_POST
@require_api_key
def send_prompt(request, session_id):
    """Send a subsequent prompt to an existing session's Sprite.

    The wrapper script is already on the Sprite from session-create. We only
    write the new prompt file and invoke `bash /run-agent.sh continue`.
    """
    try:
        session = AgentSession.objects.get(pk=session_id, user=request.user)
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    # A `failed` session may have left its Sprite mid-execution (see Muddy
    # Zone 8 in thoughts/research/2026-04-18-sprites-script-setup.md). Making
    # `failed` terminal prevents a resume from colliding with a runaway turn.
    err = _pkg.check_can_accept_prompt(session.status)
    if err is not None:
        return err

    req, err = parse_request_body(request, PromptRequest)
    if err is not None:
        return err

    try:
        session_service.resume_session(request.user, session.backend_handle)
    except session_service.NoBackendCredentialsError as e:
        return JsonResponse({"detail": str(e)}, status=400)
    except session_service.SessionHandleNotFound:
        # The backend handle is gone (e.g. idle timeout on the underlying
        # platform). The session record still exists, so 404 would be
        # misleading — callers cannot distinguish "session not found" from
        # "backend handle no longer available". Return 409 with an actionable
        # message instead.
        return JsonResponse(
            {"detail": "Session backend is no longer available; start a new session."},
            status=409,
        )

    # Atomically lock the session row, re-check state, allocate a turn number,
    # and enqueue the worker task. All four steps are inside the same
    # transaction so a task is never deferred without its turn row committed,
    # and a turn row is never committed without a task enqueued to drive it.
    try:
        with transaction.atomic():
            locked = AgentSession.objects.select_for_update().get(pk=session.id)
            err = _pkg.check_can_accept_prompt(locked.status)
            if err is not None:
                return err
            # In addition to the pre-lock checks (which allow pending — that's
            # the initial state of a fresh session), reject pending here: if
            # status flipped to pending between the two checks, another
            # send_prompt raced in and already enqueued a turn.
            if locked.status == "pending":
                return JsonResponse({"detail": "Session already has a pending turn"}, status=409)

            next_turn_number = (
                SessionTurn.objects.filter(session=locked).aggregate(n=Max("turn_number"))["n"] or 0
            ) + 1
            turn = SessionTurn.objects.create(
                session=locked,
                turn_number=next_turn_number,
                prompt=req.prompt,
                status="pending",
            )
            locked.prompt = req.prompt
            locked.status = "pending"
            locked.exit_code = None
            locked.save(update_fields=["prompt", "status", "exit_code", "updated_at"])
            session = locked
            session_service.run_turn(session, turn, req.prompt, "continue", float(req.timeout))
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    posthog_capture(
        request.user,
        "session.prompt_sent",
        properties={
            "session_id": str(session.id),
            "turn_number": turn.turn_number,
            "prompt_length": len(req.prompt),
            "timeout": req.timeout,
        },
    )

    return JsonResponse(
        {
            "id": str(session.id),
            "status": "pending",
            "stream_url": f"/sessions/{session.id}/stream",
            "current_turn": turn.turn_number,
        },
        status=202,
    )


@require_GET
@require_api_key
def list_session_turns(request, session_id):
    """Return the full turn history for a session, ordered by turn_number."""
    try:
        session = AgentSession.objects.get(pk=session_id, user=request.user)
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    turns = session.turns.order_by("turn_number")
    return JsonResponse({"data": [_serialize_turn(t) for t in turns]})


@csrf_exempt
@require_POST
@require_api_key
def terminate_session(request, session_id):
    """Terminate a session's Sprite without deleting the session record."""
    try:
        with transaction.atomic():
            session = AgentSession.objects.select_for_update().get(pk=session_id, user=request.user)
            err = _pkg.check_can_terminate(session.status)
            if err is not None:
                return err
            handle = session.backend_handle
            session.status = "terminated"
            session.backend_handle = ""
            session.save(update_fields=["status", "backend_handle", "updated_at"])
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    if handle:
        session_service.destroy_session_task.defer(user_id=request.user.id, handle=handle)

    posthog_capture(
        request.user,
        "session.terminated",
        properties={"session_id": str(session.id)},
    )

    return JsonResponse(
        {
            "id": str(session.id),
            "status": "terminated",
        }
    )


@csrf_exempt
@require_api_key
def delete_session(request, session_id):
    """Delete a session and its Sprite."""
    if request.method != "DELETE":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    try:
        with transaction.atomic():
            session = AgentSession.objects.select_for_update().get(pk=session_id, user=request.user)
            err = _pkg.check_can_delete(session.status)
            if err is not None:
                return err
            session_id_str = str(session.id)
            session.delete()  # pre_delete signal handles Sprite cleanup
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    posthog_capture(
        request.user,
        "session.deleted",
        properties={"session_id": session_id_str},
    )

    return JsonResponse({"detail": "Session deleted"}, status=200)
