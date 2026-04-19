from __future__ import annotations

import re
import uuid

from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from pydantic import Field, field_validator

from agent_on_demand.models import Agent, AgentVersion, Environment
from agent_on_demand.runtimes import RUNTIMES, AgentModel


VALID_MCP_SERVER_TYPES = {"url", "stdio"}

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
            raise ValueError(f"skills[{i}].name {name!r} must match [a-z0-9][a-z0-9-]{{0,63}}")
        if name in seen_names:
            raise ValueError(f"skills[{i}]: duplicate name {name!r}")
        seen_names.add(name)

        if len(skill["description"]) > MAX_SKILL_DESCRIPTION_LEN:
            raise ValueError(f"skills[{i}].description exceeds {MAX_SKILL_DESCRIPTION_LEN} chars")

        content = skill["content"]
        if len(content.encode("utf-8")) > MAX_SKILL_CONTENT_BYTES:
            raise ValueError(f"skills[{i}].content exceeds {MAX_SKILL_CONTENT_BYTES} bytes")
        if _SKILL_HEREDOC_DELIMITER in content:
            raise ValueError(f"skills[{i}].content must not contain {_SKILL_HEREDOC_DELIMITER!r}")
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


def _validate_model(v: str) -> str:
    if v not in AgentModel.values():
        raise ValueError(f"Unknown model: {v}. Must be one of: {sorted(AgentModel.values())}")
    return v


class CreateAgentRequest(Schema):
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
    def _model(cls, v: str) -> str:
        return _validate_model(v)

    @field_validator("mcp_servers")
    @classmethod
    def _mcp(cls, v: list) -> list:
        return _validate_mcp_servers(v)

    @field_validator("skills")
    @classmethod
    def _skills(cls, v: list) -> list:
        return _validate_skills(v)


class UpdateAgentRequest(Schema):
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
    def _model(cls, v: str | None) -> str | None:
        if v is not None:
            _validate_model(v)
        return v

    @field_validator("mcp_servers")
    @classmethod
    def _mcp(cls, v: list | None) -> list | None:
        if v is not None:
            _validate_mcp_servers(v)
        return v

    @field_validator("skills")
    @classmethod
    def _skills(cls, v: list | None) -> list | None:
        if v is not None:
            _validate_skills(v)
        return v


class AgentOut(Schema):
    id: str
    type: str = "agent"
    name: str
    description: str | None
    system: str | None
    model: str
    runtime: str
    environment_id: str | None
    skills: list
    mcp_servers: list
    metadata: dict
    version: int
    created_at: str
    updated_at: str
    archived_at: str | None

    @staticmethod
    def from_model(agent: Agent) -> AgentOut:
        return AgentOut(
            id=str(agent.id),
            name=agent.name,
            description=agent.description or None,
            system=agent.system or None,
            model=agent.model,
            runtime=agent.runtime,
            environment_id=str(agent.environment_id) if agent.environment_id else None,
            skills=agent.skills,
            mcp_servers=agent.mcp_servers,
            metadata=agent.metadata,
            version=agent.version,
            created_at=agent.created_at.isoformat(),
            updated_at=agent.updated_at.isoformat(),
            archived_at=agent.archived_at.isoformat() if agent.archived_at else None,
        )


class AgentVersionOut(Schema):
    id: str
    type: str = "agent"
    name: str
    description: str | None
    system: str | None
    model: str
    runtime: str
    environment_id: str | None
    skills: list
    mcp_servers: list
    metadata: dict
    version: int
    created_at: str

    @staticmethod
    def from_model(av: AgentVersion) -> AgentVersionOut:
        return AgentVersionOut(
            id=str(av.agent_id),
            name=av.name,
            description=av.description or None,
            system=av.system or None,
            model=av.model,
            runtime=av.runtime,
            environment_id=str(av.environment_id) if av.environment_id else None,
            skills=av.skills,
            mcp_servers=av.mcp_servers,
            metadata=av.metadata,
            version=av.version,
            created_at=av.created_at.isoformat(),
        )


