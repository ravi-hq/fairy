import json
import logging
import re
import uuid
from typing import Literal

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from pydantic import BaseModel, Field, ValidationError, field_validator

from agent_on_demand import session_service
from agent_on_demand.auth import require_api_key
from agent_on_demand.models import (
    Agent,
    AgentSession,
    Environment,
    SessionResource,
    SessionTurn,
    UserRuntimeKey,
)
from agent_on_demand.observability import track
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.session_service import (
    McpServerSpec,
    RepoSpec,
    SessionSpec,
    SkillSpec,
)
from agent_on_demand.stream import stream_session_from_db

logger = logging.getLogger(__name__)


def _mcp_servers_to_specs(mcp_servers: list[dict]) -> list[McpServerSpec]:
    """Convert agent's mcp_servers JSON to McpServerSpec list."""
    return [
        McpServerSpec(
            name=s["name"],
            type=s.get("type", "url"),
            url=s.get("url", ""),
            headers=s.get("headers", {}),
            command=s.get("command", ""),
            args=s.get("args", []),
            env=s.get("env", {}),
        )
        for s in mcp_servers
    ]


def _skills_to_specs(skills: list[dict]) -> list[SkillSpec]:
    """Convert agent's skills JSON to SkillSpec list for materialization."""
    return [SkillSpec(name=s["name"], content=s["content"]) for s in skills]


def _resources_to_repo_specs(resources: list) -> list[RepoSpec]:
    """Convert GitHubRepoResource list to RepoSpec list for the wrapper script."""
    return [
        RepoSpec(
            url=r.url,
            mount_path=r.resolved_mount_path(),
            token=r.authorization_token,
        )
        for r in resources
    ]


def _serialize_resources(session: AgentSession) -> list[dict]:
    """Serialize session resources for API responses (token is never included)."""
    return [
        {
            "type": sr.resource_type,
            "url": sr.url,
            "mount_path": sr.mount_path,
        }
        for sr in session.resources.all()
    ]


def _serialize_session(session: AgentSession) -> dict:
    latest = session.turns.order_by("-turn_number").first()
    turn_count = latest.turn_number if latest else 0
    return {
        "id": str(session.id),
        "agent_id": str(session.agent_id) if session.agent_id else None,
        "environment_id": str(session.environment_id) if session.environment_id else None,
        "runtime": session.runtime,
        "status": session.status,
        "exit_code": session.exit_code,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "resources": _serialize_resources(session),
        "turn_count": turn_count,
        "current_turn": latest.turn_number if latest else None,
    }


def _serialize_turn(turn: SessionTurn) -> dict:
    return {
        "turn_number": turn.turn_number,
        "prompt": turn.prompt,
        "status": turn.status,
        "exit_code": turn.exit_code,
        "created_at": turn.created_at.isoformat(),
        "started_at": turn.started_at.isoformat() if turn.started_at else None,
        "ended_at": turn.ended_at.isoformat() if turn.ended_at else None,
    }


