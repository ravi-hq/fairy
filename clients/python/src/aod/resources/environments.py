from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from .._http import check_response
from ..models import Environment, EnvironmentVersion, Networking, NetworkingInput


def _normalize_networking(networking: NetworkingInput | None) -> dict[str, Any] | None:
    if networking is None:
        return None
    if isinstance(networking, Networking):
        return networking.model_dump(exclude_none=True)
    return networking


def _create_body(
    *,
    name: str,
    packages: dict[str, list[str]] | None = None,
    env_vars: dict[str, str] | None = None,
    setup_script: str | None = None,
    networking: NetworkingInput | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if packages is not None:
        body["packages"] = packages
    if env_vars is not None:
        body["env_vars"] = env_vars
    if setup_script is not None:
        body["setup_script"] = setup_script
    normalized = _normalize_networking(networking)
    if normalized is not None:
        body["networking"] = normalized
    return body


def _update_body(
    *,
    version: int,
    name: str | None = None,
    packages: dict[str, list[str]] | None = None,
    env_vars: dict[str, str] | None = None,
    setup_script: str | None = None,
    networking: NetworkingInput | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"version": version}
    if name is not None:
        body["name"] = name
    if packages is not None:
        body["packages"] = packages
    if env_vars is not None:
        body["env_vars"] = env_vars
    if setup_script is not None:
        body["setup_script"] = setup_script
    normalized = _normalize_networking(networking)
    if normalized is not None:
        body["networking"] = normalized
    return body


class Environments:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def list(self) -> list[Environment]:
        body = check_response(self._client.get("/environments"))
        return [Environment.model_validate(e) for e in body["data"]]

    def create(
        self,
        *,
        name: str,
        packages: dict[str, list[str]] | None = None,
        env_vars: dict[str, str] | None = None,
        setup_script: str | None = None,
        networking: NetworkingInput | None = None,
    ) -> Environment:
        body = _create_body(
            name=name,
            packages=packages,
            env_vars=env_vars,
            setup_script=setup_script,
            networking=networking,
        )
        return Environment.model_validate(
            check_response(self._client.post("/environments", json=body))
        )

    def get(self, environment_id: str | UUID) -> Environment:
        return Environment.model_validate(
            check_response(self._client.get(f"/environments/{environment_id}"))
        )

    def update(
        self,
        environment_id: str | UUID,
        *,
        version: int,
        name: str | None = None,
        packages: dict[str, list[str]] | None = None,
        env_vars: dict[str, str] | None = None,
        setup_script: str | None = None,
        networking: NetworkingInput | None = None,
    ) -> Environment:
        body = _update_body(
            version=version,
            name=name,
            packages=packages,
            env_vars=env_vars,
            setup_script=setup_script,
            networking=networking,
        )
        return Environment.model_validate(
            check_response(self._client.put(f"/environments/{environment_id}", json=body))
        )

    def archive(self, environment_id: str | UUID) -> Environment:
        return Environment.model_validate(
            check_response(self._client.post(f"/environments/{environment_id}/archive"))
        )

    def delete(self, environment_id: str | UUID) -> None:
        check_response(self._client.delete(f"/environments/{environment_id}/delete"))

    def versions(self, environment_id: str | UUID) -> list[EnvironmentVersion]:
        body = check_response(self._client.get(f"/environments/{environment_id}/versions"))
        return [EnvironmentVersion.model_validate(v) for v in body["data"]]


class AsyncEnvironments:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def list(self) -> list[Environment]:
        body = check_response(await self._client.get("/environments"))
        return [Environment.model_validate(e) for e in body["data"]]

    async def create(
        self,
        *,
        name: str,
        packages: dict[str, list[str]] | None = None,
        env_vars: dict[str, str] | None = None,
        setup_script: str | None = None,
        networking: NetworkingInput | None = None,
    ) -> Environment:
        body = _create_body(
            name=name,
            packages=packages,
            env_vars=env_vars,
            setup_script=setup_script,
            networking=networking,
        )
        return Environment.model_validate(
            check_response(await self._client.post("/environments", json=body))
        )

    async def get(self, environment_id: str | UUID) -> Environment:
        return Environment.model_validate(
            check_response(await self._client.get(f"/environments/{environment_id}"))
        )

    async def update(
        self,
        environment_id: str | UUID,
        *,
        version: int,
        name: str | None = None,
        packages: dict[str, list[str]] | None = None,
        env_vars: dict[str, str] | None = None,
        setup_script: str | None = None,
        networking: NetworkingInput | None = None,
    ) -> Environment:
        body = _update_body(
            version=version,
            name=name,
            packages=packages,
            env_vars=env_vars,
            setup_script=setup_script,
            networking=networking,
        )
        return Environment.model_validate(
            check_response(await self._client.put(f"/environments/{environment_id}", json=body))
        )

    async def archive(self, environment_id: str | UUID) -> Environment:
        return Environment.model_validate(
            check_response(await self._client.post(f"/environments/{environment_id}/archive"))
        )

    async def delete(self, environment_id: str | UUID) -> None:
        check_response(await self._client.delete(f"/environments/{environment_id}/delete"))

    async def versions(self, environment_id: str | UUID) -> list[EnvironmentVersion]:
        body = check_response(await self._client.get(f"/environments/{environment_id}/versions"))
        return [EnvironmentVersion.model_validate(v) for v in body["data"]]
