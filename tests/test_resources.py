import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import (
    Agent,
    AgentSession,
    APIKey,
    SessionResource,
    UserRuntimeKey,
    UserSpritesKey,
)


# --- Fixtures ---


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


@pytest.fixture
def agent(user):
    return Agent.objects.create(
        user=user,
        name="Test Agent",
        model="claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )


# --- Clone-stage mechanics (via recording fake) ---


def _clone_commands(sprite) -> list[str]:
    return [c for c in sprite.shell_strings() if c.startswith("git clone")]


@pytest.mark.django_db
class TestCloneStage:
    def test_no_resources_issues_no_clone(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        assert _clone_commands(fake_sprites.last_sprite()) == []

    def test_public_repo_no_credentials_written(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": [
                        {"type": "github_repository", "url": "https://github.com/org/repo"}
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        sprite = fake_sprites.last_sprite()
        assert _clone_commands(sprite) == [
            "git clone --depth=1 --quiet https://github.com/org/repo /workspace/repo"
        ]
        assert "/tmp/.git-credentials" not in sprite.write_map()

    def test_private_repo_writes_credentials(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": [
                        {
                            "type": "github_repository",
                            "url": "https://github.com/org/private",
                            "authorization_token": "ghp_tok",
                        }
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        sprite = fake_sprites.last_sprite()
        creds = sprite.write_map().get("/tmp/.git-credentials")
        assert creds is not None
        assert "ghp_tok" in creds
        cmds = sprite.shell_strings()
        assert any("git config --global credential.helper" in c for c in cmds)
        # No explicit cleanup: the Sprite is session-scoped and torn down on
        # terminate/delete. Adding cleanup inside the provisioning script would
        # cost another sprite.command round trip for no practical benefit.

    def test_multiple_repos_all_cloned(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": [
                        {
                            "type": "github_repository",
                            "url": "https://github.com/org/frontend",
                        },
                        {
                            "type": "github_repository",
                            "url": "https://github.com/org/backend",
                            "authorization_token": "ghp_tok",
                        },
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        clones = _clone_commands(fake_sprites.last_sprite())
        assert len(clones) == 2
        assert any("frontend" in c for c in clones)
        assert any("backend" in c for c in clones)


# --- API validation tests (HTTP layer, unchanged) ---


@pytest.mark.django_db
class TestResourceValidation:
    def test_invalid_github_url_rejected(self, client: Client, auth_headers, runtime_key, agent):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": [
                        {"type": "github_repository", "url": "https://gitlab.com/org/repo"}
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_non_absolute_mount_path_rejected(
        self, client: Client, auth_headers, runtime_key, agent
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": [
                        {
                            "type": "github_repository",
                            "url": "https://github.com/org/repo",
                            "mount_path": "relative/path",
                        }
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_sprite_root_mount_path_rejected(
        self, client: Client, auth_headers, runtime_key, agent
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": [
                        {
                            "type": "github_repository",
                            "url": "https://github.com/org/repo",
                            "mount_path": "/home/sprite",
                        }
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_duplicate_mount_paths_rejected(self, client: Client, auth_headers, runtime_key, agent):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": [
                        {
                            "type": "github_repository",
                            "url": "https://github.com/org/repo1",
                            "mount_path": "/workspace/same",
                        },
                        {
                            "type": "github_repository",
                            "url": "https://github.com/org/repo2",
                            "mount_path": "/workspace/same",
                        },
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_too_many_resources_rejected(self, client: Client, auth_headers, runtime_key, agent):
        resources = [
            {
                "type": "github_repository",
                "url": f"https://github.com/org/repo{i}",
            }
            for i in range(11)
        ]
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": resources,
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422


# --- API integration tests ---


@pytest.mark.django_db
class TestResourcesIntegration:
    def test_create_session_with_resources(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "review the code",
                    "resources": [
                        {
                            "type": "github_repository",
                            "url": "https://github.com/org/repo",
                            "mount_path": "/workspace/repo",
                            "authorization_token": "ghp_secret123",
                        }
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()

        assert len(data["resources"]) == 1
        assert data["resources"][0]["type"] == "github_repository"
        assert data["resources"][0]["url"] == "https://github.com/org/repo"
        assert data["resources"][0]["mount_path"] == "/workspace/repo"
        assert "authorization_token" not in data["resources"][0]

        session = AgentSession.objects.get(pk=data["id"])
        sr = session.resources.first()
        assert sr.url == "https://github.com/org/repo"
        assert sr.mount_path == "/workspace/repo"
        assert sr.get_token() == "ghp_secret123"

    def test_create_session_without_resources_backward_compatible(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["resources"] == []

    def test_create_session_default_mount_path(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": [
                        {"type": "github_repository", "url": "https://github.com/org/my-repo"}
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["resources"][0]["mount_path"] == "/workspace/my-repo"

    def test_create_session_public_repo_no_token(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": [
                        {"type": "github_repository", "url": "https://github.com/org/public-repo"}
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        session = AgentSession.objects.get(pk=resp.json()["id"])
        sr = session.resources.first()
        assert sr.get_token() is None

    def test_get_session_includes_resources(self, client: Client, auth_headers, user):
        session = AgentSession.objects.create(
            user=user, runtime="claude", prompt="test", status="completed", exit_code=0
        )
        SessionResource.objects.create(
            session=session,
            resource_type="github_repository",
            url="https://github.com/org/repo",
            mount_path="/workspace/repo",
        )
        resp = client.get(f"/sessions/{session.id}", **auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["resources"]) == 1
        assert data["resources"][0]["url"] == "https://github.com/org/repo"

    def test_url_normalized_strips_dotgit(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "resources": [
                        {"type": "github_repository", "url": "https://github.com/org/repo.git"}
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["resources"][0]["url"] == "https://github.com/org/repo"
