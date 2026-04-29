"""Direct unit tests for `build_claude_otel_env`.

The module is pure (no Django, no I/O) so tests use ``SimpleNamespace``
spec stubs and pass `honeycomb_api_key` explicitly. Each test pins one
mutation-killable property of the env-var dict that gets injected into
Claude Code's process.
"""

from types import SimpleNamespace

import pytest

from agent_on_demand.runtimes.claude_otel import (
    HONEYCOMB_OTLP_ENDPOINT,
    build_claude_otel_env,
    build_claude_otel_static_env,
)


def _spec(
    *,
    runtime_name: str = "claude",
    model: str = "anthropic/claude-sonnet-4-6",
    runtime_session_id: str | None = "sess-uuid-1234",
    user_id: int | None = 42,
    environment_id: int | None = 7,
):
    runtime = SimpleNamespace(name=runtime_name)
    user = None if user_id is None else SimpleNamespace(id=user_id)
    environment = None if environment_id is None else SimpleNamespace(id=environment_id)
    return SimpleNamespace(
        runtime=runtime,
        model=model,
        runtime_session_id=runtime_session_id,
        user=user,
        environment=environment,
    )


# ---------- gating ----------


def test_returns_empty_when_api_key_unset(monkeypatch):
    monkeypatch.delenv("HONEYCOMB_API_KEY", raising=False)
    assert build_claude_otel_env(_spec(), traceparent="abc") == {}


def test_returns_empty_when_api_key_empty_string():
    """Empty string is treated as unset — same shape as observability.init_otel."""
    assert build_claude_otel_env(_spec(), traceparent="abc", honeycomb_api_key="") == {}


def test_uses_env_var_when_no_explicit_key(monkeypatch):
    """The dict is non-empty when HONEYCOMB_API_KEY is set — gating fires
    on the env var, not the explicit kwarg."""
    monkeypatch.setenv("HONEYCOMB_API_KEY", "key-from-env")
    env = build_claude_otel_env(_spec(), traceparent=None)
    assert env != {}
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"


def test_explicit_key_takes_precedence_over_env(monkeypatch):
    """An explicit ``honeycomb_api_key`` argument is what gates the
    return — even if it differs from the env var."""
    monkeypatch.setenv("HONEYCOMB_API_KEY", "from-env")
    env = build_claude_otel_env(_spec(), traceparent=None, honeycomb_api_key="from-arg")
    assert env != {}


def test_dynamic_env_does_not_contain_api_key():
    """Security pin: the per-turn dynamic env (which is rendered into
    the bash shim string and persisted in argv) must not contain the
    Honeycomb API key. The secret-bearing OTEL_EXPORTER_OTLP_HEADERS
    line lives in /tmp/aod-env via build_claude_otel_static_env instead.
    """
    env = build_claude_otel_env(_spec(), traceparent=None, honeycomb_api_key="my-secret-key")
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in env
    # The raw key must not appear in any value either.
    assert all("my-secret-key" not in v for v in env.values())


# ---------- exporter / endpoint ----------


def test_enables_telemetry_and_traces_beta():
    env = build_claude_otel_env(_spec(), traceparent=None, honeycomb_api_key="k")
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] == "1"


def test_selects_otlp_for_all_three_signals():
    env = build_claude_otel_env(_spec(), traceparent=None, honeycomb_api_key="k")
    assert env["OTEL_METRICS_EXPORTER"] == "otlp"
    assert env["OTEL_LOGS_EXPORTER"] == "otlp"
    assert env["OTEL_TRACES_EXPORTER"] == "otlp"


def test_selects_http_protobuf_endpoint():
    env = build_claude_otel_env(_spec(), traceparent=None, honeycomb_api_key="k")
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == HONEYCOMB_OTLP_ENDPOINT
    assert HONEYCOMB_OTLP_ENDPOINT == "https://api.honeycomb.io"


# ---------- trace context ----------


def test_traceparent_is_injected_when_provided():
    env = build_claude_otel_env(_spec(), traceparent="00-abc-01", honeycomb_api_key="k")
    assert env["TRACEPARENT"] == "00-abc-01"


def test_traceparent_omitted_when_none():
    env = build_claude_otel_env(_spec(), traceparent=None, honeycomb_api_key="k")
    assert "TRACEPARENT" not in env


def test_traceparent_omitted_when_empty():
    env = build_claude_otel_env(_spec(), traceparent="", honeycomb_api_key="k")
    assert "TRACEPARENT" not in env


def test_tracestate_is_injected_when_provided():
    env = build_claude_otel_env(
        _spec(),
        traceparent="00-abc-01",
        tracestate="vendor=foo",
        honeycomb_api_key="k",
    )
    assert env["TRACESTATE"] == "vendor=foo"


