from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from .._http import check_response
from ..models import Agent, AgentVersion


def _create_body(
    *,
    name: str,
    model: str,
    runtime: str,
    system_prompt: str | None = None,
    metadata: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    mcp_servers: list[dict[str, Any]] | None = None,
    skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "model": model, "runtime": runtime}
    if system_prompt is not None:
        body["system_prompt"] = system_prompt
    if metadata is not None:
        body["metadata"] = metadata
    if tools is not None:
        body["tools"] = tools
    if mcp_servers is not None:
        body["mcp_servers"] = mcp_servers
    if skills is not None:
        body["skills"] = skills
    return body


def _update_body(
    *,
    version: int,
    name: str | None = None,
    model: str | None = None,
    runtime: str | None = None,
    system_prompt: str | None = None,
    metadata: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    mcp_servers: list[dict[str, Any]] | None = None,
    skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"version": version}
    for key, value in (
        ("name", name),
        ("model", model),
        ("runtime", runtime),
        ("system_prompt", system_prompt),
        ("metadata", metadata),
        ("tools", tools),
        ("mcp_servers", mcp_servers),
        ("skills", skills),
    ):
        if value is not None:
            body[key] = value
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
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        skills: list[dict[str, Any]] | None = None,
    ) -> Agent:
        body = _create_body(
            name=name,
            model=model,
            runtime=runtime,
            system_prompt=system_prompt,
            metadata=metadata,
            tools=tools,
            mcp_servers=mcp_servers,
            skills=skills,
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
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        skills: list[dict[str, Any]] | None = None,
    ) -> Agent:
        body = _update_body(
            version=version,
            name=name,
            model=model,
            runtime=runtime,
            system_prompt=system_prompt,
            metadata=metadata,
            tools=tools,
            mcp_servers=mcp_servers,
            skills=skills,
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
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        skills: list[dict[str, Any]] | None = None,
    ) -> Agent:
        body = _create_body(
            name=name,
            model=model,
            runtime=runtime,
            system_prompt=system_prompt,
            metadata=metadata,
            tools=tools,
            mcp_servers=mcp_servers,
            skills=skills,
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
        system_prompt: str | None = None,
        metadata: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        skills: list[dict[str, Any]] | None = None,
    ) -> Agent:
        body = _update_body(
            version=version,
            name=name,
            model=model,
            runtime=runtime,
            system_prompt=system_prompt,
            metadata=metadata,
            tools=tools,
            mcp_servers=mcp_servers,
            skills=skills,
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
