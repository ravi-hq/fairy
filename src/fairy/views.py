import json
import logging
import threading
import uuid

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sprites import SpritesClient, SpriteError

from fairy.auth import require_api_key
from fairy.models import Agent, AgentSession, AgentVersion, SessionResource, UserRuntimeKey
from fairy.runtimes import RUNTIMES, AgentModel
from fairy.sprites_exec import RepoSpec, build_wrapper_script
from fairy.stream import run_session_background, stream_session_from_db

logger = logging.getLogger(__name__)


def _get_client() -> SpritesClient:
    return SpritesClient(
        token=settings.SPRITES_TOKEN,
        base_url=settings.SPRITES_BASE_URL,
    )


def _get_runtime_key(user, runtime: str) -> str | None:
    """Look up the user's stored API key for a runtime."""
    try:
        urk = UserRuntimeKey.objects.get(user=user, runtime=runtime)
        return urk.get_api_key()
    except UserRuntimeKey.DoesNotExist:
        return None


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
    runtime: str | None = Field(default=None, description="AI runtime: claude, codex, or gemini")
    prompt: str = Field(description="The prompt to send to the agent")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")
    agent_id: str | None = Field(default=None, description="Agent ID to use for this session")
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


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_POST
@require_api_key
def create_session(request):
    """Create a session, start execution in background, return session info."""
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON"}, status=400)

    try:
        req = RunRequest(**body)
    except ValidationError as e:
        return JsonResponse({"detail": e.errors(include_context=False)}, status=422)

    # Resolve agent if provided
    agent_obj = None
    if req.agent_id:
        try:
            agent_obj = Agent.objects.get(pk=req.agent_id, user=request.user)
        except (Agent.DoesNotExist, ValueError):
            return JsonResponse({"detail": "Agent not found"}, status=404)
        if agent_obj.is_archived:
            return JsonResponse({"detail": "Cannot create session with archived agent"}, status=409)

    # Runtime: explicit > agent > error
    runtime = req.runtime or (agent_obj.runtime if agent_obj else None)
    if not runtime:
        return JsonResponse(
            {"detail": "runtime is required (or provide agent_id with a configured runtime)"},
            status=400,
        )

    if runtime not in RUNTIMES:
        return JsonResponse(
            {"detail": f"Unknown runtime: {runtime}. Must be one of: {list(RUNTIMES)}"},
            status=400,
        )

    api_key = _get_runtime_key(request.user, runtime)
    if api_key is None:
        return JsonResponse(
            {"detail": f"No API key configured for runtime: {runtime}"},
            status=400,
        )

    config = RUNTIMES[runtime]
    name = f"{settings.SPRITE_NAME_PREFIX}-{uuid.uuid4().hex[:12]}"
    client = _get_client()

    try:
        sprite = client.create_sprite(name)
    except SpriteError as e:
        return JsonResponse({"detail": f"Failed to create Sprite: {e}"}, status=502)

    try:
        fs = sprite.filesystem()
        repo_specs = _resources_to_repo_specs(req.resources)

        # Build the effective prompt: agent system prompt + user prompt
        effective_prompt = req.prompt
        if agent_obj and agent_obj.system:
            effective_prompt = f"{agent_obj.system}\n\n{req.prompt}"

        script = build_wrapper_script(config, api_key, effective_prompt, repos=repo_specs)
        (fs / "run-agent.sh").write_text(script)
        sprite.command("chmod", "+x", "/run-agent.sh").run()
    except SpriteError as e:
        try:
            client.delete_sprite(name)
        except SpriteError:
            logger.warning("Failed to cleanup Sprite %s", name, exc_info=True)
        return JsonResponse({"detail": f"Failed to prepare Sprite: {e}"}, status=502)

    # Create session record
    session = AgentSession.objects.create(
        user=request.user,
        agent=agent_obj,
        runtime=runtime,
        prompt=req.prompt,
        sprite_name=name,
        status="pending",
    )

    # Persist resources
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

    # Start background execution
    thread = threading.Thread(
        target=run_session_background,
        args=(session, sprite, float(req.timeout)),
        daemon=True,
    )
    thread.start()

    return JsonResponse(
        {
            "id": str(session.id),
            "status": "pending",
            "stream_url": f"/sessions/{session.id}/stream",
            "resources": _serialize_resources(session),
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

    return JsonResponse({
        "id": str(session.id),
        "agent_id": str(session.agent_id) if session.agent_id else None,
        "runtime": session.runtime,
        "status": session.status,
        "exit_code": session.exit_code,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "resources": _serialize_resources(session),
    })


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


class PromptRequest(BaseModel):
    prompt: str = Field(description="The prompt to send to the agent")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")


@csrf_exempt
@require_POST
@require_api_key
def send_prompt(request, session_id):
    """Send a subsequent prompt to an existing session's Sprite."""
    try:
        session = AgentSession.objects.get(pk=session_id, user=request.user)
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    if session.status == "running":
        return JsonResponse({"detail": "Session is already running"}, status=409)

    if session.status == "terminated":
        return JsonResponse({"detail": "Session has been terminated"}, status=409)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON"}, status=400)

    try:
        req = PromptRequest(**body)
    except ValidationError as e:
        return JsonResponse({"detail": e.errors(include_context=False)}, status=422)

    api_key = _get_runtime_key(request.user, session.runtime)
    if api_key is None:
        return JsonResponse(
            {"detail": f"No API key configured for runtime: {session.runtime}"},
            status=400,
        )

    config = RUNTIMES[session.runtime]
    client = _get_client()

    try:
        sprite = client.get_sprite(session.sprite_name)
    except SpriteError as e:
        return JsonResponse({"detail": f"Sprite not found: {e}"}, status=404)

    try:
        fs = sprite.filesystem()
        script = build_wrapper_script(config, api_key, req.prompt, continue_session=True)
        (fs / "run-agent.sh").write_text(script)
    except SpriteError as e:
        return JsonResponse({"detail": f"Failed to prepare Sprite: {e}"}, status=502)

    # Update session for the new prompt
    session.prompt = req.prompt
    session.status = "pending"
    session.exit_code = None
    session.save(update_fields=["prompt", "status", "exit_code", "updated_at"])

    # Start background execution
    thread = threading.Thread(
        target=run_session_background,
        args=(session, sprite, float(req.timeout)),
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

    # Delete the Sprite
    if session.sprite_name:
        client = _get_client()
        try:
            client.delete_sprite(session.sprite_name)
        except SpriteError:
            logger.warning("Failed to delete Sprite %s", session.sprite_name, exc_info=True)

    session.status = "terminated"
    session.sprite_name = ""
    session.save(update_fields=["status", "sprite_name", "updated_at"])

    return JsonResponse({
        "id": str(session.id),
        "status": "terminated",
    })


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

    session.delete()  # pre_delete signal handles Sprite cleanup
    return JsonResponse({"detail": "Session deleted"}, status=200)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

AGENT_VERSIONED_FIELDS = ("name", "description", "system", "model", "runtime", "skills", "metadata")


class CreateAgentRequest(BaseModel):
    name: str = Field(max_length=200)
    model: str = Field(max_length=100)
    runtime: str = Field(max_length=32)
    system: str = Field(default="")
    description: str = Field(default="")
    skills: list = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        if v not in AgentModel.values():
            raise ValueError(
                f"Unknown model: {v}. Must be one of: {sorted(AgentModel.values())}"
            )
        return v


class UpdateAgentRequest(BaseModel):
    version: int = Field(description="Current version — optimistic concurrency check")
    name: str | None = None
    model: str | None = None
    runtime: str | None = None
    system: str | None = None
    description: str | None = None
    skills: list | None = None
    metadata: dict | None = None

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str | None) -> str | None:
        if v is not None and v not in AgentModel.values():
            raise ValueError(
                f"Unknown model: {v}. Must be one of: {sorted(AgentModel.values())}"
            )
        return v


def _serialize_agent(agent: Agent) -> dict:
    return {
        "id": str(agent.id),
        "type": "agent",
        "name": agent.name,
        "description": agent.description or None,
        "system": agent.system or None,
        "model": agent.model,
        "runtime": agent.runtime,
        "skills": agent.skills,
        "metadata": agent.metadata,
        "version": agent.version,
        "created_at": agent.created_at.isoformat(),
        "updated_at": agent.updated_at.isoformat(),
        "archived_at": agent.archived_at.isoformat() if agent.archived_at else None,
    }


def _serialize_agent_version(av: AgentVersion) -> dict:
    return {
        "id": str(av.agent_id),
        "type": "agent",
        "name": av.name,
        "description": av.description or None,
        "system": av.system or None,
        "model": av.model,
        "runtime": av.runtime,
        "skills": av.skills,
        "metadata": av.metadata,
        "version": av.version,
        "created_at": av.created_at.isoformat(),
    }


def _snapshot_version(agent: Agent):
    """Save the current agent state as a version record."""
    AgentVersion.objects.create(
        agent=agent,
        version=agent.version,
        name=agent.name,
        description=agent.description,
        system=agent.system,
        model=agent.model,
        runtime=agent.runtime,
        skills=agent.skills,
        metadata=agent.metadata,
    )


@csrf_exempt
@require_api_key
def agents_list_create(request):
    """POST: create agent. GET: list agents."""
    if request.method == "POST":
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        try:
            req = CreateAgentRequest(**body)
        except ValidationError as e:
            return JsonResponse({"detail": e.errors(include_context=False)}, status=422)

        if req.runtime not in RUNTIMES:
            return JsonResponse(
                {"detail": f"Unknown runtime: {req.runtime}. Must be one of: {list(RUNTIMES)}"},
                status=400,
            )

        agent = Agent.objects.create(
            user=request.user,
            name=req.name,
            description=req.description,
            system=req.system,
            model=req.model,
            runtime=req.runtime,
            skills=req.skills,
            metadata=req.metadata,
            version=1,
        )
        _snapshot_version(agent)

        return JsonResponse(_serialize_agent(agent), status=201)

    elif request.method == "GET":
        qs = Agent.objects.filter(user=request.user, archived_at__isnull=True).order_by("-created_at")
        return JsonResponse({"data": [_serialize_agent(a) for a in qs]})

    return JsonResponse({"detail": "Method not allowed"}, status=405)


@csrf_exempt
@require_api_key
def agent_detail(request, agent_id):
    """GET: retrieve agent. PUT: update agent."""
    try:
        agent = Agent.objects.get(pk=agent_id, user=request.user)
    except (Agent.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Agent not found"}, status=404)

    if request.method == "GET":
        return JsonResponse(_serialize_agent(agent))

    if request.method == "PUT":
        if agent.is_archived:
            return JsonResponse({"detail": "Cannot update an archived agent"}, status=409)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        try:
            req = UpdateAgentRequest(**body)
        except ValidationError as e:
            return JsonResponse({"detail": e.errors(include_context=False)}, status=422)

        if req.version != agent.version:
            return JsonResponse(
                {"detail": f"Version mismatch: expected {agent.version}, got {req.version}"},
                status=409,
            )

        if req.runtime is not None and req.runtime not in RUNTIMES:
            return JsonResponse(
                {"detail": f"Unknown runtime: {req.runtime}. Must be one of: {list(RUNTIMES)}"},
                status=400,
            )

        # Detect changes
        changed = False
        for field in ("name", "model", "runtime", "system", "description", "skills"):
            value = getattr(req, field)
            if value is not None and value != getattr(agent, field):
                setattr(agent, field, value)
                changed = True

        # Metadata merges at key level (matching Anthropic semantics)
        if req.metadata is not None:
            merged = dict(agent.metadata)
            for k, v in req.metadata.items():
                if v == "":
                    merged.pop(k, None)
                else:
                    merged[k] = v
            if merged != agent.metadata:
                agent.metadata = merged
                changed = True

        if changed:
            agent.version += 1
            agent.save()
            _snapshot_version(agent)

        return JsonResponse(_serialize_agent(agent))

    return JsonResponse({"detail": "Method not allowed"}, status=405)


@csrf_exempt
@require_POST
@require_api_key
def agent_archive(request, agent_id):
    """Archive an agent (read-only, no new sessions)."""
    try:
        agent = Agent.objects.get(pk=agent_id, user=request.user)
    except (Agent.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Agent not found"}, status=404)

    if agent.is_archived:
        return JsonResponse({"detail": "Agent is already archived"}, status=409)

    from django.utils import timezone
    agent.archived_at = timezone.now()
    agent.save(update_fields=["archived_at", "updated_at"])

    return JsonResponse(_serialize_agent(agent))


@require_GET
@require_api_key
def agent_versions(request, agent_id):
    """List all versions of an agent."""
    try:
        agent = Agent.objects.get(pk=agent_id, user=request.user)
    except (Agent.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Agent not found"}, status=404)

    versions = AgentVersion.objects.filter(agent=agent).order_by("-version")
    return JsonResponse({"data": [_serialize_agent_version(av) for av in versions]})
