from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from agent_on_demand.session_service.backends import SessionHandle
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


class Runtime(Protocol):
    name: str
    providers: set[str]  # which providers this runtime can serve; non-empty
    skills_root: str | None  # absolute path on Sprite for SKILL.md files, or None to disable
    skills_sh_agent: str | None  # `--agent` id for `npx skills add`, or None if unsupported

    def install(self, handle: SessionHandle) -> None:
        """Install the runtime CLI on the session. No-op if pre-installed in the base image."""

    def build_command(self, spec: SessionSpec, mode: Literal["run", "continue"]) -> list[str]:
        """Argv for the per-turn command. Prompt arrives via stdin."""

    def write_config(
        self, handle: SessionHandle, spec: SessionSpec, mcp_servers: list[McpServerSpec]
    ) -> None:
        """Write any per-runtime config files on the session. Always called at provision time,
        even when mcp_servers is empty."""

    def otel_env(
        self,
        spec: SessionSpec,
        traceparent: str | None,
        tracestate: str | None,
    ) -> dict[str, str]:
        """Non-secret env vars to inject into the per-turn process for
        OpenTelemetry export.

        Return ``{}`` to disable. Implementations that support OTel should
        honor ``traceparent`` / ``tracestate`` so the runtime CLI's spans
        parent under the worker span that launched the turn.

        Secret-bearing values (API keys, auth headers) belong in
        ``static_env`` instead, since this dict is rendered into the
        ``argv`` handed to the backend and could leak via logs.

        Default returns ``{}`` rather than ``None`` so a runtime that
        forgets to override still composes safely with ``build_turn_argv``.
        """
        return {}

    def static_env(self, spec: SessionSpec) -> list[tuple[str, str]]:
        """Runtime-specific env-var pairs to write into ``/tmp/aod-env`` at
        provision time.

        Use for secrets (the file is mode 0600 on the per-session Sprite,
        same threat model as the credentials already written there) or
        for config that doesn't change per turn. Per-turn dynamic context
        (W3C trace headers, etc.) belongs in ``otel_env``.

        Default returns ``[]`` so a runtime that forgets to override is a
        silent no-op rather than a `TypeError` in the env-file builder.
        """
        return []
