"""Pin step 1 of the ``sprite_name`` → ``backend_handle`` rename.

This PR adds the ``backend_handle`` column. App code dual-writes both
columns and reads ``backend_handle`` with a fallback to ``sprite_name``.
The fallback path covers in-flight sessions provisioned during the deploy
window before the dual-write code shipped — without it, those sessions get
stranded with an empty handle and orphan their backend resource on
termination.

Step 2 (drop ``sprite_name``) is a separate forward-only PR after the
dual-write deploy soaks for >=1 day.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client

from agent_on_demand.models import Agent, AgentSession, APIKey, UserCredential, UserSpritesKey


@pytest.fixture
def user(db):
    return User.objects.create_user(username="bhuser", password="x")


@pytest.fixture
def auth_headers(user):
    _, raw = APIKey.create_key(user, "k")
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


@pytest.fixture
def sprites_key(user):
    usk = UserSpritesKey(user=user)
    usk.set_api_key("fake-sprites")
    usk.save()
    return usk


@pytest.fixture
def runtime_key(user, sprites_key):
    cred = UserCredential(user=user, kind="provider:anthropic")
    cred.set_value("fake-anthropic")
    cred.save()
    return cred


@pytest.fixture
def agent(user):
    return Agent.objects.create(
        user=user,
        name="Test Agent",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )


@pytest.mark.django_db
def test_backend_handle_column_exists():
    """Migration 0018 added a ``backend_handle`` column on the sessions table.

    Goes through Django's introspection API so the assertion holds on both
    the SQLite test backend and Postgres in production.
    """
    with connection.cursor() as cursor:
        descriptions = connection.introspection.get_table_description(cursor, "agent_sessions")
    by_name = {d.name: d for d in descriptions}
    assert "backend_handle" in by_name, "backend_handle column should exist after migration 0018"
    assert "sprite_name" in by_name, "sprite_name column must still exist — step 2 is a separate PR"


@pytest.mark.django_db
def test_backend_handle_defaults_to_empty_for_existing_rows(user):
    """Rows created without an explicit ``backend_handle`` default to empty —
    that's what makes the additive migration safe on the populated table."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="x", sprite_name="legacy-handle"
    )
    session.refresh_from_db()
    assert session.backend_handle == ""
    assert session.sprite_name == "legacy-handle"


@pytest.mark.django_db
def test_create_session_dual_writes_backend_handle(
    client: Client, auth_headers, runtime_key, agent, fake_sprites
):
    """POST /sessions sets ``backend_handle`` and ``sprite_name`` to the same
    value. Missing this dual-write is the failure mode that strands new
    sessions when step 2 drops ``sprite_name``."""
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hi"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202
    session = AgentSession.objects.get(pk=resp.json()["id"])
    assert session.sprite_name
    assert session.backend_handle == session.sprite_name


@pytest.mark.django_db
def test_terminate_session_clears_both_handle_columns(
    client: Client, auth_headers, sprites_key, user, fake_sprites
):
    """Terminate must clear both columns or step 2's drop of ``sprite_name``
    would silently leave a stale handle on the session row."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="t",
        sprite_name="aod-dual",
        backend_handle="aod-dual",
        status="completed",
    )
    resp = client.post(f"/sessions/{session.id}/terminate", **auth_headers)
    assert resp.status_code == 200
    session.refresh_from_db()
    assert session.sprite_name == ""
    assert session.backend_handle == ""
    # Cleanup task must have fired with the captured handle, not an empty one.
    assert fake_sprites.deleted == ["aod-dual"]


@pytest.mark.django_db
def test_terminate_falls_back_to_sprite_name_when_backend_handle_empty(
    client: Client, auth_headers, sprites_key, user, fake_sprites
):
    """In-flight sessions provisioned before the dual-write code shipped have
    ``backend_handle=""`` and ``sprite_name="..."``. Termination must read the
    legacy column or the backend resource is orphaned."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="t",
        sprite_name="legacy-handle",
        backend_handle="",
        status="completed",
    )
    resp = client.post(f"/sessions/{session.id}/terminate", **auth_headers)
    assert resp.status_code == 200
    assert fake_sprites.deleted == ["legacy-handle"]


@pytest.mark.django_db
def test_delete_session_signal_falls_back_to_sprite_name(
    client: Client, auth_headers, sprites_key, user, fake_sprites
):
    """The pre_delete signal that enqueues backend cleanup must also honour
    the fallback — a deleted in-flight session whose ``backend_handle`` was
    never set still needs its backend resource cleaned up."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="t",
        sprite_name="legacy-del",
        backend_handle="",
        status="completed",
    )
    resp = client.delete(f"/sessions/{session.id}/delete", **auth_headers)
    assert resp.status_code == 200
    assert fake_sprites.deleted == ["legacy-del"]
