"""Cover error-path branches in views/sessions.py that test_api.py left untouched.

Each test pins a specific 4xx response shape — a refactor that returned
500 (or the wrong 4xx) would break SDK behavior in subtle ways. The
404/409/422 branches are particularly worth pinning because the SDK
parses `detail` strings to surface human-readable errors.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import (
    Agent,
    AgentSession,
    APIKey,
    SessionTurn,
    UserBackendCredential,
    UserCredential,
)


@pytest.fixture
def user(db):
    return User.objects.create_user(username="svuser", password="x")


@pytest.fixture
def auth_headers(user):
    _, raw = APIKey.create_key(user, "k")
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


@pytest.fixture
def sprites_key(user):
    cred = UserBackendCredential(user=user, backend="sprites")
    cred.set_token("fake-sprites")
    cred.save()
    return cred


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
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )


@pytest.mark.django_db
def test_sessions_collection_rejects_unknown_method(client: Client, auth_headers):
    """PATCH /sessions → 405. The collection endpoint dispatches on POST/GET
    and falls through; previously uncovered."""
    resp = client.patch("/sessions", **auth_headers)
    assert resp.status_code == 405
    assert resp.json()["detail"] == "Method not allowed"


@pytest.mark.django_db
def test_create_session_with_unknown_model_returns_422(client, auth_headers, runtime_key, user):
    """An agent persisted with a model string that's no longer in MODELS
    (e.g. catalog removal) must reject session creation up-front rather
    than silently failing later at provision time. Pin the contract so a
    refactor that elided the check leaves this stops at 422."""
    bogus_agent = Agent.objects.create(
        user=user,
        name="bogus-model",
        model="unknown/model-id-not-in-catalog",
        runtime="claude",
        version=1,
    )
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(bogus_agent.id), "prompt": "hi"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422
    assert "Unknown model" in resp.json()["detail"]


@pytest.mark.django_db
def test_create_session_with_runtime_provider_mismatch_returns_422(
    client, auth_headers, runtime_key, user
):
    """An agent persisted with a runtime/model whose provider isn't in
    runtime.providers must reject at session-create. Real failure mode:
    catalog edits where a model's provider was retroactively changed."""
    bad_agent = Agent.objects.create(
        user=user,
        name="bad",
        model="openai/gpt-4.1",
        runtime="claude",  # claude doesn't serve openai/*
        version=1,
    )
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(bad_agent.id), "prompt": "hi"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422
    assert "cannot serve" in resp.json()["detail"]


@pytest.mark.django_db
def test_create_session_with_runtime_no_longer_in_registry_returns_400(
    client, auth_headers, runtime_key, user
):
    """Agent rows persist forever; the runtime registry can change between
    deploys. An agent created when runtime "ghost" was valid and persisted
    in the DB after "ghost" was removed from RUNTIMES must reject session
    creation with a 400 listing the current valid runtimes — not a 500
    from a downstream KeyError on RUNTIMES[runtime].

    Reaches the branch by inserting an agent directly via the ORM with a
    runtime string the API validator would now reject. This is the only
    way that branch is reachable, since the create-agent path also rejects
    unknown runtimes."""
    ghost_agent = Agent.objects.create(
        user=user,
        name="ghost",
        model="anthropic/claude-sonnet-4-6",
        runtime="ghost-runtime",
        version=1,
    )
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(ghost_agent.id), "prompt": "hi"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Unknown runtime: ghost-runtime" in detail
    assert "Must be one of:" in detail


