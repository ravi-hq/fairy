"""Comprehensive PostHog event leak guards across every endpoint.

`tests/test_observability.py` covers the three creation events
(agent.created, environment.created, session.created). This file pins
the contract for the *other* events:

  - agent.updated, agent.archived
  - environment.updated, environment.archived, environment.deleted
  - session.terminated, session.deleted, session.prompt_sent

Each test asserts:
  - the event fires with the expected name + safe properties
  - no sensitive content (env_var values, raw prompts, full system prompts,
    setup-script bodies) appears anywhere in the captured properties

A regression that, say, accidentally added `req.prompt` to
session.prompt_sent properties would slip past code review without a
test like this — PostHog dashboards leak credentials silently.
"""

from __future__ import annotations

import json
from typing import Any

import posthog
import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import (
    Agent,
    AgentSession,
    APIKey,
    Environment,
    UserCredential,
    UserSpritesKey,
)


SECRET_PROMPT = "PLEASE_DO_NOT_LEAK_PROMPT_2026"
SECRET_ENV_KEY = "AOD_TELEMETRY_SECRET_KEY"
SECRET_ENV_VALUE = "PLEASE_DO_NOT_LEAK_VALUE_2026"
SECRET_SETUP = "echo PLEASE_DO_NOT_LEAK_SETUP_2026"
SECRET_SYSTEM = "PLEASE_DO_NOT_LEAK_SYSTEM_2026"


@pytest.fixture
def user(db):
    return User.objects.create_user(username="telem", password="x")


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
    """Same setup as test_observability.py — patch Client.capture so events
    are recorded but no network call fires."""
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
    return json.dumps(events, default=str)


@pytest.mark.django_db
def test_agent_archived_event_emits_id_only(client: Client, auth_headers, user, captured_events):
    agent = Agent.objects.create(
        user=user,
        name="A",
        system=SECRET_SYSTEM,
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )
    captured_events.clear()
    resp = client.post(f"/agents/{agent.id}/archive", **auth_headers)
    assert resp.status_code == 200
    matched = [e for e in captured_events if e["event"] == "agent.archived"]
    assert len(matched) == 1
    assert matched[0]["properties"] == {"agent_id": str(agent.id)}
    # System prompt was on the agent — must not appear in the event.
    assert SECRET_SYSTEM not in _all_property_strings(captured_events)


