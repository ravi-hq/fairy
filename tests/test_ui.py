import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import (
    Agent,
    AgentSession,
    APIKey,
    Environment,
    UserBackendCredential,
)


@pytest.fixture
def user(db):
    return User.objects.create_user(username="alice", password="alicepass123!")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(username="bob", password="bobpass123!")


@pytest.fixture
def logged_in_client(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_dashboard_requires_login(client: Client):
    resp = client.get("/ui/")
    assert resp.status_code == 302
    assert "/ui/login" in resp.url


@pytest.mark.django_db
def test_landing_is_public(client: Client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Agent on Demand" in body
    assert "sprites.dev" in body
    assert "/ui/register" in body
    assert "ravi-hq.github.io/agent-on-demand" in body


@pytest.mark.django_db
def test_landing_shows_dashboard_cta_when_logged_in(logged_in_client: Client):
    resp = logged_in_client.get("/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Open dashboard" in body
    assert "Create an account" not in body


@pytest.mark.django_db
def test_register_provisions_sprites_key_and_api_key(client: Client):
    resp = client.post(
        "/ui/register",
        data={
            "username": "charlie",
            "password1": "supersecret123!",
            "password2": "supersecret123!",
            "sprites_api_key": "sprites-token-xyz",
        },
    )
    assert resp.status_code == 302
    assert resp.url == "/ui/welcome"

    charlie = User.objects.get(username="charlie")
    cred = UserBackendCredential.objects.get(user=charlie, backend="sprites")
    assert cred.get_token() == "sprites-token-xyz"
    assert APIKey.objects.filter(user=charlie, is_active=True).count() == 1


@pytest.mark.django_db
def test_register_missing_sprites_key_rejected(client: Client):
    resp = client.post(
        "/ui/register",
        data={
            "username": "erin",
            "password1": "supersecret123!",
            "password2": "supersecret123!",
        },
    )
    assert resp.status_code == 200
    assert not User.objects.filter(username="erin").exists()


@pytest.mark.django_db
def test_register_mismatched_passwords_rejected(client: Client):
    resp = client.post(
        "/ui/register",
        data={
            "username": "dave",
            "password1": "a",
            "password2": "b",
            "sprites_api_key": "sprites-token",
        },
    )
    assert resp.status_code == 200
    assert not User.objects.filter(username="dave").exists()


@pytest.mark.django_db
def test_welcome_shows_raw_key_once(client: Client):
    resp = client.post(
        "/ui/register",
        data={
            "username": "frank",
            "password1": "supersecret123!",
            "password2": "supersecret123!",
            "sprites_api_key": "sprites-token",
        },
    )
    assert resp.status_code == 302

    resp = client.get("/ui/welcome")
    assert resp.status_code == 200
    assert b"aod_" in resp.content
    assert b"curl" in resp.content
    assert b"/ui/" in resp.content  # dashboard link

    resp = client.get("/ui/welcome")
    assert resp.status_code == 302
    assert resp.url == "/ui/"


@pytest.mark.django_db
def test_welcome_requires_login(client: Client):
    resp = client.get("/ui/welcome")
    assert resp.status_code == 302
    assert "/ui/login" in resp.url


@pytest.mark.django_db
def test_register_redirects_if_authenticated(logged_in_client):
    resp = logged_in_client.get("/ui/register")
    assert resp.status_code == 302
    assert resp.url == "/ui/"


@pytest.mark.django_db
def test_login_logout_flow(client: Client, user):
    resp = client.post("/ui/login", data={"username": "alice", "password": "alicepass123!"})
    assert resp.status_code == 302
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.content

    resp = client.post("/ui/logout")
    assert resp.status_code == 302
    resp = client.get("/ui/")
    assert resp.status_code == 302


@pytest.mark.django_db
def test_dashboard_flags_missing_sprites_key(logged_in_client):
    resp = logged_in_client.get("/ui/")
    assert resp.status_code == 200
    assert b"don't have a Sprites API token" in resp.content


@pytest.mark.django_db
def test_dashboard_hides_warning_when_key_set(logged_in_client, user):
    cred = UserBackendCredential(user=user, backend="sprites")
    cred.set_token("some-token")
    cred.save()
    resp = logged_in_client.get("/ui/")
    assert resp.status_code == 200
    assert b"don't have a Sprites API token" not in resp.content


@pytest.mark.django_db
def test_sprites_key_create_and_rotate(logged_in_client, user):
    resp = logged_in_client.post("/ui/sprites-key", data={"api_key": "first-token"})
    assert resp.status_code == 302
    cred = UserBackendCredential.objects.get(user=user, backend="sprites")
    assert cred.get_token() == "first-token"

    resp = logged_in_client.post("/ui/sprites-key", data={"api_key": "rotated-token"})
    assert resp.status_code == 302
    cred.refresh_from_db()
    assert cred.get_token() == "rotated-token"
    assert UserBackendCredential.objects.filter(user=user, backend="sprites").count() == 1


@pytest.mark.django_db
def test_api_key_create_shows_raw_once(logged_in_client, user):
    resp = logged_in_client.post(
        "/ui/api-keys", data={"name": "cli", "expires_at": ""}, follow=False
    )
    assert resp.status_code == 200
    assert b"Copy this key now" in resp.content
    assert APIKey.objects.filter(user=user, is_active=True).count() == 1


@pytest.mark.django_db
def test_api_key_revoke(logged_in_client, user):
    key, _raw = APIKey.create_key(user, "to-revoke")
    resp = logged_in_client.post(f"/ui/api-keys/{key.id}/revoke")
    assert resp.status_code == 302
    key.refresh_from_db()
    assert key.is_active is False


@pytest.mark.django_db
def test_api_key_revoke_scoped_to_owner(logged_in_client, other_user):
    key, _raw = APIKey.create_key(other_user, "not-mine")
    resp = logged_in_client.post(f"/ui/api-keys/{key.id}/revoke")
    assert resp.status_code == 404
    key.refresh_from_db()
    assert key.is_active is True


@pytest.mark.django_db
def test_agents_list_only_shows_owned(logged_in_client, user, other_user):
    mine = Agent.objects.create(
        user=user, name="mine", model="anthropic/claude-sonnet-4-6", runtime="claude", version=1
    )
    theirs = Agent.objects.create(
        user=other_user,
        name="theirs",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )
    resp = logged_in_client.get("/ui/agents")
    assert resp.status_code == 200
    assert mine.name.encode() in resp.content
    assert theirs.name.encode() not in resp.content


@pytest.mark.django_db
def test_agent_detail_404_for_other_user(logged_in_client, other_user):
    theirs = Agent.objects.create(
        user=other_user,
        name="theirs",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )
    resp = logged_in_client.get(f"/ui/agents/{theirs.id}")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_environment_list_and_detail(logged_in_client, user):
    env = Environment.objects.create(user=user, name="myenv", version=1)
    resp = logged_in_client.get("/ui/environments")
    assert resp.status_code == 200
    assert b"myenv" in resp.content

    resp = logged_in_client.get(f"/ui/environments/{env.id}")
    assert resp.status_code == 200
    assert b"myenv" in resp.content


@pytest.mark.django_db
def test_sessions_list_and_detail(logged_in_client, user):
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="hello world", status="completed", exit_code=0
    )
    resp = logged_in_client.get("/ui/sessions")
    assert resp.status_code == 200
    assert str(session.id)[:8].encode() in resp.content

    resp = logged_in_client.get(f"/ui/sessions/{session.id}")
    assert resp.status_code == 200
    assert b"hello world" in resp.content


@pytest.mark.django_db
def test_session_detail_404_for_other_user(logged_in_client, other_user):
    theirs = AgentSession.objects.create(
        user=other_user, runtime="claude", prompt="x", status="completed"
    )
    resp = logged_in_client.get(f"/ui/sessions/{theirs.id}")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_ui_does_not_expose_env_var_values(logged_in_client, user):
    env = Environment.objects.create(
        user=user,
        name="secrets",
        env_vars={"DATABASE_URL": "postgres://super-secret"},
        version=1,
    )
    resp = logged_in_client.get(f"/ui/environments/{env.id}")
    assert resp.status_code == 200
    assert b"DATABASE_URL" in resp.content
    assert b"super-secret" not in resp.content


# --- GET-form-render coverage ---
#
# Existing tests POST submissions and assert resulting state. The plain
# GET-renders that show empty forms had no coverage — a refactor that
# returned 500 (e.g. a template syntax error in the empty-state branch)
# would slip past CI.


@pytest.mark.django_db
def test_register_get_renders_form(client: Client):
    resp = client.get("/ui/register")
    assert resp.status_code == 200
    # The form template references the field id_username produced by Django.
    assert b"id_username" in resp.content


@pytest.mark.django_db
def test_sprites_key_get_renders_form(logged_in_client):
    resp = logged_in_client.get("/ui/sprites-key")
    assert resp.status_code == 200
    assert b"<form" in resp.content


@pytest.mark.django_db
def test_api_keys_get_renders_form(logged_in_client):
    resp = logged_in_client.get("/ui/api-keys")
    assert resp.status_code == 200
    assert b"<form" in resp.content


@pytest.mark.django_db
def test_agent_detail_renders_for_owner(logged_in_client, user):
    """`test_agent_detail_404_for_other_user` pins the negative case;
    this pins the positive render so a template-rendering regression
    can't slip past."""
    agent = Agent.objects.create(
        user=user,
        name="Owned-Agent",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )
    resp = logged_in_client.get(f"/ui/agents/{agent.id}")
    assert resp.status_code == 200
    assert b"Owned-Agent" in resp.content
