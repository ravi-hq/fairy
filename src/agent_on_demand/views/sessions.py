import json
import uuid
from typing import Literal

import posthog
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from pydantic import BaseModel, Field, ValidationError, field_validator

from agent_on_demand import session_service
from agent_on_demand.auth import require_api_key
from agent_on_demand.github_resource_validation import (
    resolved_mount_path,
    validate_github_url,
    validate_mount_path,
    validate_resources_count_and_dedup,
)
from agent_on_demand.models import (
    Agent,
    AgentSession,
    Environment,
    SessionResource,
    SessionTurn,
    UserQuota,
)
from agent_on_demand.models.auth import CREDENTIAL_ENV_VAR, UserCredential
from agent_on_demand.models_catalog import MODELS
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.session_state import (
    check_can_accept_prompt,
    check_can_delete,
    check_can_terminate,
)
from agent_on_demand.stream import stream_session_from_db


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
        "turn_count": session.turns.count(),
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
    def _validate_url(cls, v: str) -> str:
        return validate_github_url(v)

    @field_validator("mount_path")
    @classmethod
    def _validate_mount_path(cls, v: str | None) -> str | None:
        return validate_mount_path(v)

    def resolved_mount_path(self) -> str:
        return resolved_mount_path(self.url, self.mount_path)


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
    def _validate_resources(cls, v: list[GitHubRepoResource]) -> list[GitHubRepoResource]:
        validate_resources_count_and_dedup([r.resolved_mount_path() for r in v])
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

    # Ensure the user has registered at least one credential that this runtime
    # can authenticate with. A runtime accepts either a provider credential
    # (e.g. `provider:anthropic` for Claude's Anthropic API) or a
    # runtime-specific token (e.g. `runtime_token:claude-oauth`).
    runtime_obj = RUNTIMES[runtime]
    accepted_kinds = {f"provider:{p}" for p in runtime_obj.providers}
    accepted_kinds |= {
        kind for kind in CREDENTIAL_ENV_VAR if kind.startswith(f"runtime_token:{runtime}")
    }
    has_credential = UserCredential.objects.filter(
        user=request.user, kind__in=accepted_kinds
    ).exists()
    if not has_credential:
        return JsonResponse(
            {"detail": f"No API key configured for runtime: {runtime}"},
            status=400,
        )

    # Agent's model must be known and servable by the agent's runtime.
    # An unknown model is rejected immediately rather than silently skipping
    # the provider compatibility check and failing later at provision time.
    if agent_obj.model not in MODELS:
        return JsonResponse(
            {"detail": f"Unknown model: {agent_obj.model}"},
            status=422,
        )
    model = MODELS[agent_obj.model]
    if model.provider not in runtime_obj.providers:
        return JsonResponse(
            {
                "detail": (
                    f"Runtime {runtime} cannot serve model {agent_obj.model}: "
                    f"provider {model.provider} not in {sorted(runtime_obj.providers)}"
                )
            },
            status=422,
        )

    # Sync pre-check so missing Sprites creds return 400 immediately rather
    # than surfacing as a failed session the client has to poll for.
    if session_service.get_client(request.user) is None:
        return JsonResponse({"detail": "No Sprites API key configured"}, status=400)

    name = f"{settings.SPRITE_NAME_PREFIX}-{uuid.uuid4().hex[:12]}"
    runtime_session_id = str(uuid.uuid4())

    # First-turn prompt gets the agent's system prepended. Subsequent turns
    # inherit it via the runtime CLI's own --continue/--resume state.
    effective_prompt = req.prompt
    if agent_obj.system:
        effective_prompt = f"{agent_obj.system}\n\n{req.prompt}"

    with transaction.atomic():
        # Re-check the concurrent session quota inside a transaction, locking
        # the UserQuota row so two simultaneous requests cannot both pass the
        # pre-check above and each create a session, silently exceeding the limit.
        # We read locked_max directly from the locked row rather than going
        # through user.quota (which Django caches on the instance and may
        # reflect state from before the get_or_create below).
        locked_quota, _ = UserQuota.objects.get_or_create(user=request.user)
        locked_quota = UserQuota.objects.select_for_update().get(pk=locked_quota.pk)
        locked_max = (
            locked_quota.max_concurrent_sessions or settings.DEFAULT_MAX_CONCURRENT_SESSIONS
        )
        locked_count = UserQuota.active_session_count_for(request.user)
        if locked_count >= locked_max:
            return JsonResponse(
                {
                    "detail": (
                        f"Concurrent session limit reached ({locked_count}/{locked_max}). "
                        "Terminate an active session before starting a new one."
                    ),
                    "limit": locked_max,
                    "active": locked_count,
                },
                status=429,
            )
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

        session_service.provision_session_task.defer(
            session_id=str(session.id),
            turn_id=turn.id,
            prompt=effective_prompt,
            mode="run",
            timeout=float(req.timeout),
        )

    with posthog.new_context():
        posthog.identify_context(str(request.user.id))
        posthog.capture(
            "session.created",
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
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    return JsonResponse(_serialize_session(session))


@require_GET
@require_api_key
async def stream_session(request, session_id):
    """Stream session logs via SSE (live tail + full replay)."""
    try:
        session = await AgentSession.objects.aget(pk=session_id, user=request.user)
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    raw = request.META.get("HTTP_LAST_EVENT_ID") or request.GET.get("since", "0")
    try:
        since = max(0, int(raw))
    except ValueError:
        return JsonResponse({"detail": "since must be an integer"}, status=400)

    async def event_generator():
        yield f"data: {json.dumps({'type': 'start', 'runtime': session.runtime, 'session_id': str(session.id)})}\n\n"

        async for event in stream_session_from_db(str(session.id), since=since):
            if event == "":
                yield ": heartbeat\n\n"
            else:
                payload = json.loads(event)
                log_id = payload.get("id")
                # turn_start is a synthetic event derived from the same DB row
                # as the output event that follows it. Advancing the SSE cursor
                # on turn_start would cause the output event (same id) to be
                # skipped on reconnect. Only real log row events advance it.
                if log_id and payload.get("type") != "turn_start":
                    yield f"id: {log_id}\n"
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
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    # A `failed` session may have left its Sprite mid-execution (see Muddy
    # Zone 8 in thoughts/research/2026-04-18-sprites-script-setup.md). Making
    # `failed` terminal prevents a resume from colliding with a runaway turn.
    err = check_can_accept_prompt(session.status)
    if err is not None:
        return err

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
    except session_service.SessionHandleNotFound:
        # The Sprite is gone (e.g. idle timeout on the Sprites platform). The
        # session record still exists, so 404 would be misleading — callers
        # cannot distinguish "session not found" from "Sprite no longer
        # available". Return 409 with an actionable message instead.
        return JsonResponse(
            {"detail": "Session sprite is no longer available; start a new session."},
            status=409,
        )

    # Atomically lock the session row, re-check state, allocate a turn number,
    # and enqueue the worker task. All four steps are inside the same
    # transaction so a task is never deferred without its turn row committed,
    # and a turn row is never committed without a task enqueued to drive it.
    try:
        with transaction.atomic():
            locked = AgentSession.objects.select_for_update().get(pk=session.id)
            err = check_can_accept_prompt(locked.status)
            if err is not None:
                return err
            # In addition to the pre-lock checks (which allow pending — that's
            # the initial state of a fresh session), reject pending here: if
            # status flipped to pending between the two checks, another
            # send_prompt raced in and already enqueued a turn.
            if locked.status == "pending":
                return JsonResponse({"detail": "Session already has a pending turn"}, status=409)

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
            session_service.run_turn(
                session, turn, req.prompt, "continue", float(req.timeout)
            )
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    with posthog.new_context():
        posthog.identify_context(str(request.user.id))
        posthog.capture(
            "session.prompt_sent",
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
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    turns = session.turns.order_by("turn_number")
    return JsonResponse({"data": [_serialize_turn(t) for t in turns]})


@csrf_exempt
@require_POST
@require_api_key
def terminate_session(request, session_id):
    """Terminate a session's Sprite without deleting the session record."""
    try:
        with transaction.atomic():
            session = AgentSession.objects.select_for_update().get(pk=session_id, user=request.user)
            err = check_can_terminate(session.status)
            if err is not None:
                return err
            sprite_name = session.sprite_name
            session.status = "terminated"
            session.sprite_name = ""
            session.save(update_fields=["status", "sprite_name", "updated_at"])
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    if sprite_name:
        session_service.destroy_session_task.defer(user_id=request.user.id, sprite_name=sprite_name)

    with posthog.new_context():
        posthog.identify_context(str(request.user.id))
        posthog.capture(
            "session.terminated",
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
        with transaction.atomic():
            session = AgentSession.objects.select_for_update().get(pk=session_id, user=request.user)
            err = check_can_delete(session.status)
            if err is not None:
                return err
            session_id_str = str(session.id)
            session.delete()  # pre_delete signal handles Sprite cleanup
    except AgentSession.DoesNotExist:
        return JsonResponse({"detail": "Session not found"}, status=404)

    with posthog.new_context():
        posthog.identify_context(str(request.user.id))
        posthog.capture(
            "session.deleted",
            properties={"session_id": session_id_str},
        )

    return JsonResponse({"detail": "Session deleted"}, status=200)
