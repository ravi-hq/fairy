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
from sprites import NetworkPolicy, PolicyRule, SpritesClient, SpriteError

from fairy.auth import require_api_key
from fairy.models import (
    Agent, AgentSession, AgentVersion, Environment, EnvironmentVersion,
    SessionResource, UserRuntimeKey,
)
from fairy.runtimes import RUNTIMES, AgentModel
from fairy.sprites_exec import (
    EnvironmentSetup,
    McpServerSpec,
    RepoSpec,
    SkillSpec,
    build_wrapper_script,
)
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


def _environment_to_network_policy(env: Environment | None) -> NetworkPolicy | None:
    """Build a Sprites NetworkPolicy from an Environment's networking fields.

    Returns None for unrestricted environments so the caller can skip the
    update_network_policy call and rely on the Sprites default (allow-all).
    """
    if env is None or env.networking_type != "limited":
        return None
    allowed_hosts = (env.networking_config or {}).get("allowed_hosts", [])
    rules = [PolicyRule(domain=host, action="allow") for host in allowed_hosts]
    rules.append(PolicyRule(domain="*", action="deny"))
    return NetworkPolicy(rules=rules)


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
    agent_id: str = Field(description="Agent ID to use for this session")
    prompt: str = Field(description="The prompt to send to the agent")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")
    environment_id: str | None = Field(default=None, description="Environment ID (overrides agent default)")
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

    # Resolve agent (required)
    try:
        agent_obj = Agent.objects.get(pk=req.agent_id, user=request.user)
    except (Agent.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Agent not found"}, status=404)
    if agent_obj.is_archived:
        return JsonResponse({"detail": "Cannot create session with archived agent"}, status=409)

    # Resolve environment: explicit > agent default > none
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

    # Runtime from agent
    runtime = agent_obj.runtime
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
        network_policy = _environment_to_network_policy(environment_obj)
        if network_policy is not None:
            sprite.update_network_policy(network_policy)

        fs = sprite.filesystem()
        repo_specs = _resources_to_repo_specs(req.resources)

        # Build the effective prompt: agent system prompt + user prompt
        effective_prompt = req.prompt
        if agent_obj.system:
            effective_prompt = f"{agent_obj.system}\n\n{req.prompt}"

        env_setup = None
        if environment_obj:
            env_setup = EnvironmentSetup(
                packages=environment_obj.packages,
                env_vars=environment_obj.env_vars,
                setup_script=environment_obj.setup_script,
            )

        mcp_specs = _mcp_servers_to_specs(agent_obj.mcp_servers) if agent_obj else []
        skill_specs = _skills_to_specs(agent_obj.skills) if agent_obj else []
        script = build_wrapper_script(
            config, api_key, effective_prompt,
            repos=repo_specs, environment=env_setup,
            mcp_servers=mcp_specs, skills=skill_specs,
        )
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
        environment=environment_obj,
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
            "environment_id": str(session.environment_id) if session.environment_id else None,
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
        "environment_id": str(session.environment_id) if session.environment_id else None,
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
        skill_specs = _skills_to_specs(session.agent.skills) if session.agent else []
        script = build_wrapper_script(
            config, api_key, req.prompt,
            continue_session=True, skills=skill_specs,
        )
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

AGENT_VERSIONED_FIELDS = ("name", "description", "system", "model", "runtime", "environment", "skills", "tools", "mcp_servers", "metadata")


VALID_TOOL_TYPES = {"agent_toolset_20260401", "mcp_toolset", "custom"}
VALID_MCP_SERVER_TYPES = {"url", "stdio"}


def _validate_tools(tools: list) -> list:
    for i, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise ValueError(f"tools[{i}] must be an object")
        if "type" not in tool:
            raise ValueError(f"tools[{i}] missing required field: type")
        if tool["type"] not in VALID_TOOL_TYPES:
            raise ValueError(
                f"tools[{i}]: unknown type {tool['type']!r}. "
                f"Must be one of: {sorted(VALID_TOOL_TYPES)}"
            )
        if tool["type"] == "custom":
            for field_name in ("name", "description", "input_schema"):
                if field_name not in tool:
                    raise ValueError(f"tools[{i}] (custom): missing required field: {field_name}")
        if tool["type"] == "mcp_toolset" and "mcp_server_name" not in tool:
            raise ValueError(f"tools[{i}] (mcp_toolset): missing required field: mcp_server_name")
    return tools


MAX_SKILLS_PER_AGENT = 20
MAX_SKILL_DESCRIPTION_LEN = 1024
MAX_SKILL_CONTENT_BYTES = 64 * 1024

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SKILL_ALLOWED_KEYS = {"name", "description", "content"}
_SKILL_HEREDOC_DELIMITER = "SKILL_EOF"


def _validate_skills(skills: list) -> list:
    if len(skills) > MAX_SKILLS_PER_AGENT:
        raise ValueError(f"Maximum {MAX_SKILLS_PER_AGENT} skills per agent")
    seen_names: set[str] = set()
    for i, skill in enumerate(skills):
        if not isinstance(skill, dict):
            raise ValueError(f"skills[{i}] must be an object")

        extra = set(skill) - _SKILL_ALLOWED_KEYS
        if extra:
            raise ValueError(
                f"skills[{i}]: unknown keys {sorted(extra)!r}. "
                f"Allowed: {sorted(_SKILL_ALLOWED_KEYS)}"
            )
        for field_name in ("name", "description", "content"):
            if field_name not in skill:
                raise ValueError(f"skills[{i}] missing required field: {field_name}")
            if not isinstance(skill[field_name], str):
                raise ValueError(f"skills[{i}].{field_name} must be a string")

        name = skill["name"]
        if not _SKILL_NAME_RE.match(name):
            raise ValueError(
                f"skills[{i}].name {name!r} must match [a-z0-9][a-z0-9-]{{0,63}}"
            )
        if name in seen_names:
            raise ValueError(f"skills[{i}]: duplicate name {name!r}")
        seen_names.add(name)

        if len(skill["description"]) > MAX_SKILL_DESCRIPTION_LEN:
            raise ValueError(
                f"skills[{i}].description exceeds {MAX_SKILL_DESCRIPTION_LEN} chars"
            )

        content = skill["content"]
        if len(content.encode("utf-8")) > MAX_SKILL_CONTENT_BYTES:
            raise ValueError(
                f"skills[{i}].content exceeds {MAX_SKILL_CONTENT_BYTES} bytes"
            )
        if _SKILL_HEREDOC_DELIMITER in content:
            raise ValueError(
                f"skills[{i}].content must not contain {_SKILL_HEREDOC_DELIMITER!r}"
            )
    return skills


def _validate_mcp_servers(servers: list) -> list:
    names = set()
    for i, server in enumerate(servers):
        if not isinstance(server, dict):
            raise ValueError(f"mcp_servers[{i}] must be an object")
        if "name" not in server:
            raise ValueError(f"mcp_servers[{i}] missing required field: name")
        stype = server.get("type", "url")
        if stype not in VALID_MCP_SERVER_TYPES:
            raise ValueError(
                f"mcp_servers[{i}]: unknown type {stype!r}. "
                f"Must be one of: {sorted(VALID_MCP_SERVER_TYPES)}"
            )
        if stype == "url" and "url" not in server:
            raise ValueError(f"mcp_servers[{i}] (url): missing required field: url")
        if stype == "stdio" and "command" not in server:
            raise ValueError(f"mcp_servers[{i}] (stdio): missing required field: command")
        if server["name"] in names:
            raise ValueError(f"mcp_servers[{i}]: duplicate name {server['name']!r}")
        names.add(server["name"])
    if len(servers) > 20:
        raise ValueError("Maximum 20 MCP servers per agent")
    return servers


class CreateAgentRequest(BaseModel):
    name: str = Field(max_length=200)
    model: str = Field(max_length=100)
    runtime: str = Field(max_length=32)
    system: str = Field(default="")
    description: str = Field(default="")
    environment_id: str | None = Field(default=None)
    skills: list = Field(default_factory=list)
    tools: list = Field(default_factory=list)
    mcp_servers: list = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        if v not in AgentModel.values():
            raise ValueError(
                f"Unknown model: {v}. Must be one of: {sorted(AgentModel.values())}"
            )
        return v

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, v: list) -> list:
        return _validate_tools(v)

    @field_validator("mcp_servers")
    @classmethod
    def validate_mcp_servers(cls, v: list) -> list:
        return _validate_mcp_servers(v)

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, v: list) -> list:
        return _validate_skills(v)


