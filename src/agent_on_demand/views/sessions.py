from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Literal

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.http import StreamingHttpResponse
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from pydantic import Field, field_validator

from agent_on_demand import session_service
from agent_on_demand.models import (
    Agent,
    AgentSession,
    Environment,
    SessionResource,
    SessionTurn,
    UserRuntimeKey,
)
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.sprites_exec import (
    EnvironmentSetup,
    McpServerSpec,
    RepoSpec,
    SkillSpec,
    build_wrapper_script,
)
from agent_on_demand.stream import stream_session_from_db

logger = logging.getLogger(__name__)


def _get_runtime_key(user, runtime: str) -> str | None:
    try:
        urk = UserRuntimeKey.objects.get(user=user, runtime=runtime)
        return urk.get_api_key()
    except UserRuntimeKey.DoesNotExist:
        return None


def _mcp_servers_to_specs(mcp_servers: list[dict]) -> list[McpServerSpec]:
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
    return [SkillSpec(name=s["name"], content=s["content"]) for s in skills]


def _resources_to_repo_specs(resources: list[GitHubRepoResource]) -> list[RepoSpec]:
    return [
        RepoSpec(
            url=r.url,
            mount_path=r.resolved_mount_path(),
            token=r.authorization_token,
        )
        for r in resources
    ]


class SessionResourceOut(Schema):
    type: str
    url: str
    mount_path: str

    @staticmethod
    def from_model(sr: SessionResource) -> SessionResourceOut:
        return SessionResourceOut(
            type=sr.resource_type,
            url=sr.url,
            mount_path=sr.mount_path,
        )


class SessionOut(Schema):
    id: str
    agent_id: str | None
    environment_id: str | None
    runtime: str
    status: str
    exit_code: int | None
    created_at: str
    updated_at: str
    resources: list[SessionResourceOut]
    turn_count: int
    current_turn: int | None

    @staticmethod
    def from_model(session: AgentSession) -> SessionOut:
        latest = session.turns.order_by("-turn_number").first()
        turn_count = latest.turn_number if latest else 0
        return SessionOut(
            id=str(session.id),
            agent_id=str(session.agent_id) if session.agent_id else None,
            environment_id=str(session.environment_id) if session.environment_id else None,
            runtime=session.runtime,
            status=session.status,
            exit_code=session.exit_code,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            resources=[SessionResourceOut.from_model(sr) for sr in session.resources.all()],
            turn_count=turn_count,
            current_turn=latest.turn_number if latest else None,
        )


class SessionTurnOut(Schema):
    turn_number: int
    prompt: str
    status: str
    exit_code: int | None
    created_at: str
    started_at: str | None
    ended_at: str | None

    @staticmethod
    def from_model(turn: SessionTurn) -> SessionTurnOut:
        return SessionTurnOut(
            turn_number=turn.turn_number,
            prompt=turn.prompt,
            status=turn.status,
            exit_code=turn.exit_code,
            created_at=turn.created_at.isoformat(),
            started_at=turn.started_at.isoformat() if turn.started_at else None,
            ended_at=turn.ended_at.isoformat() if turn.ended_at else None,
        )


class SessionListOut(Schema):
    data: list[SessionOut]


class SessionTurnListOut(Schema):
    data: list[SessionTurnOut]


class SessionCreatedOut(Schema):
    id: str
    status: str
    stream_url: str
    environment_id: str | None
    resources: list[SessionResourceOut]
    current_turn: int


class PromptAcceptedOut(Schema):
    id: str
    status: str
    stream_url: str
    current_turn: int


class SessionTerminatedOut(Schema):
    id: str
    status: str


class DetailOut(Schema):
    detail: str


class GitHubRepoResource(Schema):
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


class RunRequest(Schema):
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


class PromptRequest(Schema):
    prompt: str = Field(description="The prompt to send to the agent")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")