class GitHubRepoResource(BaseModel):
    type: Literal["github_repository"]
    url: str = Field(description="HTTPS GitHub repo URL, e.g. https://github.com/org/repo")
    mount_path: str | None = Field(
        default=None,
        description="Absolute path inside the Sprite where repo is cloned. "
        "Defaults to /workspace/<repo-name>.",
    )
    authorization_token: str | None = Field(
        default=None,
        description="GitHub PAT for private repos",
    )

    @field_validator("url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        if not re.match(r"^https://github\.com/[\w.-]+/[\w.-]+(\.git)?$", v):
            raise ValueError("Must be a valid https://github.com/<owner>/<repo> URL")
        return v.removesuffix(".git")

    @field_validator("mount_path")
    @classmethod
    def validate_mount_path(cls, v: str | None) -> str | None:
        if v is not None:
            if not v.startswith("/"):
                raise ValueError("mount_path must be an absolute path")
            if v in ("/", "/home/sprite"):
                raise ValueError("mount_path must not be the Sprite root")
        return v

    def resolved_mount_path(self) -> str:
        if self.mount_path:
            return self.mount_path
        repo_name = self.url.rstrip("/").split("/")[-1]
        return f"/workspace/{repo_name}"


class RunRequest(BaseModel):
    agent_id: str = Field(description="Agent ID to use for this session")
    prompt: str = Field(description="The prompt to send to the agent")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")
    environment_id: str | None = Field(
        default=None, description="Environment ID (overrides agent default)"
    )
    resources: list[GitHubRepoResource] = Field(
        default_factory=list,
        description="GitHub repositories to clone into the session",
    )

    @field_validator("resources")
    @classmethod
    def validate_resources(cls, v: list[GitHubRepoResource]) -> list[GitHubRepoResource]:
        if len(v) > 10:
            raise ValueError("Maximum 10 resources per session")
        mount_paths = [r.resolved_mount_path() for r in v]
        if len(mount_paths) != len(set(mount_paths)):
            raise ValueError("Duplicate mount_path in resources")
        return v


class PromptRequest(BaseModel):
    prompt: str = Field(description="The prompt to send to the agent")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")


@csrf_exempt
@require_api_key
def sessions_list_create(request):
    """POST: create a session. GET: list the caller's sessions."""
    if request.method == "GET":
        return _list_sessions(request)
    if request.method == "POST":
        return _create_session(request)
    return JsonResponse({"detail": "Method not allowed"}, status=405)


def _list_sessions(request):
    qs = (
        AgentSession.objects.filter(user=request.user)
        .prefetch_related("resources")
        .order_by("-created_at")
    )
    return JsonResponse({"data": [_serialize_session(s) for s in qs]})


def _create_session(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON"}, status=400)

    try:
        req = RunRequest(**body)
    except ValidationError as e:
        return JsonResponse({"detail": e.errors(include_context=False)}, status=422)

    try:
        agent_obj = Agent.objects.get(pk=req.agent_id, user=request.user)
    except (Agent.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Agent not found"}, status=404)
    if agent_obj.is_archived:
        return JsonResponse({"detail": "Cannot create session with archived agent"}, status=409)

    environment_obj = None
    env_id = req.environment_id or agent_obj.environment_id
    if env_id:
        try:
            environment_obj = Environment.objects.get(pk=env_id, user=request.user)
        except (Environment.DoesNotExist, ValueError):
            return JsonResponse({"detail": "Environment not found"}, status=404)
        if environment_obj.is_archived:
            return JsonResponse(
                {"detail": "Cannot create session with archived environment"}, status=409
            )

    runtime = agent_obj.runtime
    if runtime not in RUNTIMES:
        return JsonResponse(
            {"detail": f"Unknown runtime: {runtime}. Must be one of: {list(RUNTIMES)}"},
            status=400,
        )

    api_key = UserRuntimeKey.get_key_for(request.user, runtime)
    if api_key is None:
        return JsonResponse(
            {"detail": f"No API key configured for runtime: {runtime}"},
            status=400,
        )

    config = RUNTIMES[runtime]
    name = f"{settings.SPRITE_NAME_PREFIX}-{uuid.uuid4().hex[:12]}"
    runtime_session_id = str(uuid.uuid4())

    # First-turn prompt gets the agent's system prepended. Subsequent turns
    # inherit it via the runtime CLI's own --continue/--resume state.
    effective_prompt = req.prompt
    if agent_obj.system:
        effective_prompt = f"{agent_obj.system}\n\n{req.prompt}"

    spec = SessionSpec(
        name=name,
        runtime=config,
        api_key=api_key,
        runtime_session_id=runtime_session_id,
        environment=environment_obj,
        repos=_resources_to_repo_specs(req.resources),
        mcp_servers=_mcp_servers_to_specs(agent_obj.mcp_servers),
        skills=_skills_to_specs(agent_obj.skills),
    )

    try:
        sprite = session_service.provision_session(request.user, spec)
    except session_service.NoSpritesKeyError as e:
        return JsonResponse({"detail": str(e)}, status=400)
    except session_service.ProvisionError as e:
        logger.warning("provision failed at stage=%s: %s", e.stage, e)
        return JsonResponse({"detail": str(e)}, status=502)

    with transaction.atomic():
        session = AgentSession.objects.create(
            user=request.user,
            agent=agent_obj,
            environment=environment_obj,
            runtime=runtime,
            prompt=req.prompt,
            sprite_name=name,
            runtime_session_id=runtime_session_id,
            status="pending",
        )
        turn = SessionTurn.objects.create(
            session=session,
            turn_number=1,
            prompt=req.prompt,
            status="pending",
        )

        for resource in req.resources:
            sr = SessionResource(
                session=session,
                resource_type=resource.type,
                url=resource.url,
                mount_path=resource.resolved_mount_path(),
            )
            if resource.authorization_token:
                sr.set_token(resource.authorization_token)
            sr.save()

    session_service.run_turn(session, turn, sprite, effective_prompt, "run", float(req.timeout))

    track(
        "session.created",
        user=request.user,
        properties={
            "session_id": str(session.id),
            "agent_id": str(agent_obj.id),
            "environment_id": str(environment_obj.id) if environment_obj else None,
            "runtime": runtime,
            "model": agent_obj.model,
            "prompt_length": len(req.prompt),
            "repo_count": len(req.resources),
            "mcp_server_count": len(agent_obj.mcp_servers or []),
            "skill_count": len(agent_obj.skills or []),
            "env_var_count": len((environment_obj.env_vars or {})) if environment_obj else 0,
            "timeout": req.timeout,
        },
    )

    return JsonResponse(
        {
            "id": str(session.id),
            "status": "pending",
            "stream_url": f"/sessions/{session.id}/stream",
            "environment_id": str(session.environment_id) if session.environment_id else None,
            "resources": _serialize_resources(session),
            "current_turn": turn.turn_number,
        },
        status=202,
    )


@require_GET
@require_api_key
def get_session(request, session_id):
    """Return session metadata."""
    try:
        session = AgentSession.objects.get(pk=session_id, user=request.user)
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    return JsonResponse(_serialize_session(session))


@require_GET
@require_api_key
def stream_session(request, session_id):
    """Stream session logs via SSE.

    Works during execution (live tail) and after completion (full replay).
    """
    try:
        session = AgentSession.objects.get(pk=session_id, user=request.user)
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
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    if session.status == "running":
        return JsonResponse({"detail": "Session is already running"}, status=409)

    if session.status == "terminated":
        return JsonResponse({"detail": "Session has been terminated"}, status=409)

    # A `failed` session may have left its Sprite mid-execution (see Muddy
    # Zone 8 in thoughts/research/2026-04-18-sprites-script-setup.md). Making
    # `failed` terminal prevents a resume from colliding with a runaway turn.
    if session.status == "failed":
        return JsonResponse(
            {"detail": "Session has failed and cannot be resumed. Start a new session."},
            status=409,
        )

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON"}, status=400)

    try:
        req = PromptRequest(**body)
    except ValidationError as e:
        return JsonResponse({"detail": e.errors(include_context=False)}, status=422)

    try:
        sprite = session_service.resume_session(request.user, session.sprite_name)
    except session_service.NoSpritesKeyError as e:
        return JsonResponse({"detail": str(e)}, status=400)
    except session_service.SessionHandleNotFound as e:
        return JsonResponse({"detail": str(e)}, status=404)

    # Atomically lock the session row, re-check state, and allocate a turn
    # number. Prevents two concurrent POSTs from both creating turn N+1 or
    # transitioning the session to running.
    try:
        with transaction.atomic():
            locked = AgentSession.objects.select_for_update().get(pk=session.id)
            if locked.status == "running":
                return JsonResponse({"detail": "Session is already running"}, status=409)
            if locked.status == "terminated":
                return JsonResponse({"detail": "Session has been terminated"}, status=409)
            if locked.status == "failed":
                return JsonResponse(
                    {"detail": ("Session has failed and cannot be resumed. Start a new session.")},
                    status=409,
                )

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
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    session_service.run_turn(session, turn, sprite, req.prompt, "continue", float(req.timeout))

    track(
        "session.prompt_sent",
        user=request.user,
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
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    turns = session.turns.order_by("turn_number")
    return JsonResponse({"data": [_serialize_turn(t) for t in turns]})


@csrf_exempt
@require_POST
@require_api_key
def terminate_session(request, session_id):
    """Terminate a session's Sprite without deleting the session record."""
    try:
        session = AgentSession.objects.get(pk=session_id, user=request.user)
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    if session.status == "terminated":
        return JsonResponse({"detail": "Session is already terminated"}, status=409)

    session_service.destroy_session(request.user, session.sprite_name)

    session.status = "terminated"
    session.sprite_name = ""
    session.save(update_fields=["status", "sprite_name", "updated_at"])

    track(
        "session.terminated",
        user=request.user,
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
        session = AgentSession.objects.get(pk=session_id, user=request.user)
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    if session.status == "running":
        return JsonResponse({"detail": "Cannot delete a running session"}, status=409)

    session_id_str = str(session.id)
    session.delete()  # pre_delete signal handles Sprite cleanup

    track(
        "session.deleted",
        user=request.user,
        properties={"session_id": session_id_str},
    )

    return JsonResponse({"detail": "Session deleted"}, status=200)
