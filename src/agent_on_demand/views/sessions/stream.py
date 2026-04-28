import json

from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_GET

from agent_on_demand.auth import require_api_key
from agent_on_demand.models import AgentSession

# Test code patches `agent_on_demand.views.sessions.stream_session_from_db` to
# stub the SSE source. Resolve through the package namespace so the patch is
# observed at call time.
from agent_on_demand.views import sessions as _pkg  # noqa: E402


@require_GET
@require_api_key
async def stream_session(request, session_id):
    """Stream session logs via SSE (live tail + full replay)."""
    try:
        session = await AgentSession.objects.aget(pk=session_id, user=request.user)
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    raw = request.META.get("HTTP_LAST_EVENT_ID") or request.GET.get("since", "0")
    try:
        since = max(0, int(raw))
    except ValueError:
        return JsonResponse({"detail": "since must be an integer"}, status=400)

    async def event_generator():
        yield (
            "data: "
            + json.dumps(
                {"type": "start", "runtime": session.runtime, "session_id": str(session.id)}
            )
            + "\n\n"
        )

        async for event in _pkg.stream_session_from_db(str(session.id), since=since):
            if event == "":
                yield ": heartbeat\n\n"
            else:
                payload = json.loads(event)
                log_id = payload.get("id")
                # turn_start is a synthetic event derived from the same DB row
                # as the output event that follows it. Advancing the SSE cursor
                # on turn_start would cause the output event (same id) to be
                # skipped on reconnect. Only real log row events advance it.
                if log_id and payload.get("type") != "turn_start":
                    yield f"id: {log_id}\n"
                yield f"data: {event}\n\n"

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
