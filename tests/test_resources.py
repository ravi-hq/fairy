import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fairy.models import Agent, AgentSession, APIKey, SessionResource, UserRuntimeKey
from fairy.runtimes import RUNTIMES
from fairy.sprites_exec import RepoSpec, build_wrapper_script


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
def runtime_key(user):
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


@pytest.fixture
def mock_sprites(mocker):
    """Mock the Sprites client so create_session doesn't hit a real API."""
    mock_sprite = mocker.MagicMock()
    mock_fs = mocker.MagicMock()
    mock_sprite.filesystem.return_value = mock_fs
    mock_fs.__truediv__ = mocker.Mock(return_value=mock_fs)
    mock_fs.write_text = mocker.Mock()
    mock_sprite.command.return_value.run = mocker.Mock()

    mock_client = mocker.MagicMock()
    mock_client.create_sprite.return_value = mock_sprite
    mocker.patch("fairy.views._get_client", return_value=mock_client)
    mocker.patch("fairy.views.threading.Thread")
    return mock_sprite, mock_fs


# --- Wrapper script tests ---


class TestBuildCloneSection:
    def test_no_repos_produces_no_clone_lines(self):
        config = RUNTIMES["claude"]
        script = build_wrapper_script(config, "sk-test", "hello", repos=[])
        assert "git clone" not in script

    def test_single_public_repo(self):
        config = RUNTIMES["claude"]
        repo = RepoSpec(url="https://github.com/org/repo", mount_path="/workspace/repo")
        script = build_wrapper_script(config, "sk-test", "hello", repos=[repo])
        assert "git clone --depth=1 --quiet" in script
        assert "/workspace/repo" in script
        assert ".git-credentials" not in script  # no token = no credentials file

    def test_single_private_repo_with_token(self):
        config = RUNTIMES["claude"]
        repo = RepoSpec(
            url="https://github.com/org/private-repo",
            mount_path="/workspace/private-repo",
            token="ghp_testtoken123",
        )
        script = build_wrapper_script(config, "sk-test", "hello", repos=[repo])
        assert "git clone --depth=1 --quiet" in script
        assert "/workspace/private-repo" in script
        # Token should be in .git-credentials, not in the clone URL
        assert ".git-credentials" in script
        assert "ghp_testtoken123" in script
        assert "credential.helper" in script
        # Credentials cleaned up after clone
        assert "rm -f /tmp/.git-credentials" in script
        assert "--unset credential.helper" in script

    def test_multiple_repos(self):
        config = RUNTIMES["claude"]
        repos = [
            RepoSpec(url="https://github.com/org/frontend", mount_path="/workspace/frontend"),
            RepoSpec(
                url="https://github.com/org/backend",
                mount_path="/workspace/backend",
                token="ghp_token",
            ),
        ]
        script = build_wrapper_script(config, "sk-test", "hello", repos=repos)
        assert script.count("git clone") == 2
        assert "/workspace/frontend" in script
        assert "/workspace/backend" in script

    def test_clone_section_before_exec(self):
        config = RUNTIMES["claude"]
        repo = RepoSpec(url="https://github.com/org/repo", mount_path="/workspace/repo")
        script = build_wrapper_script(config, "sk-test", "hello", repos=[repo])
        clone_pos = script.index("git clone")
        exec_pos = script.index("exec ")
        assert clone_pos < exec_pos

    def test_continue_session_no_repos(self):
        config = RUNTIMES["claude"]
        script = build_wrapper_script(config, "sk-test", "hello", continue_session=True, repos=[])
        assert "git clone" not in script
        assert "--continue" in script


# --- API validation tests ---


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
        self, client: Client, auth_headers, runtime_key, agent, mock_sprites
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

        # Response includes resources without token
        assert len(data["resources"]) == 1
        assert data["resources"][0]["type"] == "github_repository"
        assert data["resources"][0]["url"] == "https://github.com/org/repo"
        assert data["resources"][0]["mount_path"] == "/workspace/repo"
        assert "authorization_token" not in data["resources"][0]

        # Resource persisted in DB with encrypted token
        session = AgentSession.objects.get(pk=data["id"])
        sr = session.resources.first()
        assert sr.url == "https://github.com/org/repo"
        assert sr.mount_path == "/workspace/repo"
        assert sr.get_token() == "ghp_secret123"

    def test_create_session_without_resources_backward_compatible(
        self, client: Client, auth_headers, runtime_key, agent, mock_sprites
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
        self, client: Client, auth_headers, runtime_key, agent, mock_sprites
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
        self, client: Client, auth_headers, runtime_key, agent, mock_sprites
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
        self, client: Client, auth_headers, runtime_key, agent, mock_sprites
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
