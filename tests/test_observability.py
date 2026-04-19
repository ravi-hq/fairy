"""PostHog event firing + sensitive-data leak guards.

These tests do NOT touch the network — they patch `posthog.capture` and force
`_posthog_initialized=True` so `track()` calls reach the mock. The init
helpers themselves are validated as no-ops when their env vars are unset.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand import observability
from agent_on_demand.models import APIKey, UserRuntimeKey, UserSpritesKey


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
    urk = UserRuntimeKey(user=user, runtime="claude")
    urk.set_api_key("fake-anthropic")
    urk.save()


@pytest.fixture
def captured_events(monkeypatch, mocker):
    """Set POSTHOG_API_KEY, run the real `init_posthog()`, then mock at the
    Client level so the entire posthog module-level proxy chain
    (`_proxy → setup → default_client.capture`) executes for real.

    Mocking `posthog.capture` directly (the previous approach) bypasses
    `setup()`, so a misconfigured init silently passes the test suite while
    failing in production. Mocking the Client method exercises everything.
    """
    import posthog

    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test_key_for_unit_tests")
    monkeypatch.setattr(observability, "_posthog_initialized", False)
    monkeypatch.setattr(posthog, "default_client", None)

    events: list[dict[str, Any]] = []

    def _client_capture(event, *, distinct_id=None, properties=None, **_kwargs):
        events.append({"distinct_id": distinct_id, "event": event, "properties": properties})
        return "fake-uuid"

    mocker.patch("posthog.client.Client.capture", autospec=False, side_effect=_client_capture)

    observability.init_posthog()
    assert observability._posthog_initialized, "init_posthog() must succeed for these tests"
    return events


def _all_property_strings(events: list[dict[str, Any]]) -> str:
    """Concatenate every property value (including nested) into one searchable
    blob for substring leak assertions."""
    return json.dumps(events, default=str)


# --- init helpers no-op when env unset ---


def test_init_otel_noop_without_api_key(monkeypatch):
    monkeypatch.delenv("HONEYCOMB_API_KEY", raising=False)
    monkeypatch.setattr(observability, "_otel_initialized", False)
    observability.init_otel()
    assert observability._otel_initialized is False


def test_init_posthog_noop_without_api_key(monkeypatch):
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    monkeypatch.setattr(observability, "_posthog_initialized", False)
    observability.init_posthog()
    assert observability._posthog_initialized is False


def test_track_noop_when_posthog_uninitialized(monkeypatch, mocker):
    monkeypatch.setattr(observability, "_posthog_initialized", False)
    spy = mocker.patch("posthog.capture")
    observability.track("anything", user=None, properties={"x": 1})
    spy.assert_not_called()


def test_distinct_id_is_stable_and_hashed(user):
    a = observability.distinct_id_for_user(user)
    b = observability.distinct_id_for_user(user)
    assert a == b
    assert len(a) == 32
    # Hex-only — never the raw username or any other identifying field
    assert all(c in "0123456789abcdef" for c in a)
    assert user.username not in a


# --- agent.created event + leak guard ---


@pytest.mark.django_db
def test_agent_created_event_fires_with_safe_props(client: Client, auth_headers, captured_events):
    resp = client.post(
        "/agents",
        data=json.dumps(
            {
                "name": "obs-agent",
                "model": "claude-sonnet-4-6",
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
    assert props["model"] == "claude-sonnet-4-6"
    assert props["skill_count"] == 1
    assert props["system_length"] == len("SYSTEM_PROMPT_BODY_SHOULD_NOT_LEAK")

    blob = _all_property_strings(captured_events)
    assert "SYSTEM_PROMPT_BODY_SHOULD_NOT_LEAK" not in blob
    assert "SKILL_DESC_SHOULD_NOT_LEAK" not in blob
    assert "SKILL_BODY" not in blob


# --- environment.created event + leak guard ---


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


# --- session.created event + leak guard (prompt + repo URL must not appear) ---


@pytest.mark.django_db
def test_session_created_event_excludes_prompt_and_repo_url(
    client: Client, auth_headers, runtime_keys, fake_sprites, captured_events
):
    # Create env + agent
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
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "environment_id": env_id,
            }
        ),
        content_type="application/json",
        **auth_headers,
    )
    agent_id = agent_resp.json()["id"]

    # Drop pre-session events so the assertion blob is just the session ones
    captured_events.clear()

    resp = client.post(
        "/sessions",
        data=json.dumps(
            {
                "agent_id": agent_id,
                "prompt": SENSITIVE_PROMPT,
                "resources": [
                    {"type": "github_repository", "url": SENSITIVE_REPO_URL}
                ],
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
