from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


SessionStatus = Literal["pending", "running", "completed", "failed", "terminated"]
TurnStatus = Literal["pending", "running", "completed", "failed"]
NetworkingType = Literal["unrestricted", "limited"]
PackageManager = Literal["apt", "cargo", "gem", "go", "npm", "pip"]


class McpServer(_Model):
    name: str
    type: Literal["url", "stdio"]
    url: str | None = None
    command: str | None = None


class Networking(_Model):
    type: NetworkingType
    allowed_hosts: list[str] = Field(default_factory=list)


class SessionResource(_Model):
    """A GitHub repo cloned into the Sprite for a session.

    Tokens supplied on creation are never returned on any response.
    """

    type: Literal["github_repository"]
    url: str
    mount_path: str | None = None


# Mirrors `github_resource_validation.py` on the server.
GITHUB_URL_RE = r"^https://github\.com/[\w.-]+/[\w.-]+(\.git)?$"


class GithubRepoResource(_Model):
    """Request shape for a github_repository resource on a session.

    Use this in `sessions.create(..., resources=[...])` for fast-fail
    validation and to expose `authorization_token` on the typed path.
    Plain dicts continue to work too. The server validates this same
    shape (`github_resource_validation.py`).

    `mount_path` defaults server-side to `/workspace/<repo-name>` when
    omitted. Setting it requires an absolute path that is not `/` or
    `/home/sprite` (those would shadow the Sprite's working directory).
    """

    type: Literal["github_repository"] = "github_repository"
    url: str = Field(pattern=GITHUB_URL_RE)
    mount_path: str | None = None
    authorization_token: str | None = Field(
        default=None,
        description="GitHub PAT for private repos. Never echoed back on any response.",
    )

    @field_validator("mount_path")
    @classmethod
    def _validate_mount_path(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.startswith("/"):
            raise ValueError("mount_path must be an absolute path")
        # Strip a trailing slash so `/home/sprite/` is treated the same
        # as `/home/sprite`. The server's reserved-path set today does
        # not normalize the trailing slash, so the SDK fails fast where
        # the server might accept; treat that gap as a server bug.
        if v.rstrip("/") in {"", "/home/sprite"}:
            raise ValueError("mount_path must not be the Sprite root")
        return v


GithubRepoResourceInput = GithubRepoResource | dict[str, Any]


class Agent(_Model):
    id: UUID
    type: Literal["agent"] = "agent"
    name: str
    description: str | None = None
    system: str | None = None
    model: str
    runtime: str
    environment_id: UUID | None = None
    skills: list[dict[str, Any]] = Field(default_factory=list)
    mcp_servers: list[McpServer] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentVersion(_Model):
    id: UUID
    type: Literal["agent"] = "agent"
    name: str
    description: str | None = None
    system: str | None = None
    model: str
    runtime: str
    environment_id: UUID | None = None
    skills: list[dict[str, Any]] = Field(default_factory=list)
    mcp_servers: list[McpServer] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int
    created_at: datetime


class Environment(_Model):
    id: UUID
    type: Literal["environment"] = "environment"
    name: str
    packages: dict[str, list[str]] = Field(default_factory=dict)
    setup_script: str | None = None
    networking: Networking
    version: int
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EnvironmentVersion(_Model):
    id: UUID
    type: Literal["environment"] = "environment"
    name: str
    packages: dict[str, list[str]] = Field(default_factory=dict)
    setup_script: str | None = None
    networking: Networking
    version: int
    created_at: datetime


class Session(_Model):
    id: UUID
    agent_id: UUID | None = None
    environment_id: UUID | None = None
    runtime: str
    status: SessionStatus
    exit_code: int | None = None
    created_at: datetime
    updated_at: datetime
    resources: list[SessionResource] = Field(default_factory=list)
    turn_count: int = 0
    current_turn: int | None = None


class SessionTurn(_Model):
    turn_number: int
    prompt: str
    status: TurnStatus
    exit_code: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class SessionAck(_Model):
    """Ack payload returned by `POST /sessions`, `POST /sessions/{id}/prompt`,
    and `POST /sessions/{id}/terminate`.

    The server intentionally returns a trimmed ack here, not a full `Session`.
    Only `id` and `status` are guaranteed on every ack; the rest are set
    when applicable (e.g. `environment_id`/`resources` on create, nothing
    extra on terminate). Fetch the full record via `GET /sessions/{id}`.
    """

    id: UUID
    status: SessionStatus
    stream_url: str | None = None
    environment_id: UUID | None = None
    resources: list[SessionResource] = Field(default_factory=list)
    current_turn: int | None = None


StreamEventType = Literal[
    "start",
    "turn_start",
    "output",
    "stage",
    "exit",
    "error",
    "terminated",
    "stale",
]


class StreamEvent(_Model):
    """A single SSE event from `/sessions/{id}/stream`.

    Additional fields per type land in `extra` — the event schema is still
    evolving on the server, so the SDK keeps the raw payload accessible.
    """

    type: StreamEventType
    id: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> StreamEvent:
        known = {"type", "id"}
        return cls(
            type=payload.get("type", "output"),
            id=payload.get("id"),
            extra={k: v for k, v in payload.items() if k not in known},
        )
