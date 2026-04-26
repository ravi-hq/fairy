import json
import re

import posthog
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from pydantic import BaseModel, Field, ValidationError, field_validator

from agent_on_demand.auth import require_api_key
from agent_on_demand.models import Agent, AgentVersion, Environment
from agent_on_demand.models_catalog import MODELS
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.versioning import check_version_match


AGENT_VERSIONED_FIELDS = (
    "name",
    "description",
    "system",
    "model",
    "runtime",
    "environment",
    "skills",
    "mcp_servers",
    "metadata",
)


VALID_MCP_SERVER_TYPES = {"url", "stdio"}


MAX_SKILLS_PER_AGENT = 20
MAX_SKILL_DESCRIPTION_LEN = 1024
MAX_SKILL_CONTENT_BYTES = 64 * 1024

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# `owner/repo` — same character class GitHub allows for both segments.
_GITHUB_SOURCE_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

# Two skill shapes:
#   inline: {name, description, content}          ← content shipped in-band
#   github: {type: "github", description, source, name?}
#                                                 ← installed on the Sprite at
#                                                   provision time via the
#                                                   skills.sh CLI
#                                                   (`npx skills add ...`).
#                                                   `name` selects a single skill
#                                                   from the repo via the CLI's
#                                                   `--skill` flag; omit it to
#                                                   install every SKILL.md the
#                                                   repo exposes.
# Detection: presence of `type` field. Inline shape omits it.
_INLINE_SKILL_KEYS = {"name", "description", "content"}
_GITHUB_SKILL_KEYS = {"type", "name", "description", "source"}
_GITHUB_REQUIRED_KEYS = {"type", "description", "source"}

_SKILL_HEREDOC_DELIMITER = "SKILL_EOF"


def _validate_skill_inline(skill: dict, i: int) -> None:
    extra = set(skill) - _INLINE_SKILL_KEYS
    if extra:
        raise ValueError(
            f"skills[{i}]: unknown keys {sorted(extra)!r}. "
            f"Allowed for inline skills: {sorted(_INLINE_SKILL_KEYS)}"
        )
    for field_name in ("name", "description", "content"):
        if field_name not in skill:
            raise ValueError(f"skills[{i}] missing required field: {field_name}")
        if not isinstance(skill[field_name], str):
            raise ValueError(f"skills[{i}].{field_name} must be a string")
    content = skill["content"]
    if len(content.encode("utf-8")) > MAX_SKILL_CONTENT_BYTES:
        raise ValueError(f"skills[{i}].content exceeds {MAX_SKILL_CONTENT_BYTES} bytes")
    if _SKILL_HEREDOC_DELIMITER in content:
        raise ValueError(f"skills[{i}].content must not contain {_SKILL_HEREDOC_DELIMITER!r}")


def _validate_skill_github(skill: dict, i: int) -> None:
    extra = set(skill) - _GITHUB_SKILL_KEYS
    if extra:
        raise ValueError(
            f"skills[{i}]: unknown keys {sorted(extra)!r}. "
            f"Allowed for github skills: {sorted(_GITHUB_SKILL_KEYS)}"
        )
    for field_name in _GITHUB_REQUIRED_KEYS:
        if field_name not in skill:
            raise ValueError(f"skills[{i}] missing required field: {field_name}")
        if not isinstance(skill[field_name], str):
            raise ValueError(f"skills[{i}].{field_name} must be a string")
    # `name` is optional for github; if present, must still be a string.
    if "name" in skill and not isinstance(skill["name"], str):
        raise ValueError(f"skills[{i}].name must be a string")
    if skill["type"] != "github":
        raise ValueError(f"skills[{i}].type {skill['type']!r} unsupported (only 'github')")
    if not _GITHUB_SOURCE_RE.match(skill["source"]):
        raise ValueError(f"skills[{i}].source {skill['source']!r} must be 'owner/repo'")


def _validate_skills(skills: list) -> list:
    if len(skills) > MAX_SKILLS_PER_AGENT:
        raise ValueError(f"Maximum {MAX_SKILLS_PER_AGENT} skills per agent")
    seen_dedup_keys: set[str] = set()
    for i, skill in enumerate(skills):
        if not isinstance(skill, dict):
            raise ValueError(f"skills[{i}] must be an object")

        # Discriminate by presence of `type`. Inline skills omit it.
        is_github = "type" in skill
        if is_github:
            _validate_skill_github(skill, i)
        else:
            _validate_skill_inline(skill, i)

        # Name regex applies whenever `name` is present. Github skills may
        # omit it (means: install every SKILL.md from the repo); inline
        # validation above already required it.
        if "name" in skill:
            name = skill["name"]
            if not _SKILL_NAME_RE.match(name):
                raise ValueError(f"skills[{i}].name {name!r} must match [a-z0-9][a-z0-9-]{{0,63}}")
            dedup_key = name
        else:
            # Whole-repo github install. Dedup on source so two "all skills
            # from owner/repo" entries collide, but a per-skill entry from
            # the same source can coexist (different dedup key).
            dedup_key = f"@github:{skill['source']}"

        if dedup_key in seen_dedup_keys:
            label = "name" if "name" in skill else "source"
            raise ValueError(f"skills[{i}]: duplicate {label} {dedup_key!r}")
        seen_dedup_keys.add(dedup_key)

        if len(skill["description"]) > MAX_SKILL_DESCRIPTION_LEN:
            raise ValueError(f"skills[{i}].description exceeds {MAX_SKILL_DESCRIPTION_LEN} chars")
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
    mcp_servers: list = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        if v not in MODELS:
            raise ValueError(f"Unknown model: {v}. Must be one of: {sorted(MODELS)}")
        return v

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
    mcp_servers: list | None = None
    metadata: dict | None = None

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str | None) -> str | None:
        if v is not None and v not in MODELS:
            raise ValueError(f"Unknown model: {v}. Must be one of: {sorted(MODELS)}")
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


