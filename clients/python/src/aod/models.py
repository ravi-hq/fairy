from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    """Networking config on an environment.

    Used for both request and response — the server defaults `type` to
    `"unrestricted"` when absent, and so does this class. Pass to
    `environments.create/update(..., networking=...)` or accept it from
    a fetched `Environment.networking`.
    """

    type: NetworkingType = "unrestricted"
    allowed_hosts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _limited_requires_hosts(self) -> Networking:
        # Server-side, `type="limited"` with empty allowed_hosts builds a
        # policy with only a `*` deny rule — i.e. blocks all egress
        # silently. Reject at SDK construction so callers see the mistake
        # before it ships.
        if self.type == "limited" and not self.allowed_hosts:
            raise ValueError("allowed_hosts must be non-empty when type is 'limited'")
        return self


NetworkingInput = Networking | dict[str, Any]


class SessionResource(_Model):
    """A GitHub repo cloned into the Sprite for a session.

    Tokens supplied on creation are never returned on any response.
    """

    type: Literal["github_repository"]
    url: str
    mount_path: str | None = None


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
