import json
import re

import posthog
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from pydantic import BaseModel, Field, ValidationError, field_validator

from agent_on_demand.auth import require_api_key
from agent_on_demand.models import Environment, EnvironmentVersion


def _env_safe_props(env: Environment) -> dict:
    """Counts/flags only — never raw env_var values, names, or scripts."""
    return {
        "environment_id": str(env.id),
        "package_count": sum(len(pkgs) for pkgs in (env.packages or {}).values()),
        "package_managers": sorted((env.packages or {}).keys()),
        "env_var_count": len(env.env_vars or {}),
        "has_setup_script": bool((env.setup_script or "").strip()),
        "setup_script_length": len(env.setup_script or ""),
        "networking_type": env.networking_type,
        "allowed_hosts_count": len((env.networking_config or {}).get("allowed_hosts", [])),
    }


VALID_PACKAGE_MANAGERS = {"apt", "cargo", "gem", "go", "npm", "pip"}

# Valid POSIX shell variable names. Keys that don't match this will corrupt
# /tmp/aod-env when written as `KEY=value` shell assignments.
_ENV_VAR_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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

    @field_validator("env_vars")
    @classmethod
    def validate_env_vars(cls, v: dict) -> dict:
        for key in v:
            if not _ENV_VAR_KEY_RE.match(key):
                raise ValueError(f"Invalid env_var key {key!r}: must match [A-Za-z_][A-Za-z0-9_]*")
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

    @field_validator("env_vars")
    @classmethod
    def validate_env_vars(cls, v: dict | None) -> dict | None:
        if v is not None:
            for key in v:
                if not _ENV_VAR_KEY_RE.match(key):
                    raise ValueError(
                        f"Invalid env_var key {key!r}: must match [A-Za-z_][A-Za-z0-9_]*"
                    )
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

        try:
            with transaction.atomic():
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
        except IntegrityError:
            return JsonResponse(
                {"detail": f"An active environment named {req.name!r} already exists"},
                status=409,
            )

        with posthog.new_context():
            posthog.identify_context(str(request.user.id))
            posthog.capture("environment.created", properties=_env_safe_props(env))

        return JsonResponse(_serialize_environment(env), status=201)

    elif request.method == "GET":
        qs = Environment.objects.filter(user=request.user, archived_at__isnull=True).order_by(
            "-created_at"
        )
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
            try:
                with transaction.atomic():
                    env.save()
                    _snapshot_environment_version(env)
            except IntegrityError:
                return JsonResponse(
                    {"detail": f"An active environment named {env.name!r} already exists"},
                    status=409,
                )
            with posthog.new_context():
                posthog.identify_context(str(request.user.id))
                posthog.capture(
                    "environment.updated",
                    properties={**_env_safe_props(env), "version": env.version},
                )

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

    env.archived_at = timezone.now()
    env.save(update_fields=["archived_at", "updated_at"])

    with posthog.new_context():
        posthog.identify_context(str(request.user.id))
        posthog.capture(
            "environment.archived",
            properties={"environment_id": str(env.id)},
        )

    return JsonResponse(_serialize_environment(env))


@csrf_exempt
@require_api_key
def environment_delete(request, environment_id):
    """Delete an environment (only if no sessions reference it)."""
    if request.method != "DELETE":
        return JsonResponse({"detail": "Method not allowed"}, status=405)

    try:
        with transaction.atomic():
            # Lock the row so a concurrent POST /sessions cannot create a
            # session referencing this environment between the existence check
            # and the delete. Without the lock, the cascade would silently
            # NULL-out the new session's environment FK.
            env = Environment.objects.select_for_update().get(pk=environment_id, user=request.user)
            if env.sessions.exists():
                return JsonResponse(
                    {"detail": "Cannot delete environment with existing sessions"},
                    status=409,
                )
            env_id_str = str(env.id)
            env.delete()
    except (Environment.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Environment not found"}, status=404)

    with posthog.new_context():
        posthog.identify_context(str(request.user.id))
        posthog.capture("environment.deleted", properties={"environment_id": env_id_str})

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
