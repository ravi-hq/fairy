from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


SessionStatus = Literal["pending", "running", "completed", "failed", "terminated"]
TurnStatus = Literal["pending", "running", "completed", "failed"]
NetworkingType = Literal["unrestricted", "limited"]
PackageManager = Literal["apt", "cargo", "gem", "go", "npm", "pip"]


class McpServerUrl(_Model):
    """MCP server reachable over HTTP/SSE.

    Use this in `agents.create(..., mcp_servers=[...])` for fast-fail
    validation; the SDK accepts plain dicts too. The server validates
    the same shape (see `mcp_server_validation.py`).
    """

    name: str
    type: Literal["url"] = "url"
    url: str
    headers: dict[str, str] | None = None


class McpServerStdio(_Model):
    """MCP server spawned as a local process on the Sprite.

    Use this in `agents.create(..., mcp_servers=[...])` for fast-fail
    validation; the SDK accepts plain dicts too.
    """

    name: str
    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] | None = None
    env: dict[str, str] | None = None


# Response shape — the server stores whatever was submitted, so the read
# model carries the full union of optional fields rather than two
# discriminated classes (which would force consumers to type-narrow on
# every iteration).
class McpServer(_Model):
    name: str
    type: Literal["url", "stdio"]
    url: str | None = None
    command: str | None = None
    headers: dict[str, str] | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None


# Module-level runtime expression — `from __future__ import annotations` only
# defers evaluation of *annotations*, not assignments. Use `Union` so this
# stays import-safe on Python <3.10 even though we currently floor at 3.11.
McpServerInput = Union[McpServerUrl, McpServerStdio, dict[str, Any]]


# Mirrors `skill_validation.py` on the server. Validating client-side
# means callers see a typed exception immediately rather than a 422
# from the wire.
SKILL_NAME_RE = r"^[a-z0-9][a-z0-9-]{0,63}$"
GITHUB_SOURCE_RE = r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"
MAX_SKILL_CONTENT_BYTES = 64 * 1024
MAX_SKILL_DESCRIPTION_LEN = 1024
SKILL_HEREDOC_DELIMITER = "SKILL_EOF"


class InlineSkill(_Model):
    """A skill whose content is shipped in-band.

    The server materializes `content` to `<skills_root>/<name>/SKILL.md`
    via a bash heredoc at provision time; `content` must therefore not
    contain the heredoc delimiter `SKILL_EOF`. Same regex and size cap
    as `skill_validation.py` on the server.
    """

    # `SKILL_NAME_RE` already caps the length at 64 (`{0,63}` plus the
    # leading char), so no separate `max_length` is needed.
    name: str = Field(pattern=SKILL_NAME_RE)
    description: str = Field(max_length=MAX_SKILL_DESCRIPTION_LEN)
    content: str

    @field_validator("content")
    @classmethod
    def _validate_content(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_SKILL_CONTENT_BYTES:
            raise ValueError(f"content exceeds {MAX_SKILL_CONTENT_BYTES} bytes")
        if SKILL_HEREDOC_DELIMITER in v:
            raise ValueError(f"content must not contain {SKILL_HEREDOC_DELIMITER!r}")
        return v


class GithubSkill(_Model):
    """A skill installed from a GitHub repo at provision time.

    Omit `name` to install every `SKILL.md` the repo exposes; provide
    `name` to install just one.
    """

    type: Literal["github"] = "github"
    description: str = Field(max_length=MAX_SKILL_DESCRIPTION_LEN)
    source: str = Field(pattern=GITHUB_SOURCE_RE)
    name: str | None = Field(default=None, pattern=SKILL_NAME_RE)


SkillInput = InlineSkill | GithubSkill | dict[str, Any]


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
