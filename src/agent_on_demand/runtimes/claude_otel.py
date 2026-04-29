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
# backslashes, or double quotes (Claude Code monitoring docs). Everything
# we emit (UUIDs, ints, slug-shaped runtime/model strings) already satisfies
# that, but the filter keeps a future surprise from poisoning the whole
# attribute string and silently dropping every span.
_FORBIDDEN_RESOURCE_VALUE_CHARS = frozenset(' ,;"\\')


def build_claude_otel_env(
    spec: "SessionSpec",
    traceparent: str | None,
    tracestate: str | None = None,
    honeycomb_api_key: str | None = None,
) -> dict[str, str]:
    """Return env vars to inject into the Claude Code subprocess.

    `honeycomb_api_key` defaults to `os.environ["HONEYCOMB_API_KEY"]`; pass
    explicitly from tests. When unset, returns `{}` — the only safe no-op
    when the receiving collector is unconfigured.

    `traceparent` should be the W3C traceparent header of the worker span
    that's about to launch the turn. When falsy, telemetry still flows but
    the `claude_code.interaction` span starts a new root, which means the
    server-side worker span and the in-Sprite Claude span won't link.
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
        "OTEL_EXPORTER_OTLP_HEADERS": f"x-honeycomb-team={api_key}",
        "OTEL_RESOURCE_ATTRIBUTES": _build_resource_attributes(spec),
    }
    if traceparent:
        env["TRACEPARENT"] = traceparent
    if tracestate:
        env["TRACESTATE"] = tracestate
    return env


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
