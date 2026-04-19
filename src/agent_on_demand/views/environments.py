from __future__ import annotations

import uuid
from typing import Any

from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.responses import Status
from pydantic import Field, field_validator

from agent_on_demand.models import Environment, EnvironmentVersion


VALID_PACKAGE_MANAGERS = {"apt", "cargo", "gem", "go", "npm", "pip"}


def _validate_packages(v: dict) -> dict:
    for manager, pkgs in v.items():
        if manager not in VALID_PACKAGE_MANAGERS:
            raise ValueError(
                f"Unknown package manager: {manager}. "
                f"Must be one of: {sorted(VALID_PACKAGE_MANAGERS)}"
            )
        if not isinstance(pkgs, list) or not all(isinstance(p, str) for p in pkgs):
            raise ValueError(f"packages.{manager} must be a list of strings")
    return v


def _validate_networking(v: dict) -> dict:
    net_type = v.get("type", "unrestricted")
    if net_type not in ("unrestricted", "limited"):
        raise ValueError("networking.type must be 'unrestricted' or 'limited'")
    if net_type == "limited":
        hosts = v.get("allowed_hosts", [])
        if not isinstance(hosts, list):
            raise ValueError("networking.allowed_hosts must be a list")
    return v


class CreateEnvironmentRequest(Schema):
    name: str = Field(max_length=200)
    packages: dict[str, list[str]] = Field(default_factory=dict)
    env_vars: dict[str, str] = Field(default_factory=dict)
    setup_script: str = Field(default="")
    networking: dict = Field(default_factory=lambda: {"type": "unrestricted"})

    @field_validator("packages")
    @classmethod
    def _packages(cls, v: dict) -> dict:
        return _validate_packages(v)

    @field_validator("networking")
    @classmethod
    def _networking(cls, v: dict) -> dict:
        return _validate_networking(v)


class UpdateEnvironmentRequest(Schema):
    version: int = Field(description="Current version — optimistic concurrency check")
    name: str | None = None
    packages: dict[str, list[str]] | None = None
    env_vars: dict[str, str] | None = None
    setup_script: str | None = None
    networking: dict | None = None

    @field_validator("packages")
    @classmethod
    def _packages(cls, v: dict | None) -> dict | None:
        if v is not None:
            _validate_packages(v)
        return v

    @field_validator("networking")
    @classmethod
    def _networking(cls, v: dict | None) -> dict | None:
        if v is not None:
            _validate_networking(v)
        return v


class EnvironmentOut(Schema):
    id: str
    type: str = "environment"
    name: str
    packages: dict
    setup_script: str | None
    networking: dict
    version: int
    created_at: str
    updated_at: str
    archived_at: str | None

    @staticmethod
    def from_model(env: Environment) -> EnvironmentOut:
        networking: dict[str, Any] = {"type": env.networking_type}
        if env.networking_type == "limited" and env.networking_config:
            networking.update(env.networking_config)
        return EnvironmentOut(
            id=str(env.id),
            name=env.name,
            packages=env.packages,
            setup_script=env.setup_script or None,
            networking=networking,
            version=env.version,
            created_at=env.created_at.isoformat(),
            updated_at=env.updated_at.isoformat(),
            archived_at=env.archived_at.isoformat() if env.archived_at else None,
        )


class EnvironmentVersionOut(Schema):
    id: str
    type: str = "environment"
    name: str
    packages: dict
    setup_script: str | None
    networking: dict
    version: int
    created_at: str

    @staticmethod
    def from_model(ev: EnvironmentVersion) -> EnvironmentVersionOut:
        networking: dict[str, Any] = {"type": ev.networking_type}
        if ev.networking_type == "limited" and ev.networking_config:
            networking.update(ev.networking_config)
        return EnvironmentVersionOut(
            id=str(ev.environment_id),
            name=ev.name,
            packages=ev.packages,
            setup_script=ev.setup_script or None,
            networking=networking,
            version=ev.version,
            created_at=ev.created_at.isoformat(),
        )


