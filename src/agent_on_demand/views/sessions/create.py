import json
import uuid

import posthog
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from pydantic import ValidationError

from agent_on_demand import session_service
from agent_on_demand.auth import require_api_key
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

from .schemas import RunRequest
from .serializers import _serialize_resources, _serialize_session


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