def _get_session(user, session_id: uuid.UUID) -> AgentSession:
    try:
        return AgentSession.objects.get(pk=session_id, user=user)
    except (AgentSession.DoesNotExist, ValueError):
        raise HttpError(404, "Session not found")


router = Router()


@router.get("/sessions", response=SessionListOut)
def list_sessions(request):
    qs = (
        AgentSession.objects.filter(user=request.user)
        .prefetch_related("resources")
        .order_by("-created_at")
    )
    return {"data": [SessionOut.from_model(s) for s in qs]}


@router.post("/sessions", response={202: SessionCreatedOut})
def create_session(request, payload: RunRequest):
    try:
        agent_obj = Agent.objects.get(pk=payload.agent_id, user=request.user)
    except (Agent.DoesNotExist, ValueError):
        raise HttpError(404, "Agent not found")
    if agent_obj.is_archived:
        raise HttpError(409, "Cannot create session with archived agent")

    environment_obj = None
    env_id = payload.environment_id or agent_obj.environment_id
    if env_id:
        try:
            environment_obj = Environment.objects.get(pk=env_id, user=request.user)
        except (Environment.DoesNotExist, ValueError):
            raise HttpError(404, "Environment not found")
        if environment_obj.is_archived:
            raise HttpError(409, "Cannot create session with archived environment")

    runtime = agent_obj.runtime
    if runtime not in RUNTIMES:
        raise HttpError(400, f"Unknown runtime: {runtime}. Must be one of: {list(RUNTIMES)}")

    api_key = _get_runtime_key(request.user, runtime)
    if api_key is None:
        raise HttpError(400, f"No API key configured for runtime: {runtime}")

    config = RUNTIMES[runtime]
    name = f"{settings.SPRITE_NAME_PREFIX}-{uuid.uuid4().hex[:12]}"
    runtime_session_id = str(uuid.uuid4())

    # First-turn prompt gets the agent's system prepended. Subsequent turns
    # inherit it via the runtime CLI's own --continue/--resume state.
    effective_prompt = payload.prompt
    if agent_obj.system:
        effective_prompt = f"{agent_obj.system}\n\n{payload.prompt}"

    env_setup = None
    if environment_obj:
        env_setup = EnvironmentSetup(
            packages=environment_obj.packages,
            env_vars=environment_obj.env_vars,
            setup_script=environment_obj.setup_script,
        )

    script = build_wrapper_script(
        config,
        api_key,
        runtime_session_id=runtime_session_id,
        repos=_resources_to_repo_specs(payload.resources),
        environment=env_setup,
        mcp_servers=_mcp_servers_to_specs(agent_obj.mcp_servers),
        skills=_skills_to_specs(agent_obj.skills),
    )

    try:
        sprite = session_service.provision_session(
            request.user,
            name=name,
            environment=environment_obj,
            wrapper_script=script,
            prompt=effective_prompt,
        )
    except session_service.NoSpritesKeyError as e:
        raise HttpError(400, str(e))
    except session_service.ProvisionError as e:
        raise HttpError(502, str(e))

    with transaction.atomic():
        session = AgentSession.objects.create(
            user=request.user,
            agent=agent_obj,
            environment=environment_obj,
            runtime=runtime,
            prompt=payload.prompt,
            sprite_name=name,
            runtime_session_id=runtime_session_id,
            status="pending",
        )
        turn = SessionTurn.objects.create(
            session=session,
            turn_number=1,
            prompt=payload.prompt,
            status="pending",
        )

        for resource in payload.resources:
            sr = SessionResource(
                session=session,
                resource_type=resource.type,
                url=resource.url,
                mount_path=resource.resolved_mount_path(),
            )
            if resource.authorization_token:
                sr.set_token(resource.authorization_token)
            sr.save()

    session_service.start_turn(session, turn, sprite, "run", float(payload.timeout))

    return Status(
        202,
        SessionCreatedOut(
            id=str(session.id),
            status="pending",
            stream_url=f"/sessions/{session.id}/stream",
            environment_id=str(session.environment_id) if session.environment_id else None,
            resources=[SessionResourceOut.from_model(sr) for sr in session.resources.all()],
            current_turn=turn.turn_number,
        ),
    )