class AgentListOut(Schema):
    data: list[AgentOut]


class AgentVersionListOut(Schema):
    data: list[AgentVersionOut]


def _snapshot_version(agent: Agent) -> None:
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


def _get_agent(user, agent_id: uuid.UUID) -> Agent:
    try:
        return Agent.objects.get(pk=agent_id, user=user)
    except (Agent.DoesNotExist, ValueError):
        raise HttpError(404, "Agent not found")


def _resolve_environment(user, environment_id: str | None) -> Environment | None:
    if not environment_id:
        return None
    try:
        return Environment.objects.get(pk=environment_id, user=user)
    except (Environment.DoesNotExist, ValueError):
        raise HttpError(404, "Environment not found")


router = Router()


@router.get("/agents", response=AgentListOut)
def list_agents(request):
    qs = Agent.objects.filter(user=request.user, archived_at__isnull=True).order_by("-created_at")
    return {"data": [AgentOut.from_model(a) for a in qs]}


@router.post("/agents", response={201: AgentOut})
def create_agent(request, payload: CreateAgentRequest):
    if payload.runtime not in RUNTIMES:
        raise HttpError(
            400, f"Unknown runtime: {payload.runtime}. Must be one of: {list(RUNTIMES)}"
        )

    env_obj = _resolve_environment(request.user, payload.environment_id)

    agent = Agent.objects.create(
        user=request.user,
        name=payload.name,
        description=payload.description,
        system=payload.system,
        model=payload.model,
        runtime=payload.runtime,
        environment=env_obj,
        skills=payload.skills,
        mcp_servers=payload.mcp_servers,
        metadata=payload.metadata,
        version=1,
    )
    _snapshot_version(agent)
    return Status(201, AgentOut.from_model(agent))


@router.get("/agents/{agent_id}", response=AgentOut)
def get_agent(request, agent_id: uuid.UUID):
    return AgentOut.from_model(_get_agent(request.user, agent_id))


@router.put("/agents/{agent_id}", response=AgentOut)
def update_agent(request, agent_id: uuid.UUID, payload: UpdateAgentRequest):
    agent = _get_agent(request.user, agent_id)
    if agent.is_archived:
        raise HttpError(409, "Cannot update an archived agent")

    if payload.version != agent.version:
        raise HttpError(
            409, f"Version mismatch: expected {agent.version}, got {payload.version}"
        )

    if payload.runtime is not None and payload.runtime not in RUNTIMES:
        raise HttpError(
            400, f"Unknown runtime: {payload.runtime}. Must be one of: {list(RUNTIMES)}"
        )

    changed = False

    if payload.environment_id is not None:
        env_obj = _resolve_environment(request.user, payload.environment_id)
        if env_obj and env_obj.id != agent.environment_id:
            agent.environment = env_obj
            changed = True

    for field in ("name", "model", "runtime", "system", "description", "skills", "mcp_servers"):
        value = getattr(payload, field)
        if value is not None and value != getattr(agent, field):
            setattr(agent, field, value)
            changed = True

    if payload.metadata is not None:
        merged = dict(agent.metadata)
        for k, v in payload.metadata.items():
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

    return AgentOut.from_model(agent)


@router.post("/agents/{agent_id}/archive", response=AgentOut)
def archive_agent(request, agent_id: uuid.UUID):
    agent = _get_agent(request.user, agent_id)
    if agent.is_archived:
        raise HttpError(409, "Agent is already archived")
    agent.archived_at = timezone.now()
    agent.save(update_fields=["archived_at", "updated_at"])
    return AgentOut.from_model(agent)


@router.get("/agents/{agent_id}/versions", response=AgentVersionListOut)
def list_agent_versions(request, agent_id: uuid.UUID):
    agent = _get_agent(request.user, agent_id)
    versions = AgentVersion.objects.filter(agent=agent).order_by("-version")
    return {"data": [AgentVersionOut.from_model(av) for av in versions]}
