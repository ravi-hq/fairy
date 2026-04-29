from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel

from .._http import check_response
from ..models import Agent, AgentVersion, SkillInput


def _normalize_skills(skills: list[SkillInput] | None) -> list[dict[str, Any]] | None:
    """Accept typed pydantic inputs or raw dicts, return wire form."""
    if skills is None:
        return None
    out: list[dict[str, Any]] = []
    for entry in skills:
        if isinstance(entry, BaseModel):
            out.append(entry.model_dump(exclude_none=True))
        else:
            out.append(entry)
    return out


def _create_body(
    *,
    name: str,
    model: str,
    runtime: str,
    system: str | None = None,
    description: str | None = None,
    environment_id: str | UUID | None = None,
    skills: list[SkillInput] | None = None,
    mcp_servers: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "model": model, "runtime": runtime}
    if system is not None:
        body["system"] = system
    if description is not None:
        body["description"] = description
    if environment_id is not None:
        body["environment_id"] = str(environment_id)
    normalized_skills = _normalize_skills(skills)
    if normalized_skills is not None:
        body["skills"] = normalized_skills
    if mcp_servers is not None:
        body["mcp_servers"] = mcp_servers
    if metadata is not None:
        body["metadata"] = metadata
    return body


def _update_body(
    *,
    version: int,
    name: str | None = None,
    model: str | None = None,
    runtime: str | None = None,
    system: str | None = None,
    description: str | None = None,
    environment_id: str | UUID | None = None,
    skills: list[SkillInput] | None = None,
    mcp_servers: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"version": version}
    if name is not None:
        body["name"] = name
    if model is not None:
        body["model"] = model
    if runtime is not None:
        body["runtime"] = runtime
    if system is not None:
        body["system"] = system
    if description is not None:
        body["description"] = description
    if environment_id is not None:
        body["environment_id"] = str(environment_id)
    normalized_skills = _normalize_skills(skills)
    if normalized_skills is not None:
        body["skills"] = normalized_skills
    if mcp_servers is not None:
        body["mcp_servers"] = mcp_servers
    if metadata is not None:
        body["metadata"] = metadata
    return body


class Agents:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def list(self) -> list[Agent]:
        body = check_response(self._client.get("/agents"))
        return [Agent.model_validate(a) for a in body["data"]]

    def create(
        self,
        *,
        name: str,
        model: str,
        runtime: str,
        system: str | None = None,
        description: str | None = None,
        environment_id: str | UUID | None = None,
        skills: list[SkillInput] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Agent:
        body = _create_body(
            name=name,
            model=model,
            runtime=runtime,
            system=system,
            description=description,
            environment_id=environment_id,
            skills=skills,
            mcp_servers=mcp_servers,
            metadata=metadata,
        )
        return Agent.model_validate(check_response(self._client.post("/agents", json=body)))

    def get(self, agent_id: str | UUID) -> Agent:
        return Agent.model_validate(check_response(self._client.get(f"/agents/{agent_id}")))

    def update(
        self,
        agent_id: str | UUID,
        *,
        version: int,
        name: str | None = None,
        model: str | None = None,
        runtime: str | None = None,
        system: str | None = None,
        description: str | None = None,
        environment_id: str | UUID | None = None,
        skills: list[SkillInput] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Agent:
        body = _update_body(
            version=version,
            name=name,
            model=model,
            runtime=runtime,
            system=system,
            description=description,
            environment_id=environment_id,
            skills=skills,
            mcp_servers=mcp_servers,
            metadata=metadata,
        )
        return Agent.model_validate(
            check_response(self._client.put(f"/agents/{agent_id}", json=body))
        )

    def archive(self, agent_id: str | UUID) -> Agent:
        return Agent.model_validate(
            check_response(self._client.post(f"/agents/{agent_id}/archive"))
        )

    def versions(self, agent_id: str | UUID) -> list[AgentVersion]:
        body = check_response(self._client.get(f"/agents/{agent_id}/versions"))
        return [AgentVersion.model_validate(v) for v in body["data"]]


class AsyncAgents:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def list(self) -> list[Agent]:
        body = check_response(await self._client.get("/agents"))
        return [Agent.model_validate(a) for a in body["data"]]

    async def create(
        self,
        *,
        name: str,
        model: str,
        runtime: str,
        system: str | None = None,
        description: str | None = None,
        environment_id: str | UUID | None = None,
        skills: list[SkillInput] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Agent:
        body = _create_body(
            name=name,
            model=model,
            runtime=runtime,
            system=system,
            description=description,
            environment_id=environment_id,
            skills=skills,
            mcp_servers=mcp_servers,
            metadata=metadata,
        )
        return Agent.model_validate(check_response(await self._client.post("/agents", json=body)))

    async def get(self, agent_id: str | UUID) -> Agent:
        return Agent.model_validate(check_response(await self._client.get(f"/agents/{agent_id}")))

    async def update(
        self,
        agent_id: str | UUID,
        *,
        version: int,
        name: str | None = None,
        model: str | None = None,
        runtime: str | None = None,
        system: str | None = None,
        description: str | None = None,
        environment_id: str | UUID | None = None,
        skills: list[SkillInput] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Agent:
        body = _update_body(
            version=version,
            name=name,
            model=model,
            runtime=runtime,
            system=system,
            description=description,
            environment_id=environment_id,
            skills=skills,
            mcp_servers=mcp_servers,
            metadata=metadata,
        )
        return Agent.model_validate(
            check_response(await self._client.put(f"/agents/{agent_id}", json=body))
        )

    async def archive(self, agent_id: str | UUID) -> Agent:
        return Agent.model_validate(
            check_response(await self._client.post(f"/agents/{agent_id}/archive"))
        )

    async def versions(self, agent_id: str | UUID) -> list[AgentVersion]:
        body = check_response(await self._client.get(f"/agents/{agent_id}/versions"))
        return [AgentVersion.model_validate(v) for v in body["data"]]
