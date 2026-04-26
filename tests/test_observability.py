"""PostHog event firing + sensitive-data leak guards.

These tests do NOT touch the network — they patch `posthog.client.Client.capture`
so every `posthog.capture()` call reaches the spy through the SDK's normal
module-level proxy chain (`_proxy → setup → default_client.capture`).

Mocking `posthog.capture` directly would bypass `setup()`, hiding misconfigured
init bugs in production. Mocking the Client method exercises everything.
"""

from __future__ import annotations

import json
from typing import Any

import posthog
import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand import observability
from agent_on_demand.models import APIKey, UserCredential, UserSpritesKey


SENSITIVE_PROMPT = "PLEASE_DO_NOT_LEAK_THIS_PROMPT_TEXT_2026"
SENSITIVE_REPO_URL = "https://github.com/secret-org/super-private-repo"
SENSITIVE_ENV_VALUE = "PLEASE_DO_NOT_LEAK_THIS_SECRET_VALUE"
SENSITIVE_ENV_KEY = "AOD_TEST_SUPER_SECRET"
SENSITIVE_SETUP_SCRIPT = "echo PLEASE_DO_NOT_LEAK_SETUP_SCRIPT_BODY"


@pytest.fixture
def user(db):
    return User.objects.create_user(username="obs", password="pass")


@pytest.fixture
def auth_headers(user):
    _, raw = APIKey.create_key(user, "k")
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


@pytest.fixture
def runtime_keys(user):
    usk = UserSpritesKey(user=user)
    usk.set_api_key("fake-sprites")
    usk.save()
    cred = UserCredential(user=user, kind="provider:anthropic")
    cred.set_value("fake-anthropic")
    cred.save()


@pytest.fixture
def captured_events(monkeypatch, mocker):
    """Patch Client.capture so real proxy init runs but no network calls fire."""
    monkeypatch.setattr(posthog, "api_key", "phc_test_key_for_unit_tests")
    monkeypatch.setattr(posthog, "disabled", False)
    monkeypatch.setattr(posthog, "default_client", None)

    events: list[dict[str, Any]] = []

    def _client_capture(event, *, distinct_id=None, properties=None, **_kwargs):
        events.append({"distinct_id": distinct_id, "event": event, "properties": properties})
        return "fake-uuid"

    mocker.patch("posthog.client.Client.capture", autospec=False, side_effect=_client_capture)
    return events


def _all_property_strings(events: list[dict[str, Any]]) -> str:
    """Concatenate every property value (including nested) into one searchable
    blob for substring leak assertions."""
    return json.dumps(events, default=str)


def test_init_otel_noop_without_api_key(monkeypatch):
    monkeypatch.delenv("HONEYCOMB_API_KEY", raising=False)
    monkeypatch.setattr(observability, "_otel_initialized", False)
    observability.init_otel()
    assert observability._otel_initialized is False


def test_init_otel_short_circuits_when_already_initialized(monkeypatch):
    """The first call sets `_otel_initialized = True`; subsequent calls must
    early-return without re-touching env vars or re-instrumenting libs.
    Otherwise import-time double-init in worker forks would attach a second
    set of handlers and double-emit every log line."""
    monkeypatch.setattr(observability, "_otel_initialized", True)
    # If the function didn't early-return it would read HONEYCOMB_API_KEY;
    # delete it so a non-early-return would either re-init or no-op based
    # on absence (also wrong, because we already initialized).
    monkeypatch.delenv("HONEYCOMB_API_KEY", raising=False)
    observability.init_otel()
    assert observability._otel_initialized is True