class EnvironmentListOut(Schema):
    data: list[EnvironmentOut]


class EnvironmentVersionListOut(Schema):
    data: list[EnvironmentVersionOut]


def _snapshot_environment_version(env: Environment) -> None:
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


def _get_env(user, environment_id: uuid.UUID) -> Environment:
    try:
        return Environment.objects.get(pk=environment_id, user=user)
    except (Environment.DoesNotExist, ValueError):
        raise HttpError(404, "Environment not found")


router = Router()


@router.get("/environments", response=EnvironmentListOut)
def list_environments(request):
    qs = Environment.objects.filter(
        user=request.user, archived_at__isnull=True
    ).order_by("-created_at")
    return {"data": [EnvironmentOut.from_model(e) for e in qs]}


@router.post("/environments", response={201: EnvironmentOut})
def create_environment(request, payload: CreateEnvironmentRequest):
    networking_type = payload.networking.get("type", "unrestricted")
    networking_config = {k: v for k, v in payload.networking.items() if k != "type"}

    env = Environment.objects.create(
        user=request.user,
        name=payload.name,
        packages=payload.packages,
        env_vars=payload.env_vars,
        setup_script=payload.setup_script,
        networking_type=networking_type,
        networking_config=networking_config,
        version=1,
    )
    _snapshot_environment_version(env)
    return Status(201, EnvironmentOut.from_model(env))


@router.get("/environments/{environment_id}", response=EnvironmentOut)
def get_environment(request, environment_id: uuid.UUID):
    return EnvironmentOut.from_model(_get_env(request.user, environment_id))


@router.put("/environments/{environment_id}", response=EnvironmentOut)
def update_environment(request, environment_id: uuid.UUID, payload: UpdateEnvironmentRequest):
    env = _get_env(request.user, environment_id)
    if env.is_archived:
        raise HttpError(409, "Cannot update an archived environment")

    if payload.version != env.version:
        raise HttpError(
            409, f"Version mismatch: expected {env.version}, got {payload.version}"
        )

    changed = False
    if payload.name is not None and payload.name != env.name:
        env.name = payload.name
        changed = True
    if payload.packages is not None and payload.packages != env.packages:
        env.packages = payload.packages
        changed = True
    if payload.env_vars is not None and payload.env_vars != env.env_vars:
        env.env_vars = payload.env_vars
        changed = True
    if payload.setup_script is not None and payload.setup_script != env.setup_script:
        env.setup_script = payload.setup_script
        changed = True

    if payload.networking is not None:
        new_type = payload.networking.get("type", "unrestricted")
        new_config = {k: v for k, v in payload.networking.items() if k != "type"}
        if new_type != env.networking_type or new_config != env.networking_config:
            env.networking_type = new_type
            env.networking_config = new_config
            changed = True

    if changed:
        env.version += 1
        env.save()
        _snapshot_environment_version(env)

    return EnvironmentOut.from_model(env)


@router.post("/environments/{environment_id}/archive", response=EnvironmentOut)
def archive_environment(request, environment_id: uuid.UUID):
    env = _get_env(request.user, environment_id)
    if env.is_archived:
        raise HttpError(409, "Environment is already archived")
    env.archived_at = timezone.now()
    env.save(update_fields=["archived_at", "updated_at"])
    return EnvironmentOut.from_model(env)


@router.delete("/environments/{environment_id}/delete", response={200: dict})
def delete_environment(request, environment_id: uuid.UUID):
    env = _get_env(request.user, environment_id)
    if env.sessions.exists():
        raise HttpError(409, "Cannot delete environment with existing sessions")
    env.delete()
    return Status(200, {"detail": "Environment deleted"})


@router.get("/environments/{environment_id}/versions", response=EnvironmentVersionListOut)
def list_environment_versions(request, environment_id: uuid.UUID):
    env = _get_env(request.user, environment_id)
    versions = EnvironmentVersion.objects.filter(environment=env).order_by("-version")
    return {"data": [EnvironmentVersionOut.from_model(ev) for ev in versions]}
