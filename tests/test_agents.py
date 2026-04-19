import json
import uuid

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import Agent, AgentVersion, APIKey, UserRuntimeKey, UserSpritesKey


@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", password="testpass")


@pytest.fixture
def api_key(user):
    instance, raw_key = APIKey.create_key(user, "test-key")
    return instance, raw_key


@pytest.fixture
def auth_headers(api_key):
    _, raw_key = api_key
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


@pytest.fixture
def sprites_key(user):
    usk = UserSpritesKey(user=user)
    usk.set_api_key("fake-sprites-token")
    usk.save()
    return usk


@pytest.fixture
def runtime_key(user, sprites_key):
    urk = UserRuntimeKey(user=user, runtime="claude")
    urk.set_api_key("fake-anthropic-key")
    urk.save()
    return urk


SAMPLE_SKILL = {
    "name": "web-search",
    "description": "Search the web for current information.",
    "content": "---\nname: web-search\ndescription: Search the web for current information.\n---\n\nUse this skill when the user asks for up-to-date information.\n",
}


@pytest.fixture
def agent(user):
    a = Agent.objects.create(
        user=user,
        name="Test Agent",
        description="A test agent",
        system="You are a helpful assistant.",
        model="claude-sonnet-4-6",
        runtime="claude",
        skills=[SAMPLE_SKILL],
        metadata={"team": "platform"},
        version=1,
    )
    AgentVersion.objects.create(
        agent=a,
        version=1,
        name=a.name,
        description=a.description,
        system=a.system,
        model=a.model,
        runtime=a.runtime,
        skills=a.skills,
        metadata=a.metadata,
    )
    return a


# --- Create ---