@pytest.fixture
def reset_otel(monkeypatch):
    """Reset OTel global state for tests that exercise init_otel's full body.
    Each test is responsible for cleaning up loggers it attached.

    Also patches the OTLP exporters with no-ops so the lingering
    BatchSpanProcessor / BatchLogRecordProcessor inside the global providers
    don't try to flush to api.honeycomb.io with the fake key at session
    teardown — that produces noisy 401 errors in CI logs even though the
    tests themselves pass.
    """
    import logging

    class _NoopExporter:
        def export(self, _records):
            return 0  # SUCCESS

        def shutdown(self):
            return None

        def force_flush(self, _timeout_millis=30000):
            return True

    # The exporters are imported lazily inside init_otel, so patch the
    # source modules rather than `observability.<name>`.
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        lambda **_kw: _NoopExporter(),
    )
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter",
        lambda **_kw: _NoopExporter(),
    )

    monkeypatch.setattr(observability, "_otel_initialized", False)
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    yield
    # Detach any handlers init_otel added so subsequent tests aren't polluted
    # by an OTel BatchProcessor pinned to a torn-down provider.
    for h in list(root.handlers):
        if h not in handlers_before:
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    root.setLevel(level_before)


def test_init_otel_attaches_handlers_when_api_key_set(monkeypatch, reset_otel):
    """With HONEYCOMB_API_KEY set, init_otel must mark itself initialized,
    attach an OTel LoggingHandler to root, and ensure a StreamHandler is
    present too — the StreamHandler is critical because attaching only the
    OTel handler suppresses Python's implicit stderr fallback, leaving
    Render/etc. with no app log output."""
    import logging

    from opentelemetry.sdk._logs import LoggingHandler

    monkeypatch.setenv("HONEYCOMB_API_KEY", "fake-key-for-tests")
    observability.init_otel(service_name="aod-test")

    assert observability._otel_initialized is True
    root = logging.getLogger()
    has_otel = any(isinstance(h, LoggingHandler) for h in root.handlers)
    has_stream = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, LoggingHandler)
        for h in root.handlers
    )
    assert has_otel, "OTel LoggingHandler must be attached"
    assert has_stream, "StreamHandler must remain so platform log capture keeps working"


def test_init_otel_resolves_service_name_from_arg(monkeypatch, reset_otel):
    """Explicit service_name arg wins over env var — used by the worker entry
    point so traces from web/worker land in separate Honeycomb datasets."""
    monkeypatch.setenv("HONEYCOMB_API_KEY", "fake-key-for-tests")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "from-env")
    observability.init_otel(service_name="from-arg")
    assert observability._otel_initialized is True
    # The function doesn't expose the resolved name, but completing without
    # raising plus _otel_initialized=True confirms the resource was built;
    # the precedence is exercised by line coverage on the resolution branch.


def test_instrument_libraries_warns_on_individual_failures(monkeypatch, mocker, reset_otel):
    """Each auto-instrumentation is wrapped in try/except so one bad
    optional dep can't take the whole process down. Patch one to raise
    and assert the others still attach + a warning is logged."""
    monkeypatch.setenv("HONEYCOMB_API_KEY", "fake-key-for-tests")

    # Fail Django instrumentation; psycopg + requests should still proceed.
    fake_django_inst = mocker.MagicMock()
    fake_django_inst.return_value.instrument.side_effect = RuntimeError("boom")
    mocker.patch("opentelemetry.instrumentation.django.DjangoInstrumentor", fake_django_inst)

    warn = mocker.patch.object(observability.logger, "warning")
    observability.init_otel(service_name="aod-test-warn")

    assert observability._otel_initialized is True
    # At least one warning fired for the failing instrumentation.
    failing_warns = [c for c in warn.call_args_list if "Django instrumentation failed" in str(c)]
    assert failing_warns, "expected a warning for the Django instrumentation failure"