class UpdateAgentRequest(BaseModel):
    version: int = Field(description="Current version — optimistic concurrency check")
    name: str | None = None
    model: str | None = None
    runtime: str | None = None
    system: str | None = None
    description: str | None = None
    environment_id: str | None = None
    skills: list | None = None
    tools: list | None = None
    mcp_servers: list | None = None
    metadata: dict | None = None

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str | None) -> str | None:
        if v is not None and v not in AgentModel.values():
            raise ValueError(
                f"Unknown model: {v}. Must be one of: {sorted(AgentModel.values())}"
            )
        return v

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, v: list | None) -> list | None:
        if v is not None:
            _validate_tools(v)
        return v

    @field_validator("mcp_servers")
    @classmethod
    def validate_mcp_servers(cls, v: list | None) -> list | None:
        if v is not None:
            _validate_mcp_servers(v)
        return v

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, v: list | None) -> list | None:
        if v is not None:
            _validate_skills(v)
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
        "environment_id": str(agent.environment_id) if agent.environment_id else None,
        "skills": agent.skills,
        "tools": agent.tools,
        "mcp_servers": agent.mcp_servers,
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
        "environment_id": str(av.environment_id) if av.environment_id else None,
        "skills": av.skills,
        "tools": av.tools,
        "mcp_servers": av.mcp_servers,
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
        environment=agent.environment,
        skills=agent.skills,
        tools=agent.tools,
        mcp_servers=agent.mcp_servers,
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

        env_obj = None
        if req.environment_id:
            try:
                env_obj = Environment.objects.get(pk=req.environment_id, user=request.user)
            except (Environment.DoesNotExist, ValueError):
                return JsonResponse({"detail": "Environment not found"}, status=404)

        agent = Agent.objects.create(
            user=request.user,
            name=req.name,
            description=req.description,
            system=req.system,
            model=req.model,
            runtime=req.runtime,
            environment=env_obj,
            skills=req.skills,
            tools=req.tools,
            mcp_servers=req.mcp_servers,
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

        # Resolve environment_id if provided
        if req.environment_id is not None:
            try:
                env_obj = Environment.objects.get(pk=req.environment_id, user=request.user)
            except (Environment.DoesNotExist, ValueError):
                return JsonResponse({"detail": "Environment not found"}, status=404)
            if env_obj.id != agent.environment_id:
                agent.environment = env_obj
                changed = True

        for field in ("name", "model", "runtime", "system", "description", "skills", "tools", "mcp_servers"):
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


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------

VALID_PACKAGE_MANAGERS = {"apt", "cargo", "gem", "go", "npm", "pip"}


class CreateEnvironmentRequest(BaseModel):
    name: str = Field(max_length=200)
    packages: dict[str, list[str]] = Field(default_factory=dict)
    env_vars: dict[str, str] = Field(default_factory=dict)
    setup_script: str = Field(default="")
    networking: dict = Field(default_factory=lambda: {"type": "unrestricted"})

    @field_validator("packages")
    @classmethod
    def validate_packages(cls, v: dict) -> dict:
        for manager, pkgs in v.items():
            if manager not in VALID_PACKAGE_MANAGERS:
                raise ValueError(
                    f"Unknown package manager: {manager}. "
                    f"Must be one of: {sorted(VALID_PACKAGE_MANAGERS)}"
                )
            if not isinstance(pkgs, list) or not all(isinstance(p, str) for p in pkgs):
                raise ValueError(f"packages.{manager} must be a list of strings")
        return v

    @field_validator("networking")
    @classmethod
    def validate_networking(cls, v: dict) -> dict:
        net_type = v.get("type", "unrestricted")
        if net_type not in ("unrestricted", "limited"):
            raise ValueError("networking.type must be 'unrestricted' or 'limited'")
        if net_type == "limited":
            hosts = v.get("allowed_hosts", [])
            if not isinstance(hosts, list):
                raise ValueError("networking.allowed_hosts must be a list")
        return v


class UpdateEnvironmentRequest(BaseModel):
    version: int = Field(description="Current version — optimistic concurrency check")
    name: str | None = None
    packages: dict[str, list[str]] | None = None
    env_vars: dict[str, str] | None = None
    setup_script: str | None = None
    networking: dict | None = None

    @field_validator("packages")
    @classmethod
    def validate_packages(cls, v: dict | None) -> dict | None:
        if v is not None:
            for manager, pkgs in v.items():
                if manager not in VALID_PACKAGE_MANAGERS:
                    raise ValueError(
                        f"Unknown package manager: {manager}. "
                        f"Must be one of: {sorted(VALID_PACKAGE_MANAGERS)}"
                    )
                if not isinstance(pkgs, list) or not all(isinstance(p, str) for p in pkgs):
                    raise ValueError(f"packages.{manager} must be a list of strings")
        return v

    @field_validator("networking")
    @classmethod
    def validate_networking(cls, v: dict | None) -> dict | None:
        if v is not None:
            net_type = v.get("type", "unrestricted")
            if net_type not in ("unrestricted", "limited"):
                raise ValueError("networking.type must be 'unrestricted' or 'limited'")
        return v


def _serialize_environment(env: Environment) -> dict:
    networking = {"type": env.networking_type}
    if env.networking_type == "limited" and env.networking_config:
        networking.update(env.networking_config)
    return {
        "id": str(env.id),
        "type": "environment",
        "name": env.name,
        "packages": env.packages,
        "setup_script": env.setup_script or None,
        "networking": networking,
        "version": env.version,
        "created_at": env.created_at.isoformat(),
        "updated_at": env.updated_at.isoformat(),
        "archived_at": env.archived_at.isoformat() if env.archived_at else None,
    }


def _serialize_environment_version(ev: EnvironmentVersion) -> dict:
    networking = {"type": ev.networking_type}
    if ev.networking_type == "limited" and ev.networking_config:
        networking.update(ev.networking_config)
    return {
        "id": str(ev.environment_id),
        "type": "environment",
        "name": ev.name,
        "packages": ev.packages,
        "setup_script": ev.setup_script or None,
        "networking": networking,
        "version": ev.version,
        "created_at": ev.created_at.isoformat(),
    }


def _snapshot_environment_version(env: Environment):
    """Save the current environment state as a version record."""
    EnvironmentVersion.objects.create(
        environment=env,
        version=env.version,
        name=env.name,
        packages=env.packages,
        env_vars=env.env_vars,
        setup_script=env.setup_script,
        networking_type=env.networking_type,
        networking_config=env.networking_config,
    )


@csrf_exempt
@require_api_key
def environments_list_create(request):
    """POST: create environment. GET: list environments."""
    if request.method == "POST":
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        try:
            req = CreateEnvironmentRequest(**body)
        except ValidationError as e:
            return JsonResponse({"detail": e.errors(include_context=False)}, status=422)

        networking_type = req.networking.get("type", "unrestricted")
        networking_config = {k: v for k, v in req.networking.items() if k != "type"}

        env = Environment.objects.create(
            user=request.user,
            name=req.name,
            packages=req.packages,
            env_vars=req.env_vars,
            setup_script=req.setup_script,
            networking_type=networking_type,
            networking_config=networking_config,
            version=1,
        )
        _snapshot_environment_version(env)

        return JsonResponse(_serialize_environment(env), status=201)

    elif request.method == "GET":
        qs = Environment.objects.filter(
            user=request.user, archived_at__isnull=True
        ).order_by("-created_at")
        return JsonResponse({"data": [_serialize_environment(e) for e in qs]})

    return JsonResponse({"detail": "Method not allowed"}, status=405)


@csrf_exempt
@require_api_key
def environment_detail(request, environment_id):
    """GET: retrieve environment. PUT: update environment."""
    try:
        env = Environment.objects.get(pk=environment_id, user=request.user)
    except (Environment.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Environment not found"}, status=404)

    if request.method == "GET":
        return JsonResponse(_serialize_environment(env))

    if request.method == "PUT":
        if env.is_archived:
            return JsonResponse({"detail": "Cannot update an archived environment"}, status=409)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        try:
            req = UpdateEnvironmentRequest(**body)
        except ValidationError as e:
            return JsonResponse({"detail": e.errors(include_context=False)}, status=422)

        if req.version != env.version:
            return JsonResponse(
                {"detail": f"Version mismatch: expected {env.version}, got {req.version}"},
                status=409,
            )

        changed = False
        if req.name is not None and req.name != env.name:
            env.name = req.name
            changed = True

        if req.packages is not None and req.packages != env.packages:
            env.packages = req.packages
            changed = True

        if req.env_vars is not None and req.env_vars != env.env_vars:
            env.env_vars = req.env_vars
            changed = True

        if req.setup_script is not None and req.setup_script != env.setup_script:
            env.setup_script = req.setup_script
            changed = True

        if req.networking is not None:
            new_type = req.networking.get("type", "unrestricted")
            new_config = {k: v for k, v in req.networking.items() if k != "type"}
            if new_type != env.networking_type or new_config != env.networking_config:
                env.networking_type = new_type
                env.networking_config = new_config
                changed = True

        if changed:
            env.version += 1
            env.save()
            _snapshot_environment_version(env)

        return JsonResponse(_serialize_environment(env))

    return JsonResponse({"detail": "Method not allowed"}, status=405)


@csrf_exempt
@require_POST
@require_api_key
def environment_archive(request, environment_id):
    """Archive an environment (read-only, no new sessions)."""
    try:
        env = Environment.objects.get(pk=environment_id, user=request.user)
    except (Environment.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Environment not found"}, status=404)

    if env.is_archived:
        return JsonResponse({"detail": "Environment is already archived"}, status=409)

    from django.utils import timezone
    env.archived_at = timezone.now()
    env.save(update_fields=["archived_at", "updated_at"])

    return JsonResponse(_serialize_environment(env))


@csrf_exempt
@require_api_key
def environment_delete(request, environment_id):
    """Delete an environment (only if no sessions reference it)."""
    if request.method != "DELETE":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    try:
        env = Environment.objects.get(pk=environment_id, user=request.user)
    except (Environment.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Environment not found"}, status=404)

    if env.sessions.exists():
        return JsonResponse(
            {"detail": "Cannot delete environment with existing sessions"},
            status=409,
        )

    env.delete()
    return JsonResponse({"detail": "Environment deleted"}, status=200)


@require_GET
@require_api_key
def environment_versions(request, environment_id):
    """List all versions of an environment."""
    try:
        env = Environment.objects.get(pk=environment_id, user=request.user)
    except (Environment.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Environment not found"}, status=404)

    versions = EnvironmentVersion.objects.filter(environment=env).order_by("-version")
    return JsonResponse({"data": [_serialize_environment_version(ev) for ev in versions]})
