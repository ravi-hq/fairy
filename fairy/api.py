import json
import logging
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sprites import SpritesClient, SpriteError
from sse_starlette.sse import EventSourceResponse

from fairy.config import settings
from fairy.runtimes import RUNTIMES
from fairy.sprites_exec import build_wrapper_script
from fairy.stream import stream_agent_output

logger = logging.getLogger(__name__)

app = FastAPI(title="Fairy", description="AI coding agent orchestration API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_client() -> SpritesClient:
    return SpritesClient(
        token=settings.sprites_token,
        base_url=settings.sprites_base_url,
    )


class RunRequest(BaseModel):
    runtime: str = Field(description="AI runtime: claude, codex, or gemini")
    prompt: str = Field(description="The prompt to send to the agent")
    api_key: str = Field(description="API key for the chosen runtime")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/run")
async def run_agent(req: RunRequest):
    if req.runtime not in RUNTIMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown runtime: {req.runtime}. Must be one of: {list(RUNTIMES)}",
        )

    config = RUNTIMES[req.runtime]
    name = f"{settings.sprite_name_prefix}-{uuid.uuid4().hex[:12]}"
    client = _get_client()

    try:
        sprite = client.create_sprite(name)
    except SpriteError as e:
        raise HTTPException(status_code=502, detail=f"Failed to create Sprite: {e}")

    try:
        fs = sprite.filesystem()
        script = build_wrapper_script(config, req.api_key, req.prompt)
        (fs / "run-agent.sh").write_text(script)
        sprite.command("chmod", "+x", "/run-agent.sh").run()
    except SpriteError as e:
        _cleanup(client, name)
        raise HTTPException(status_code=502, detail=f"Failed to prepare Sprite: {e}")

    async def event_generator():
        try:
            yield json.dumps({"type": "start", "runtime": req.runtime, "sprite": name})
            async for event in stream_agent_output(sprite, float(req.timeout)):
                yield event
        except Exception as e:
            logger.exception("Error during agent streaming")
            yield json.dumps({"type": "error", "message": str(e)})
        finally:
            _cleanup(client, name)

    return EventSourceResponse(event_generator(), media_type="text/event-stream")


def _cleanup(client: SpritesClient, sprite_name: str):
    try:
        client.delete_sprite(sprite_name)
    except SpriteError:
        logger.warning("Failed to cleanup Sprite %s", sprite_name, exc_info=True)