@pytest.mark.django_db
def test_send_prompt_session_not_found(client, auth_headers):
    fake = uuid.uuid4()
    resp = client.post(
        f"/sessions/{fake}/prompt",
        data=json.dumps({"prompt": "hi"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 404
    assert "Session not found" in resp.json()["detail"]


@pytest.mark.django_db
def test_send_prompt_invalid_json(client, auth_headers, user):
    """Invalid JSON on prompt endpoint must return 400, not 500."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="x", status="completed"
    )
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data="{not json",
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 400
    assert "Invalid JSON" in resp.json()["detail"]


@pytest.mark.django_db
def test_send_prompt_missing_required_field_returns_422(client, auth_headers, user):
    """PromptRequest requires `prompt`; sending an empty body must 422."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="x", status="completed"
    )
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_send_prompt_to_running_session_rejected(client, auth_headers, user):
    """check_can_accept_prompt rejects 'running' state — a turn is in flight,
    enqueueing another would cause the runtime CLI to clobber the in-progress
    session state."""
    session = AgentSession.objects.create(user=user, runtime="claude", prompt="x", status="running")
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "next"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409


@pytest.mark.django_db
def test_send_prompt_to_failed_session_rejected(client, auth_headers, user):
    """`failed` is terminal — the Sprite may have been left in a bad state
    by the failure. Resuming would risk colliding with whatever the runtime
    half-completed."""
    session = AgentSession.objects.create(user=user, runtime="claude", prompt="x", status="failed")
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "retry"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409


@pytest.mark.django_db
def test_send_prompt_without_backend_credential_returns_400(client, auth_headers, user):
    """A user with a `completed` session but no UserBackendCredential hits
    the `NoBackendCredentialsError` branch synchronously: `resume_session` →
    `require_client` (session_service/client.py) raises before the view
    ever returns, so the 400 path runs entirely in the request thread —
    there is no worker dispatch involved. Without this branch, the SDK
    sees an opaque 5xx instead of an actionable "configure backend
    credentials" message.

    Pin the exact `detail` so a rename of the error message string in
    client.py breaks this test loudly, rather than silently drifting away
    from the API contract SDK clients parse against."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="x", status="completed", backend_handle="s"
    )
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "next"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "No backend credentials configured"


@pytest.mark.django_db
def test_send_prompt_when_sprite_is_gone_returns_409(
    client, auth_headers, runtime_key, user, mocker
):
    """When the Sprite backing a session has been reaped (idle timeout on
    the Sprites platform, manual deletion), `resume_session` raises
    `SessionHandleNotFound`. The session row still exists, so 404 would
    be misleading — callers couldn't distinguish "session not found" from
    "Sprite no longer available". The contract is 409 with an actionable
    message; SDK clients parse `detail` to surface "start a new session"
    guidance to the user."""
    from agent_on_demand.session_service.errors import SessionHandleNotFound

    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="x", status="completed", backend_handle="s"
    )
    mocker.patch(
        "agent_on_demand.views.sessions.session_service.resume_session",
        side_effect=SessionHandleNotFound("Sprite not found: gone"),
    )
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "next"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409
    assert "Session backend is no longer available" in resp.json()["detail"]
    assert "start a new session" in resp.json()["detail"]


@pytest.mark.django_db
def test_send_prompt_session_deleted_between_pre_lock_and_lock_returns_404(
    client, auth_headers, runtime_key, user, mocker
):
    """send_prompt has a pre-lock `get` at the top and a post-lock
    `select_for_update().get()` inside the transaction. If the session
    row is deleted between those two reads, the inner get raises
    DoesNotExist and the catch returns a clean 404 instead of letting
    a 500 escape from inside the transaction.

    Reaches the branch by patching `select_for_update` so the lock-
    time fetch raises DoesNotExist while the pre-lock fetch returns a
    real row. Uses `MagicMock` (not a hand-rolled stub) so the patched
    queryset transparently accepts any chained call the production
    code might add later (e.g. `select_for_update().filter(...).get()`)
    rather than failing with a confusing AttributeError unrelated to
    the branch under test."""
    from unittest.mock import MagicMock

    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="x", status="completed", backend_handle="s"
    )
    fake_sprite = object()
    mocker.patch(
        "agent_on_demand.views.sessions.session_service.resume_session",
        return_value=fake_sprite,
    )

    locked_qs = MagicMock()
    locked_qs.get.side_effect = AgentSession.DoesNotExist
    mocker.patch.object(AgentSession.objects, "select_for_update", return_value=locked_qs)

    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "next"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Session not found"


@pytest.mark.django_db
def test_send_prompt_post_lock_status_change_returns_409(
    client, auth_headers, runtime_key, user, mocker
):
    """send_prompt runs check_can_accept_prompt twice — once before
    acquiring the row lock (fast-fail) and once after (race-safe). If a
    concurrent worker transitioned the session from 'completed' to
    'running' between those two checks, the post-lock check must reject
    with the same 409 message — without this branch, a duplicate turn
    would be enqueued for a session whose worker is already mid-flight,
    risking a second runtime CLI invocation against the same Sprite.

    Reaches the branch by patching `check_can_accept_prompt` to return
    None on the first call (pre-lock pass) and a 409 on the second call
    (post-lock rejection), simulating the racing transition."""
    from django.http import JsonResponse

    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="x", status="completed", backend_handle="s"
    )
    fake_sprite = object()
    mocker.patch(
        "agent_on_demand.views.sessions.session_service.resume_session",
        return_value=fake_sprite,
    )
    call = {"n": 0}

    def staggered_check(status):
        call["n"] += 1
        if call["n"] == 1:
            return None
        return JsonResponse({"detail": "Session is already running"}, status=409)

    mocker.patch(
        "agent_on_demand.views.sessions.check_can_accept_prompt",
        side_effect=staggered_check,
    )
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "next"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409
    assert "Session is already running" in resp.json()["detail"]
    # Both checks must have run — pre-lock passes, post-lock rejects.
    assert call["n"] == 2


@pytest.mark.django_db
def test_send_prompt_post_lock_pending_race_returns_409(
    client, auth_headers, runtime_key, user, mocker
):
    """check_can_accept_prompt allows 'pending' (it's the initial state of
    a fresh session). But under concurrent send_prompt calls, the second
    arrival's pre-lock check sees the same accepting status as the first
    — and only the explicit `if locked.status == 'pending'` check after
    the lock distinguishes them. Without this branch, two concurrent
    callers would each enqueue a turn against the same session.

    Reaches the branch by patching the queryset chain so the locked
    session row reports status='pending', simulating a sibling caller
    who won the lock first and transitioned the row."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="x", status="completed", backend_handle="s"
    )
    fake_sprite = object()
    mocker.patch(
        "agent_on_demand.views.sessions.session_service.resume_session",
        return_value=fake_sprite,
    )

    class FakeLockedQS:
        def get(self, **kwargs):
            locked = AgentSession.objects.get(pk=session.id)
            locked.status = "pending"
            return locked

    mocker.patch.object(AgentSession.objects, "select_for_update", return_value=FakeLockedQS())
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "next"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409
    assert "Session already has a pending turn" in resp.json()["detail"]


@pytest.mark.django_db
def test_list_session_turns_not_found(client, auth_headers):
    resp = client.get(f"/sessions/{uuid.uuid4()}/turns", **auth_headers)
    assert resp.status_code == 404
    assert "Session not found" in resp.json()["detail"]


@pytest.mark.django_db
def test_list_session_turns_returns_ordered_history(client, auth_headers, user):
    """Turns must come back ordered by turn_number — SDK consumers rely on
    the order to reconstruct conversation history."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="x", status="completed"
    )
    SessionTurn.objects.create(session=session, turn_number=2, prompt="b", status="completed")
    SessionTurn.objects.create(session=session, turn_number=1, prompt="a", status="completed")
    resp = client.get(f"/sessions/{session.id}/turns", **auth_headers)
    assert resp.status_code == 200
    nums = [t["turn_number"] for t in resp.json()["data"]]
    assert nums == [1, 2]


@pytest.mark.django_db
def test_list_session_turns_other_user_returns_404(client, auth_headers, user):
    """Cross-user listing must look indistinguishable from missing — same
    contract as session detail and delete: don't leak existence."""
    other = User.objects.create_user(username="other-stl", password="x")
    theirs = AgentSession.objects.create(
        user=other, runtime="claude", prompt="x", status="completed"
    )
    SessionTurn.objects.create(session=theirs, turn_number=1, prompt="p", status="completed")
    resp = client.get(f"/sessions/{theirs.id}/turns", **auth_headers)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_terminate_session_not_found(client, auth_headers):
    resp = client.post(f"/sessions/{uuid.uuid4()}/terminate", **auth_headers)
    assert resp.status_code == 404
