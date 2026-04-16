import json
import logging
import threading
import uuid

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from pydantic import BaseModel, Field, ValidationError
from sprites import SpritesClient, SpriteError

from fairy.models import AgentSession
from fairy.runtimes import RUNTIMES
from fairy.sprites_exec import build_wrapper_script
from fairy.stream import run_session_background, stream_session_from_db

logger = logging.getLogger(__name__)


def _get_client() -> SpritesClient:
    return SpritesClient(
        token=settings.SPRITES_TOKEN,
        base_url=settings.SPRITES_BASE_URL,
    )


class RunRequest(BaseModel):
    runtime: str = Field(description="AI runtime: claude, codex, or gemini")
    prompt: str = Field(description="The prompt to send to the agent")
    api_key: str = Field(description="API key for the chosen runtime")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_POST
def create_session(request):
    """Create a session, start execution in background, return session info."""
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON"}, status=400)

    try:
        req = RunRequest(**body)
    except ValidationError as e:
        return JsonResponse({"detail": e.errors()}, status=422)

    if req.runtime not in RUNTIMES:
        return JsonResponse(
            {"detail": f"Unknown runtime: {req.runtime}. Must be one of: {list(RUNTIMES)}"},
            status=400,
        )

    config = RUNTIMES[req.runtime]
    name = f"{settings.SPRITE_NAME_PREFIX}-{uuid.uuid4().hex[:12]}"
    client = _get_client()

    try:
        sprite = client.create_sprite(name)
    except SpriteError as e:
        return JsonResponse({"detail": f"Failed to create Sprite: {e}"}, status=502)

    try:
        fs = sprite.filesystem()
        script = build_wrapper_script(config, req.api_key, req.prompt)
        (fs / "run-agent.sh").write_text(script)
        sprite.command("chmod", "+x", "/run-agent.sh").run()
    except SpriteError as e:
        _cleanup(client, name)
        return JsonResponse({"detail": f"Failed to prepare Sprite: {e}"}, status=502)

    # Create session record
    session = AgentSession.objects.create(
        runtime=req.runtime,
        prompt=req.prompt,
        sprite_name=name,
        status="pending",
    )

    # Start background execution
    def cleanup():
        _cleanup(client, name)

    thread = threading.Thread(
        target=run_session_background,
        args=(session, sprite, float(req.timeout), cleanup),
        daemon=True,
    )
    thread.start()

    return JsonResponse(
        {
            "id": str(session.id),
            "status": "pending",
            "stream_url": f"/sessions/{session.id}/stream",
        },
        status=202,
    )


@require_GET
def get_session(request, session_id):
    """Return session metadata."""
    try:
        session = AgentSession.objects.get(pk=session_id)
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    return JsonResponse({
        "id": str(session.id),
        "runtime": session.runtime,
        "status": session.status,
        "exit_code": session.exit_code,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    })


@require_GET
def stream_session(request, session_id):
    """Stream session logs via SSE.

    Works during execution (live tail) and after completion (full replay).
    """
    try:
        session = AgentSession.objects.get(pk=session_id)
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    def event_generator():
        yield f"data: {json.dumps({'type': 'start', 'runtime': session.runtime, 'session_id': str(session.id)})}\n\n"

        for event in stream_session_from_db(str(session.id)):
            if event == "":
                yield ": heartbeat\n\n"
            else:
                yield f"data: {event}\n\n"

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _cleanup(client: SpritesClient, sprite_name: str):
    try:
        client.delete_sprite(sprite_name)
    except SpriteError:
        logger.warning("Failed to cleanup Sprite %s", sprite_name, exc_info=True)
