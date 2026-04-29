"""Build the OpenTelemetry env-var dict for a per-turn Claude Code invocation.

Claude Code reads `TRACEPARENT` / `TRACESTATE` from its own environment in
`-p` (non-interactive) mode and parents its `claude_code.interaction` span
under that context. By passing the worker's `session.execute_turn` span as
the parent, every API request, tool call, and hook execution Claude makes
on the Sprite shows up under the same trace as the rows we wrote on the
worker side — one waterfall per turn in Honeycomb.

Gated on `HONEYCOMB_API_KEY`: when unset, returns `{}` so unit tests and
local dev (and any deploy without telemetry) behave identically. Same
gating shape as `agent_on_demand.observability.init_otel`.

Pure module — no I/O, no Django imports — so it can be direct-tested under
mutmut's hammett runner.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import SessionSpec

HONEYCOMB_OTLP_ENDPOINT = "https://api.honeycomb.io"

# Resource-attribute values must not contain spaces, commas, semicolons,
# backslashes, or double quotes (Claude Code monitoring docs). `=` is the
# key/value separator in the rendered string, so a value containing `=`
# would corrupt parsing (`aod.model=foo=bar` is ambiguous). Everything we
# emit today (UUIDs, ints, slug-shaped runtime/model strings) already
# satisfies that, but the filter keeps a future surprise — say a model
# name that includes `=` — from poisoning the whole attribute string and
# silently dropping every span.
_FORBIDDEN_RESOURCE_VALUE_CHARS = frozenset(' ,;"\\=')


def build_claude_otel_env(
    spec: "SessionSpec",
    traceparent: str | None,
    tracestate: str | None = None,
    honeycomb_api_key: str | None = None,
) -> dict[str, str]:
    """Return non-secret env vars to inject into the Claude Code subprocess
    via the per-turn bash shim.

    The Honeycomb API key is *not* in this dict — the secret-bearing
    `OTEL_EXPORTER_OTLP_HEADERS` line lives in `/tmp/aod-env` (written at
    provision time via `build_claude_otel_static_env`), not the shim
    string. Argv may be persisted by Procrastinate or logged by the
    Sprite SDK; the file is mode 0600 inside the per-session Sprite and
    matches the threat model already accepted for `ANTHROPIC_API_KEY`.

    `honeycomb_api_key` defaults to `os.environ["HONEYCOMB_API_KEY"]`; pass
    explicitly from tests. When unset, returns `{}` — the only safe no-op
    when the receiving collector is unconfigured.

    `traceparent` should be the W3C traceparent header of the worker span
    that's about to launch the turn. When falsy, telemetry still flows but
    the `claude_code.interaction` span starts a new root, which means the
    server-side worker span and the in-Sprite Claude span won't link.

    `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA` is currently undocumented in
    public Claude Code docs (only the standard `CLAUDE_CODE_ENABLE_TELEMETRY`
    is). Confirmed working with Claude Code as of the docs revision at
    https://code.claude.com/docs/en/monitoring-usage (April 2026). If a
    future Claude Code version stops emitting traces, check whether this
    flag has been renamed or promoted to GA.
    """
    api_key = (
        honeycomb_api_key if honeycomb_api_key is not None else os.environ.get("HONEYCOMB_API_KEY")
    )
    if not api_key:
        return {}

    env: dict[str, str] = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_ENDPOINT": HONEYCOMB_OTLP_ENDPOINT,
        "OTEL_RESOURCE_ATTRIBUTES": _build_resource_attributes(spec),
    }
    if traceparent:
        env["TRACEPARENT"] = traceparent
    if tracestate:
        env["TRACESTATE"] = tracestate
    return env


def build_claude_otel_static_env(
    honeycomb_api_key: str | None = None,
) -> list[tuple[str, str]]:
    """Return secret-bearing env-var pairs to write into `/tmp/aod-env` at
    provision time.

    Today this is only `OTEL_EXPORTER_OTLP_HEADERS`, which carries the
    Honeycomb API key. Splitting it from `build_claude_otel_env` keeps the
    secret out of the per-turn bash shim string (and therefore out of any
    log that records the argv handed to `sprite.command(...)`).

    Returns `[]` when `HONEYCOMB_API_KEY` is unset — same gating shape as
    `build_claude_otel_env`. The list-of-pairs return matches the
    `credentials` shape `build_env_file_body` already consumes.
    """
    api_key = (
        honeycomb_api_key if honeycomb_api_key is not None else os.environ.get("HONEYCOMB_API_KEY")
    )
    if not api_key:
        return []
    return [("OTEL_EXPORTER_OTLP_HEADERS", f"x-honeycomb-team={api_key}")]


def _build_resource_attributes(spec: "SessionSpec") -> str:
    """Render `OTEL_RESOURCE_ATTRIBUTES` value (comma-separated key=value).

    Each value is filtered against `_FORBIDDEN_RESOURCE_VALUE_CHARS` and
    skipped if any character would corrupt the attribute string. Empty
    values are also skipped — the doc says `key=` is technically valid but
    it's noise.
    """
    candidates: list[tuple[str, str]] = [
        ("aod.runtime", spec.runtime.name),
        ("aod.model", spec.model),
    ]
    if spec.runtime_session_id:
        candidates.append(("aod.session_id", spec.runtime_session_id))
    if spec.user is not None and spec.user.id is not None:
        candidates.append(("aod.user_id", str(spec.user.id)))
    if spec.environment is not None and spec.environment.id is not None:
        candidates.append(("aod.environment_id", str(spec.environment.id)))
    pairs = [(k, v) for k, v in candidates if _is_safe_attribute_value(v)]
    return ",".join(f"{k}={v}" for k, v in pairs)


def _is_safe_attribute_value(value: str) -> bool:
    if not value:
        return False
    return not any(c in _FORBIDDEN_RESOURCE_VALUE_CHARS for c in value)
