"""Translate persisted session ORM rows into a SessionSpec.

This is the single path that hydrates AgentSession + Agent + Environment +
SessionResource state into the SessionSpec shape consumed by provisioning
and turn execution. A bug here (missing field, wrong default, type drift)
silently breaks every session, so the function gets direct pytest-django
unit tests in tests/test_spec_factory.py.

Lives outside tasks.py so it can be imported and tested without pulling in
Procrastinate task decorators (which mutmut's hammett runner can't load).
"""

from __future__ import annotations

from agent_on_demand.models import AgentSession
from agent_on_demand.runtimes import RUNTIMES

from .specs import McpServerSpec, RepoSpec, SessionSpec, SkillSpec


def build_spec_for_session(session: AgentSession) -> SessionSpec:
    """Rehydrate a SessionSpec from persisted session state."""
    agent = session.agent
    mcp_servers: list[McpServerSpec] = []
    skills: list[SkillSpec] = []
    model = ""
    if agent is not None:
        model = agent.model
        for s in agent.mcp_servers or []:
            mcp_servers.append(
                McpServerSpec(
                    name=s["name"],
                    type=s.get("type", "url"),
                    url=s.get("url", ""),
                    headers=s.get("headers", {}),
                    command=s.get("command", ""),
                    args=s.get("args", []),
                    env=s.get("env", {}),
                )
            )
        for s in agent.skills or []:
            if s.get("type") == "github":
                # name is optional for github refs (omit → install all skills
                # from the repo); pass it through verbatim, including absent.
                skills.append(SkillSpec(name=s.get("name"), source=s["source"]))
            else:
                skills.append(SkillSpec(name=s["name"], content=s["content"]))

    repos = [
        RepoSpec(url=r.url, mount_path=r.mount_path, token=r.get_token())
        for r in session.resources.all()
    ]

    return SessionSpec(
        name=session.sprite_name,
        runtime=RUNTIMES[session.runtime],
        model=model,
        user=session.user,
        runtime_session_id=str(session.runtime_session_id) if session.runtime_session_id else None,
        environment=session.environment,
        repos=repos,
        mcp_servers=mcp_servers,
        skills=skills,
    )
