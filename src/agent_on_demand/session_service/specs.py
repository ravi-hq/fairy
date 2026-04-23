from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agent_on_demand.models import Environment

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from agent_on_demand.runtimes import Runtime


@dataclass(frozen=True)
class RepoSpec:
    url: str
    mount_path: str
    token: str | None = None


@dataclass(frozen=True)
class McpServerSpec:
    """Normalized MCP server config, translated to runtime-specific format."""

    name: str
    type: str = "url"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillSpec:
    """A skill to materialize on the Sprite.

    Exactly one of ``content`` / ``source`` is set:

    - inline: ``content`` carries the full SKILL.md text (including YAML
      frontmatter). Written directly to ``<skills_root>/<name>/SKILL.md`` —
      ``name`` is required for this shape.
    - github: ``source`` is an ``owner/repo`` identifier. Installed on the
      Sprite during provisioning by invoking the
      `skills.sh <https://skills.sh>`_ CLI
      (``npx -y skills@latest add <source> -g -a <runtime-agent> -y``).
      ``name`` is optional: when set, it is forwarded to the CLI as
      ``--skill <name>`` to install a single SKILL.md from the repo; when
      omitted, every skill in the repo is installed.
    """

    name: str | None = None
    content: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class SessionSpec:
    """Everything needed to stand up a Sprite ready to run an agent.

    The view layer builds this from the request; the service decides how to
    realize each field on the Sprite.
    """

    name: str
    runtime: "Runtime"
    model: str
    user: "User"
    runtime_session_id: str | None
    environment: Environment | None
    repos: list[RepoSpec]
    mcp_servers: list[McpServerSpec]
    skills: list[SkillSpec]