def test_tracestate_omitted_when_none():
    env = build_claude_otel_env(
        _spec(), traceparent="00-abc-01", tracestate=None, honeycomb_api_key="k"
    )
    assert "TRACESTATE" not in env


# ---------- resource attributes ----------


def test_resource_attributes_include_runtime_and_model():
    env = build_claude_otel_env(_spec(), traceparent=None, honeycomb_api_key="k")
    attrs = env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "aod.runtime=claude" in attrs
    assert "aod.model=anthropic/claude-sonnet-4-6" in attrs


def test_resource_attributes_include_session_id_when_set():
    env = build_claude_otel_env(
        _spec(runtime_session_id="my-session-uuid"),
        traceparent=None,
        honeycomb_api_key="k",
    )
    assert "aod.session_id=my-session-uuid" in env["OTEL_RESOURCE_ATTRIBUTES"]


def test_resource_attributes_skip_session_id_when_none():
    env = build_claude_otel_env(
        _spec(runtime_session_id=None), traceparent=None, honeycomb_api_key="k"
    )
    assert "aod.session_id" not in env["OTEL_RESOURCE_ATTRIBUTES"]


def test_resource_attributes_include_user_and_environment_ids():
    env = build_claude_otel_env(
        _spec(user_id=99, environment_id=11),
        traceparent=None,
        honeycomb_api_key="k",
    )
    attrs = env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "aod.user_id=99" in attrs
    assert "aod.environment_id=11" in attrs


def test_resource_attributes_skip_when_user_missing():
    env = build_claude_otel_env(_spec(user_id=None), traceparent=None, honeycomb_api_key="k")
    assert "aod.user_id" not in env["OTEL_RESOURCE_ATTRIBUTES"]


def test_resource_attributes_skip_when_environment_missing():
    env = build_claude_otel_env(_spec(environment_id=None), traceparent=None, honeycomb_api_key="k")
    assert "aod.environment_id" not in env["OTEL_RESOURCE_ATTRIBUTES"]


@pytest.mark.parametrize("forbidden", [" ", ",", ";", "\\", '"', "="])
def test_resource_attributes_skip_unsafe_values(forbidden):
    """Per Claude Code monitoring docs, OTEL_RESOURCE_ATTRIBUTES values may
    not contain spaces, commas, semicolons, double quotes, or backslashes.
    A bad value silently corrupts every attribute downstream of it, so we
    drop the offender rather than emit a poisoned string."""
    env = build_claude_otel_env(
        _spec(model=f"bad{forbidden}model"),
        traceparent=None,
        honeycomb_api_key="k",
    )
    assert "aod.model=" not in env["OTEL_RESOURCE_ATTRIBUTES"]
    # Other safe attrs still present.
    assert "aod.runtime=claude" in env["OTEL_RESOURCE_ATTRIBUTES"]


def test_resource_attributes_value_format_has_no_spaces():
    """Sanity: the rendered OTEL_RESOURCE_ATTRIBUTES string has no spaces.
    Spaces would break the OTEL parser silently (per the doc)."""
    env = build_claude_otel_env(_spec(), traceparent=None, honeycomb_api_key="k")
    assert " " not in env["OTEL_RESOURCE_ATTRIBUTES"]


# ---------- static env (the secret slot) ----------


def test_static_env_returns_empty_when_api_key_unset(monkeypatch):
    monkeypatch.delenv("HONEYCOMB_API_KEY", raising=False)
    assert build_claude_otel_static_env() == []


def test_static_env_returns_empty_when_api_key_empty():
    assert build_claude_otel_static_env(honeycomb_api_key="") == []


def test_static_env_carries_honeycomb_header():
    """The OTEL header line is what goes into /tmp/aod-env so the API
    key never reaches the per-turn bash shim string."""
    pairs = build_claude_otel_static_env(honeycomb_api_key="my-key")
    assert pairs == [("OTEL_EXPORTER_OTLP_HEADERS", "x-honeycomb-team=my-key")]


def test_static_env_uses_env_var_when_no_explicit_key(monkeypatch):
    monkeypatch.setenv("HONEYCOMB_API_KEY", "from-env")
    pairs = build_claude_otel_static_env()
    assert pairs == [("OTEL_EXPORTER_OTLP_HEADERS", "x-honeycomb-team=from-env")]


def test_static_env_explicit_key_takes_precedence_over_env(monkeypatch):
    monkeypatch.setenv("HONEYCOMB_API_KEY", "from-env")
    pairs = build_claude_otel_static_env(honeycomb_api_key="from-arg")
    assert pairs == [("OTEL_EXPORTER_OTLP_HEADERS", "x-honeycomb-team=from-arg")]