def _check_runtime_model_compat(runtime_name: str, model_id: str) -> str | None:
    """Return an error message if model isn't servable by runtime, else None."""
    runtime = RUNTIMES[runtime_name]
    model = MODELS[model_id]
    if model.provider not in runtime.providers:
        return (
            f"Runtime {runtime_name} cannot serve model {model_id}: "
            f"provider {model.provider} not in {sorted(runtime.providers)}"
        )
    if model.runtimes is not None and runtime_name not in model.runtimes:
        return f"Model {model_id} not supported on runtime {runtime_name}"
    return None


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
        compat_err = _check_runtime_model_compat(req.runtime, req.model)
        if compat_err is not None:
            return JsonResponse({"detail": compat_err}, status=422)

        env_obj = None
        if req.environment_id:
            try:
                env_obj = Environment.objects.get(pk=req.environment_id, user=request.user)
            except (Environment.DoesNotExist, ValueError):
                return JsonResponse({"detail": "Environment not found"}, status=404)
            if env_obj.is_archived:
                return JsonResponse(
                    {"detail": "Cannot assign an archived environment to an agent"}, status=409
                )

        with transaction.atomic():
            agent = Agent.objects.create(
                user=request.user,
                name=req.name,
                description=req.description,
                system=req.system,
                model=req.model,
                runtime=req.runtime,
                environment=env_obj,
                skills=req.skills,
                mcp_servers=req.mcp_servers,
                metadata=req.metadata,
                version=1,
            )
            _snapshot_version(agent)

        with posthog.new_context():
            posthog.identify_context(str(request.user.id))
            posthog.capture(
                "agent.created",
                properties={
                    "agent_id": str(agent.id),
                    "runtime": agent.runtime,
                    "model": agent.model,
                    "has_environment": agent.environment_id is not None,
                    "system_length": len(agent.system or ""),
                    "description_length": len(agent.description or ""),
                    "skill_count": len(agent.skills or []),
                    "mcp_server_count": len(agent.mcp_servers or []),
                    "metadata_key_count": len(agent.metadata or {}),
                },
            )

        return JsonResponse(_serialize_agent(agent), status=201)

    elif request.method == "GET":
        qs = Agent.objects.filter(user=request.user, archived_at__isnull=True).order_by(
            "-created_at"
        )
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

        version_err = check_version_match(req.version, agent.version)
        if version_err is not None:
            return version_err

        if req.runtime is not None and req.runtime not in RUNTIMES:
            return JsonResponse(
                {"detail": f"Unknown runtime: {req.runtime}. Must be one of: {list(RUNTIMES)}"},
                status=400,
            )

        effective_runtime = req.runtime or agent.runtime
        effective_model = req.model or agent.model
        if effective_runtime in RUNTIMES and effective_model in MODELS:
            compat_err = _check_runtime_model_compat(effective_runtime, effective_model)
            if compat_err is not None:
                return JsonResponse({"detail": compat_err}, status=422)

        # Detect changes
        changed = False

        # Resolve environment_id only when it was explicitly included in the
        # request body. environment_id defaults to None in the schema, so we
        # must consult model_fields_set to distinguish "absent from payload"
        # from "explicitly set to null" (which clears the environment).
        if "environment_id" in req.model_fields_set:
            if req.environment_id is not None:
                try:
                    env_obj = Environment.objects.get(pk=req.environment_id, user=request.user)
                except (Environment.DoesNotExist, ValueError):
                    return JsonResponse({"detail": "Environment not found"}, status=404)
                if env_obj.is_archived:
                    return JsonResponse(
                        {"detail": "Cannot assign an archived environment to an agent"}, status=409
                    )
                if env_obj.id != agent.environment_id:
                    agent.environment = env_obj
                    changed = True
            elif agent.environment_id is not None:
                # Explicit null — detach the environment.
                agent.environment = None
                changed = True

        for field in ("name", "model", "runtime", "system", "description", "skills", "mcp_servers"):
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
            with transaction.atomic():
                agent.save()
                _snapshot_version(agent)
            with posthog.new_context():
                posthog.identify_context(str(request.user.id))
                posthog.capture(
                    "agent.updated",
                    properties={
                        "agent_id": str(agent.id),
                        "version": agent.version,
                        "runtime": agent.runtime,
                        "model": agent.model,
                    },
                )

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

    agent.archived_at = timezone.now()
    agent.save(update_fields=["archived_at", "updated_at"])

    with posthog.new_context():
        posthog.identify_context(str(request.user.id))
        posthog.capture("agent.archived", properties={"agent_id": str(agent.id)})

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