@router.get("/sessions/{session_id}", response=SessionOut)
def get_session(request, session_id: uuid.UUID):
    return SessionOut.from_model(_get_session(request.user, session_id))


@router.get("/sessions/{session_id}/stream")
def stream_session(request, session_id: uuid.UUID):
    session = _get_session(request.user, session_id)

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


@router.post("/sessions/{session_id}/prompt", response={202: PromptAcceptedOut})
def send_prompt(request, session_id: uuid.UUID, payload: PromptRequest):
    session = _get_session(request.user, session_id)

    if session.status == "running":
        raise HttpError(409, "Session is already running")
    if session.status == "terminated":
        raise HttpError(409, "Session has been terminated")

    try:
        sprite = session_service.resume_session(request.user, session.sprite_name)
    except session_service.NoSpritesKeyError as e:
        raise HttpError(400, str(e))
    except session_service.SessionHandleNotFound as e:
        raise HttpError(404, str(e))

    # Atomically lock the session row, re-check state, and allocate a turn
    # number. Prevents two concurrent POSTs from both creating turn N+1 or
    # transitioning the session to running.
    try:
        with transaction.atomic():
            locked = AgentSession.objects.select_for_update().get(pk=session.id)
            if locked.status == "running":
                raise HttpError(409, "Session is already running")
            if locked.status == "terminated":
                raise HttpError(409, "Session has been terminated")

            next_turn_number = (
                SessionTurn.objects.filter(session=locked).aggregate(n=Max("turn_number"))["n"] or 0
            ) + 1
            turn = SessionTurn.objects.create(
                session=locked,
                turn_number=next_turn_number,
                prompt=payload.prompt,
                status="pending",
            )
            locked.prompt = payload.prompt
            locked.status = "pending"
            locked.exit_code = None
            locked.save(update_fields=["prompt", "status", "exit_code", "updated_at"])
            session = locked
    except AgentSession.DoesNotExist:
        raise HttpError(404, "Session not found")

    try:
        session_service.write_prompt(sprite, payload.prompt)
    except session_service.ProvisionError as e:
        raise HttpError(502, str(e))

    session_service.start_turn(session, turn, sprite, "continue", float(payload.timeout))

    return Status(
        202,
        PromptAcceptedOut(
            id=str(session.id),
            status="pending",
            stream_url=f"/sessions/{session.id}/stream",
            current_turn=turn.turn_number,
        ),
    )


@router.get("/sessions/{session_id}/turns", response=SessionTurnListOut)
def list_session_turns(request, session_id: uuid.UUID):
    session = _get_session(request.user, session_id)
    turns = session.turns.order_by("turn_number")
    return {"data": [SessionTurnOut.from_model(t) for t in turns]}


@router.post("/sessions/{session_id}/terminate", response=SessionTerminatedOut)
def terminate_session(request, session_id: uuid.UUID):
    session = _get_session(request.user, session_id)
    if session.status == "terminated":
        raise HttpError(409, "Session is already terminated")

    session_service.destroy_session(request.user, session.sprite_name)

    session.status = "terminated"
    session.sprite_name = ""
    session.save(update_fields=["status", "sprite_name", "updated_at"])

    return SessionTerminatedOut(id=str(session.id), status="terminated")


@router.delete("/sessions/{session_id}/delete", response=DetailOut)
def delete_session(request, session_id: uuid.UUID):
    session = _get_session(request.user, session_id)
    if session.status == "running":
        raise HttpError(409, "Cannot delete a running session")
    session.delete()  # pre_delete signal handles Sprite cleanup
    return DetailOut(detail="Session deleted")