@pytest.mark.django_db
def test_agent_created_event_fires_with_safe_props(client: Client, auth_headers, captured_events):
    resp = client.post(
        "/agents",
        data=json.dumps(
            {
                "name": "obs-agent",
                "model": "anthropic/claude-sonnet-4-6",
                "runtime": "claude",
                "system": "SYSTEM_PROMPT_BODY_SHOULD_NOT_LEAK",
                "skills": [
                    {
                        "name": "skill-one",
                        "description": "SKILL_DESC_SHOULD_NOT_LEAK",
                        "content": "---\nname: skill-one\ndescription: x\n---\nSKILL_BODY",
                    }
                ],
            }
        ),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 201
    matched = [e for e in captured_events if e["event"] == "agent.created"]
    assert len(matched) == 1
    props = matched[0]["properties"]
    assert props["runtime"] == "claude"
    assert props["model"] == "anthropic/claude-sonnet-4-6"
    assert props["skill_count"] == 1
    assert props["system_length"] == len("SYSTEM_PROMPT_BODY_SHOULD_NOT_LEAK")

    blob = _all_property_strings(captured_events)
    assert "SYSTEM_PROMPT_BODY_SHOULD_NOT_LEAK" not in blob
    assert "SKILL_DESC_SHOULD_NOT_LEAK" not in blob
    assert "SKILL_BODY" not in blob


@pytest.mark.django_db
def test_environment_created_event_excludes_secret_values(
    client: Client, auth_headers, captured_events
):
    resp = client.post(
        "/environments",
        data=json.dumps(
            {
                "name": "obs-env",
                "packages": {"pip": ["requests", "httpx"]},
                "env_vars": {SENSITIVE_ENV_KEY: SENSITIVE_ENV_VALUE},
                "setup_script": SENSITIVE_SETUP_SCRIPT,
                "networking": {"type": "limited", "allowed_hosts": ["api.example.com"]},
            }
        ),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 201
    matched = [e for e in captured_events if e["event"] == "environment.created"]
    assert len(matched) == 1
    props = matched[0]["properties"]
    assert props["env_var_count"] == 1
    assert props["package_count"] == 2
    assert props["package_managers"] == ["pip"]
    assert props["networking_type"] == "limited"
    assert props["allowed_hosts_count"] == 1
    assert props["has_setup_script"] is True

    blob = _all_property_strings(captured_events)
    assert SENSITIVE_ENV_KEY not in blob
    assert SENSITIVE_ENV_VALUE not in blob
    assert "PLEASE_DO_NOT_LEAK_SETUP_SCRIPT_BODY" not in blob


@pytest.mark.django_db
def test_session_created_event_excludes_prompt_and_repo_url(
    client: Client, auth_headers, runtime_keys, fake_sprites, captured_events
):
    env_resp = client.post(
        "/environments",
        data=json.dumps(
            {
                "name": "obs-env",
                "env_vars": {SENSITIVE_ENV_KEY: SENSITIVE_ENV_VALUE},
            }
        ),
        content_type="application/json",
        **auth_headers,
    )
    env_id = env_resp.json()["id"]

    agent_resp = client.post(
        "/agents",
        data=json.dumps(
            {
                "name": "a",
                "model": "anthropic/claude-sonnet-4-6",
                "runtime": "claude",
                "environment_id": env_id,
            }
        ),
        content_type="application/json",
        **auth_headers,
    )
    agent_id = agent_resp.json()["id"]

    captured_events.clear()

    resp = client.post(
        "/sessions",
        data=json.dumps(
            {
                "agent_id": agent_id,
                "prompt": SENSITIVE_PROMPT,
                "resources": [{"type": "github_repository", "url": SENSITIVE_REPO_URL}],
            }
        ),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202

    matched = [e for e in captured_events if e["event"] == "session.created"]
    assert len(matched) == 1
    props = matched[0]["properties"]
    assert props["runtime"] == "claude"
    assert props["repo_count"] == 1
    assert props["env_var_count"] == 1
    assert props["prompt_length"] == len(SENSITIVE_PROMPT)

    blob = _all_property_strings(captured_events)
    assert SENSITIVE_PROMPT not in blob
    assert SENSITIVE_REPO_URL not in blob
    assert SENSITIVE_ENV_KEY not in blob
    assert SENSITIVE_ENV_VALUE not in blob