@pytest.mark.django_db
def test_agent_updated_event_excludes_system_and_skill_content(
    client: Client, auth_headers, user, captured_events
):
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )
    resp = client.put(
        f"/agents/{agent.id}",
        data=json.dumps({"version": 1, "system": SECRET_SYSTEM}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 200
    matched = [e for e in captured_events if e["event"] == "agent.updated"]
    assert len(matched) == 1
    blob = _all_property_strings(captured_events)
    assert SECRET_SYSTEM not in blob


@pytest.mark.django_db
def test_environment_archived_event_emits_id_only(
    client: Client, auth_headers, user, captured_events
):
    env = Environment.objects.create(
        user=user,
        name="env-arch",
        env_vars={SECRET_ENV_KEY: SECRET_ENV_VALUE},
        setup_script=SECRET_SETUP,
        version=1,
    )
    captured_events.clear()
    resp = client.post(f"/environments/{env.id}/archive", **auth_headers)
    assert resp.status_code == 200
    matched = [e for e in captured_events if e["event"] == "environment.archived"]
    assert len(matched) == 1
    assert matched[0]["properties"] == {"environment_id": str(env.id)}
    blob = _all_property_strings(captured_events)
    assert SECRET_ENV_KEY not in blob
    assert SECRET_ENV_VALUE not in blob
    assert "PLEASE_DO_NOT_LEAK_SETUP_2026" not in blob


@pytest.mark.django_db
def test_environment_updated_event_excludes_secret_values(
    client: Client, auth_headers, user, captured_events
):
    env = Environment.objects.create(user=user, name="env-upd", version=1)
    resp = client.put(
        f"/environments/{env.id}",
        data=json.dumps(
            {
                "version": 1,
                "env_vars": {SECRET_ENV_KEY: SECRET_ENV_VALUE},
                "setup_script": SECRET_SETUP,
            }
        ),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 200
    matched = [e for e in captured_events if e["event"] == "environment.updated"]
    assert len(matched) == 1
    blob = _all_property_strings(captured_events)
    assert SECRET_ENV_KEY not in blob
    assert SECRET_ENV_VALUE not in blob
    assert "PLEASE_DO_NOT_LEAK_SETUP_2026" not in blob


@pytest.mark.django_db
def test_environment_deleted_event_emits_id_only(
    client: Client, auth_headers, user, captured_events
):
    env = Environment.objects.create(
        user=user,
        name="env-del",
        env_vars={SECRET_ENV_KEY: SECRET_ENV_VALUE},
        version=1,
    )
    captured_events.clear()
    resp = client.delete(f"/environments/{env.id}/delete", **auth_headers)
    assert resp.status_code == 200
    matched = [e for e in captured_events if e["event"] == "environment.deleted"]
    assert len(matched) == 1
    assert matched[0]["properties"] == {"environment_id": str(env.id)}
    blob = _all_property_strings(captured_events)
    assert SECRET_ENV_KEY not in blob
    assert SECRET_ENV_VALUE not in blob


@pytest.mark.django_db
def test_session_prompt_sent_event_excludes_prompt_text(
    client: Client, auth_headers, user, runtime_keys, fake_sprites, captured_events, mocker
):
    """Critical: session.prompt_sent must record `prompt_length` (a number)
    but NEVER the prompt text. A regression that swapped prompt_length for
    prompt would silently leak every user prompt to PostHog."""
    # Bypass session-create flow; create a completed session that's eligible
    # for /prompt.
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="initial", sprite_name="aod-x", status="completed"
    )

    # send_prompt resumes the session via session_service.resume_session and
    # then calls run_turn which defers execute_turn. Stub both so no real
    # work happens; we only want to observe the posthog event.
    mocker.patch("agent_on_demand.session_service.resume_session", return_value=mocker.MagicMock())
    mocker.patch("agent_on_demand.session_service.run_turn")

    captured_events.clear()
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": SECRET_PROMPT, "timeout": 60}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202

    matched = [e for e in captured_events if e["event"] == "session.prompt_sent"]
    assert len(matched) == 1
    props = matched[0]["properties"]
    assert props["prompt_length"] == len(SECRET_PROMPT)
    assert props["timeout"] == 60
    assert props["session_id"] == str(session.id)

    blob = _all_property_strings(captured_events)
    assert SECRET_PROMPT not in blob


@pytest.mark.django_db
def test_session_terminated_event_emits_id_only(
    client: Client, auth_headers, user, runtime_keys, fake_sprites, captured_events
):
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt=SECRET_PROMPT, sprite_name="aod-t", status="completed"
    )
    captured_events.clear()
    resp = client.post(f"/sessions/{session.id}/terminate", **auth_headers)
    assert resp.status_code == 200
    matched = [e for e in captured_events if e["event"] == "session.terminated"]
    assert len(matched) == 1
    assert matched[0]["properties"] == {"session_id": str(session.id)}
    blob = _all_property_strings(captured_events)
    assert SECRET_PROMPT not in blob


@pytest.mark.django_db
def test_session_deleted_event_emits_id_only(
    client: Client, auth_headers, user, runtime_keys, fake_sprites, captured_events
):
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt=SECRET_PROMPT,
        sprite_name="",
        status="completed",
    )
    captured_events.clear()
    resp = client.delete(f"/sessions/{session.id}/delete", **auth_headers)
    assert resp.status_code == 200
    matched = [e for e in captured_events if e["event"] == "session.deleted"]
    assert len(matched) == 1
    assert matched[0]["properties"] == {"session_id": str(session.id)}
    blob = _all_property_strings(captured_events)
    assert SECRET_PROMPT not in blob
