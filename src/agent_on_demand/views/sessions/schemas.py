from typing import Literal

from pydantic import BaseModel, Field, field_validator

from agent_on_demand.validation.github_resource_validation import (
    resolved_mount_path,
    validate_github_url,
    validate_mount_path,
    validate_resources_count_and_dedup,
)


class GitHubRepoResource(BaseModel):
    type: Literal["github_repository"]
    url: str = Field(description="HTTPS GitHub repo URL, e.g. https://github.com/org/repo")
    mount_path: str | None = Field(
        default=None,
        description="Absolute path inside the Sprite where repo is cloned. "
        "Defaults to /workspace/<repo-name>.",
    )
    authorization_token: str | None = Field(
        default=None,
        description="GitHub PAT for private repos",
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return validate_github_url(v)

    @field_validator("mount_path")
    @classmethod
    def _validate_mount_path(cls, v: str | None) -> str | None:
        return validate_mount_path(v)

    def resolved_mount_path(self) -> str:
        return resolved_mount_path(self.url, self.mount_path)


class RunRequest(BaseModel):
    agent_id: str = Field(description="Agent ID to use for this session")
    prompt: str = Field(description="The prompt to send to the agent")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")
    environment_id: str | None = Field(
        default=None, description="Environment ID (overrides agent default)"
    )
    resources: list[GitHubRepoResource] = Field(
        default_factory=list,
        description="GitHub repositories to clone into the session",
    )

    @field_validator("resources")
    @classmethod
    def _validate_resources(cls, v: list[GitHubRepoResource]) -> list[GitHubRepoResource]:
        validate_resources_count_and_dedup([r.resolved_mount_path() for r in v])
        return v


class PromptRequest(BaseModel):
    prompt: str = Field(description="The prompt to send to the agent")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")