@pytest.mark.django_db
def test_create_agent(client: Client, auth_headers):
    resp = client.post(
        "/agents",
        data=json.dumps(
            {
                "name": "My Agent",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "system": "You are helpful.",
                "description": "Does things",
                "skills": [SAMPLE_SKILL],
                "metadata": {"env": "prod"},
            }
        ),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Agent"
    assert data["model"] == "claude-sonnet-4-6"
    assert data["runtime"] == "claude"
    assert data["system"] == "You are helpful."
    assert data["description"] == "Does things"
    assert data["skills"] == [SAMPLE_SKILL]
    assert data["metadata"] == {"env": "prod"}
    assert data["version"] == 1
    assert data["archived_at"] is None
    assert data["type"] == "agent"

    # Version record was created
    assert AgentVersion.objects.filter(agent_id=data["id"], version=1).exists()


@pytest.mark.django_db
def test_create_agent_minimal(client: Client, auth_headers):
    resp = client.post(
        "/agents",
        data=json.dumps({"name": "Minimal", "model": "claude-sonnet-4-6", "runtime": "claude"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Minimal"
    assert data["skills"] == []
    assert data["metadata"] == {}


@pytest.mark.django_db
def test_create_agent_invalid_model(client: Client, auth_headers):
    resp = client.post(
        "/agents",
        data=json.dumps({"name": "Bad", "model": "not-a-model", "runtime": "claude"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422
    assert "Unknown model" in str(resp.json()["detail"])


@pytest.mark.django_db
def test_create_agent_invalid_runtime(client: Client, auth_headers):
    resp = client.post(
        "/agents",
        data=json.dumps({"name": "Bad", "model": "claude-sonnet-4-6", "runtime": "nope"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 400
    assert "Unknown runtime" in resp.json()["detail"]


@pytest.mark.django_db
def test_create_agent_missing_fields(client: Client, auth_headers):
    resp = client.post(
        "/agents",
        data=json.dumps({"name": "Incomplete"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422


# --- List ---


@pytest.mark.django_db
def test_list_agents(client: Client, auth_headers, agent):
    resp = client.get("/agents", **auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["name"] == "Test Agent"


@pytest.mark.django_db
def test_list_agents_excludes_archived(client: Client, auth_headers, agent):
    from django.utils import timezone

    agent.archived_at = timezone.now()
    agent.save()

    resp = client.get("/agents", **auth_headers)
    assert resp.json()["data"] == []


@pytest.mark.django_db
def test_list_agents_other_user_not_visible(client: Client, auth_headers):
    other = User.objects.create_user(username="other", password="pass")
    Agent.objects.create(
        user=other,
        name="Other's Agent",
        model="x",
        runtime="claude",
        version=1,
    )
    resp = client.get("/agents", **auth_headers)
    assert resp.json()["data"] == []


# --- Get ---


@pytest.mark.django_db
def test_get_agent(client: Client, auth_headers, agent):
    resp = client.get(f"/agents/{agent.id}", **auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Agent"


@pytest.mark.django_db
def test_get_agent_not_found(client: Client, auth_headers):
    resp = client.get(f"/agents/{uuid.uuid4()}", **auth_headers)
    assert resp.status_code == 404


# --- Update ---


@pytest.mark.django_db
def test_update_agent(client: Client, auth_headers, agent):
    resp = client.put(
        f"/agents/{agent.id}",
        data=json.dumps({"version": 1, "system": "Updated system prompt."}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["system"] == "Updated system prompt."
    assert data["version"] == 2
    assert data["name"] == "Test Agent"  # unchanged

    # Version 2 was snapshotted
    assert AgentVersion.objects.filter(agent=agent, version=2).exists()


@pytest.mark.django_db
def test_update_agent_metadata_merge(client: Client, auth_headers, agent):
    resp = client.put(
        f"/agents/{agent.id}",
        data=json.dumps({"version": 1, "metadata": {"env": "staging", "new_key": "val"}}),
        content_type="application/json",
        **auth_headers,
    )
    data = resp.json()
    assert data["metadata"] == {"team": "platform", "env": "staging", "new_key": "val"}


@pytest.mark.django_db
def test_update_agent_metadata_delete_key(client: Client, auth_headers, agent):
    resp = client.put(
        f"/agents/{agent.id}",
        data=json.dumps({"version": 1, "metadata": {"team": ""}}),
        content_type="application/json",
        **auth_headers,
    )
    assert "team" not in resp.json()["metadata"]


@pytest.mark.django_db
def test_update_agent_no_change(client: Client, auth_headers, agent):
    """No-op update returns same version."""
    resp = client.put(
        f"/agents/{agent.id}",
        data=json.dumps({"version": 1, "name": "Test Agent"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.json()["version"] == 1


@pytest.mark.django_db
def test_update_agent_version_mismatch(client: Client, auth_headers, agent):
    resp = client.put(
        f"/agents/{agent.id}",
        data=json.dumps({"version": 99, "name": "New Name"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409
    assert "Version mismatch" in resp.json()["detail"]


@pytest.mark.django_db
def test_update_archived_agent(client: Client, auth_headers, agent):
    from django.utils import timezone

    agent.archived_at = timezone.now()
    agent.save()

    resp = client.put(
        f"/agents/{agent.id}",
        data=json.dumps({"version": 1, "name": "New"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409


# --- Archive ---


@pytest.mark.django_db
def test_archive_agent(client: Client, auth_headers, agent):
    resp = client.post(f"/agents/{agent.id}/archive", **auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["archived_at"] is not None

    agent.refresh_from_db()
    assert agent.is_archived


@pytest.mark.django_db
def test_archive_already_archived(client: Client, auth_headers, agent):
    from django.utils import timezone

    agent.archived_at = timezone.now()
    agent.save()

    resp = client.post(f"/agents/{agent.id}/archive", **auth_headers)
    assert resp.status_code == 409


# --- Versions ---


@pytest.mark.django_db
def test_list_versions(client: Client, auth_headers, agent):
    # Update to create version 2
    agent.system = "v2 prompt"
    agent.version = 2
    agent.save()
    AgentVersion.objects.create(
        agent=agent,
        version=2,
        name=agent.name,
        system="v2 prompt",
        model=agent.model,
        runtime=agent.runtime,
        skills=agent.skills,
        metadata=agent.metadata,
    )

    resp = client.get(f"/agents/{agent.id}/versions", **auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 2
    assert data[0]["version"] == 2
    assert data[1]["version"] == 1


# --- Session with agent_id ---


@pytest.mark.django_db
def test_create_session_with_agent(client: Client, auth_headers, agent, runtime_key, mocker):
    mock_sprite = mocker.MagicMock()
    mock_fs = mocker.MagicMock()
    mock_sprite.filesystem.return_value = mock_fs
    mock_fs.__truediv__ = mocker.Mock(return_value=mock_fs)
    mock_fs.write_text = mocker.Mock()
    mock_sprite.command.return_value.run = mocker.Mock()

    mock_client = mocker.MagicMock()
    mock_client.create_sprite.return_value = mock_sprite
    mocker.patch("agent_on_demand.views._get_client", return_value=mock_client)
    mocker.patch("agent_on_demand.views.threading.Thread")

    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "Fix the bug"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202

    # System prompt is prepended to the user prompt, written to the prompt
    # file on the Sprite. Call 0 is the script; call 1 is the prompt file.
    written_prompt = mock_fs.write_text.call_args_list[1][0][0]
    assert "You are a helpful assistant." in written_prompt
    assert "Fix the bug" in written_prompt


@pytest.mark.django_db
def test_create_session_with_agent_inherits_runtime(
    client: Client, auth_headers, agent, runtime_key, mocker
):
    mock_sprite = mocker.MagicMock()
    mock_fs = mocker.MagicMock()
    mock_sprite.filesystem.return_value = mock_fs
    mock_fs.__truediv__ = mocker.Mock(return_value=mock_fs)
    mock_fs.write_text = mocker.Mock()
    mock_sprite.command.return_value.run = mocker.Mock()

    mock_client = mocker.MagicMock()
    mock_client.create_sprite.return_value = mock_sprite
    mocker.patch("agent_on_demand.views._get_client", return_value=mock_client)
    mocker.patch("agent_on_demand.views.threading.Thread")

    # No runtime specified — should inherit from agent
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202


@pytest.mark.django_db
def test_create_session_missing_agent_id(client: Client, auth_headers):
    """agent_id is required for session creation."""
    resp = client.post(
        "/sessions",
        data=json.dumps({"prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_create_session_with_archived_agent(client: Client, auth_headers, agent, runtime_key):
    from django.utils import timezone

    agent.archived_at = timezone.now()
    agent.save()

    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409
    assert "archived" in resp.json()["detail"]
