import json
import logging
import uuid

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from pydantic import BaseModel, Field, ValidationError
from sprites import SpritesClient, SpriteError

from fairy.runtimes import RUNTIMES
from fairy.sprites_exec import build_wrapper_script
from fairy.stream import stream_agent_output

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
def run_agent(request):
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

    def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'start', 'runtime': req.runtime, 'sprite': name})}\n\n"
            for event in stream_agent_output(sprite, float(req.timeout)):
                yield f"data: {event}\n\n"
        except Exception as e:
            logger.exception("Error during agent streaming")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            _cleanup(client, name)

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _cleanup(client: SpritesClient, sprite_name: str):
    try:
        client.delete_sprite(sprite_name)
    except SpriteError:
        logger.warning("Failed to cleanup Sprite %s", sprite_name, exc_info=True)
