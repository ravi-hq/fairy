from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from pydantic import BaseModel, Field, field_validator

from agent_on_demand.analytics import capture as posthog_capture
from agent_on_demand.auth import require_api_key
from agent_on_demand.models import Agent, AgentVersion, Environment
from agent_on_demand.models_catalog import MODELS
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.validation.mcp_server_validation import (
    validate_mcp_servers as _validate_mcp_servers,
)
from agent_on_demand.validation.metadata_merge import merge_metadata
from agent_on_demand.validation.runtime_model_compat import check_runtime_model_compat
from agent_on_demand.validation.skill_validation import validate_skills as _validate_skills
from agent_on_demand.versioning import check_version_match
from agent_on_demand.views._helpers import parse_request_body


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
        req, err = parse_request_body(request, CreateAgentRequest)
        if err is not None:
            return err

        if req.runtime not in RUNTIMES:
            return JsonResponse(
                {"detail": f"Unknown runtime: {req.runtime}. Must be one of: {list(RUNTIMES)}"},
                status=400,
            )
        compat_err = check_runtime_model_compat(RUNTIMES[req.runtime], MODELS[req.model])
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

        posthog_capture(
            request.user,
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

    if request.method == "GET":
        qs = Agent.objects.filter(user=request.user, archived_at__isnull=True).order_by(
            "-created_at"
        )
        return JsonResponse({"data": [_serialize_agent(a) for a in qs]})

    return JsonResponse({"detail": "Method not allowed"}, status=405)


@csrf_exempt
@require_api_key
def agent_detail(request, agent_id):
    """GET: retrieve agent. PUT: update agent."""
    if request.method == "GET":
        try:
            agent = Agent.objects.get(pk=agent_id, user=request.user)
        except Agent.DoesNotExist:
            return JsonResponse({"detail": "Agent not found"}, status=404)
        return JsonResponse(_serialize_agent(agent))

    if request.method == "PUT":
        req, err = parse_request_body(request, UpdateAgentRequest)
        if err is not None:
            return err

        if req.runtime is not None and req.runtime not in RUNTIMES:
            return JsonResponse(
                {"detail": f"Unknown runtime: {req.runtime}. Must be one of: {list(RUNTIMES)}"},
                status=400,
            )

        # Resolve environment before acquiring the row lock (fast 404 path).
        # Use model_fields_set to distinguish "absent from payload" from
        # "explicitly set to null" (which clears the environment).
        env_obj = None
        env_id_provided = "environment_id" in req.model_fields_set

        if env_id_provided and req.environment_id is not None:
            try:
                env_obj = Environment.objects.get(pk=req.environment_id, user=request.user)
            except (Environment.DoesNotExist, ValueError):
                return JsonResponse({"detail": "Environment not found"}, status=404)
            if env_obj.is_archived:
                return JsonResponse(
                    {"detail": "Cannot assign an archived environment to an agent"}, status=409
                )

        changed = False
        with transaction.atomic():
            try:
                agent = Agent.objects.select_for_update().get(pk=agent_id, user=request.user)
            except (Agent.DoesNotExist, ValueError):
                return JsonResponse({"detail": "Agent not found"}, status=404)

            if agent.is_archived:
                return JsonResponse({"detail": "Cannot update an archived agent"}, status=409)

            version_err = check_version_match(req.version, agent.version)
            if version_err is not None:
                return version_err

            effective_runtime = req.runtime or agent.runtime
            effective_model = req.model or agent.model
            if effective_runtime in RUNTIMES and effective_model in MODELS:
                compat_err = check_runtime_model_compat(
                    RUNTIMES[effective_runtime], MODELS[effective_model]
                )
                if compat_err is not None:
                    return JsonResponse({"detail": compat_err}, status=422)

            if env_id_provided:
                if req.environment_id is not None:
                    if env_obj.id != agent.environment_id:
                        agent.environment = env_obj
                        changed = True
                elif agent.environment_id is not None:
                    # Explicit null — detach the environment.
                    agent.environment = None
                    changed = True

            for field in (
                "name",
                "model",
                "runtime",
                "system",
                "description",
                "skills",
                "mcp_servers",
            ):
                value = getattr(req, field)
                if value is not None and value != getattr(agent, field):
                    setattr(agent, field, value)
                    changed = True

            # Metadata merges at key level (matching Anthropic semantics).
            # See agent_on_demand.validation.metadata_merge for the contract — pinned by
            # mutation testing because the "" → delete branch is the kind of
            # thing a refactor can quietly drop.
            if req.metadata is not None:
                merged = merge_metadata(agent.metadata, req.metadata)
                if merged != agent.metadata:
                    agent.metadata = merged
                    changed = True

            if changed:
                agent.version += 1
                agent.save(
                    update_fields=[
                        "name",
                        "description",
                        "system",
                        "model",
                        "runtime",
                        "environment_id",
                        "skills",
                        "mcp_servers",
                        "metadata",
                        "version",
                        "updated_at",
                    ]
                )
                _snapshot_version(agent)

        if changed:
            posthog_capture(
                request.user,
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
    except Agent.DoesNotExist:
        return JsonResponse({"detail": "Agent not found"}, status=404)

    if agent.is_archived:
        return JsonResponse({"detail": "Agent is already archived"}, status=409)

    agent.archived_at = timezone.now()
    agent.save(update_fields=["archived_at", "updated_at"])

    posthog_capture(request.user, "agent.archived", properties={"agent_id": str(agent.id)})

    return JsonResponse(_serialize_agent(agent))


@require_GET
@require_api_key
def agent_versions(request, agent_id):
    """List all versions of an agent."""
    try:
        agent = Agent.objects.get(pk=agent_id, user=request.user)
    except Agent.DoesNotExist:
        return JsonResponse({"detail": "Agent not found"}, status=404)

    versions = AgentVersion.objects.filter(agent=agent).order_by("-version")
    return JsonResponse({"data": [_serialize_agent_version(av) for av in versions]})
