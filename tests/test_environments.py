import json
import uuid

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fairy.models import (
    Agent, AgentSession, AgentVersion, APIKey, Environment,
    EnvironmentVersion, UserRuntimeKey,
)
from fairy.runtimes import RUNTIMES
from fairy.sprites_exec import EnvironmentSetup, build_wrapper_script


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
def environment(user):
    env = Environment.objects.create(
        user=user,
        name="test-env",
        packages={"pip": ["pandas", "numpy"], "apt": ["ffmpeg"]},
        env_vars={"DATABASE_URL": "postgres://localhost/db", "DEBUG": "1"},
        setup_script="echo 'ready'",
        networking_type="unrestricted",
        version=1,
    )
    EnvironmentVersion.objects.create(
        environment=env, version=1, name=env.name,
        packages=env.packages, env_vars=env.env_vars,
        setup_script=env.setup_script, networking_type=env.networking_type,
        networking_config=env.networking_config,
    )
    return env


@pytest.fixture
def agent(user):
    return Agent.objects.create(
        user=user, name="Test Agent", model="claude-sonnet-4-6",
        runtime="claude", version=1,
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


class TestWrapperScriptWithEnvironment:
    def test_packages_in_correct_order(self):
        config = RUNTIMES["claude"]
        env = EnvironmentSetup(
            packages={"pip": ["pandas"], "apt": ["ffmpeg"], "npm": ["express"]},
            env_vars={},
            setup_script="",
        )
        script = build_wrapper_script(config, "sk-test", "hello", environment=env)
        apt_pos = script.index("apt-get")
        npm_pos = script.index("npm install")
        pip_pos = script.index("pip install")
        assert apt_pos < npm_pos < pip_pos

    def test_env_vars_exported(self):
        config = RUNTIMES["claude"]
        env = EnvironmentSetup(
            packages={},
            env_vars={"DATABASE_URL": "postgres://localhost/db", "DEBUG": "1"},
            setup_script="",
        )
        script = build_wrapper_script(config, "sk-test", "hello", environment=env)
        assert "export DATABASE_URL=" in script
        assert "export DEBUG=" in script

    def test_setup_script_present(self):
        config = RUNTIMES["claude"]
        env = EnvironmentSetup(
            packages={},
            env_vars={},
            setup_script="createdb myapp\necho done",
        )
        script = build_wrapper_script(config, "sk-test", "hello", environment=env)
        assert "createdb myapp" in script
        assert "echo done" in script

    def test_section_order(self):
        """env vars → packages → clone → setup script → exec."""
        config = RUNTIMES["claude"]
        from fairy.sprites_exec import RepoSpec
        env = EnvironmentSetup(
            packages={"pip": ["pandas"]},
            env_vars={"KEY": "val"},
            setup_script="echo setup",
        )
        repo = RepoSpec(url="https://github.com/org/repo", mount_path="/workspace/repo")
        script = build_wrapper_script(
            config, "sk-test", "hello", repos=[repo], environment=env
        )
        env_pos = script.index("export KEY=")
        pkg_pos = script.index("pip install")
        clone_pos = script.index("git clone")
        setup_pos = script.index("echo setup")
        exec_pos = script.index("exec ")
        assert env_pos < pkg_pos < clone_pos < setup_pos < exec_pos

    def test_no_environment_backward_compat(self):
        config = RUNTIMES["claude"]
        script = build_wrapper_script(config, "sk-test", "hello")
        assert "pip install" not in script
        assert "apt-get" not in script
        assert "# Custom setup" not in script

    def test_empty_environment(self):
        config = RUNTIMES["claude"]
        env = EnvironmentSetup(packages={}, env_vars={}, setup_script="")
        script = build_wrapper_script(config, "sk-test", "hello", environment=env)
        assert "pip install" not in script
        assert "# Custom setup" not in script

    def test_cargo_packages(self):
        config = RUNTIMES["claude"]
        env = EnvironmentSetup(
            packages={"cargo": ["ripgrep@14.0.0", "fd-find"]},
            env_vars={},
            setup_script="",
        )
        script = build_wrapper_script(config, "sk-test", "hello", environment=env)
        assert "cargo install" in script
        assert script.count("cargo install") == 2

    def test_gem_packages(self):
        config = RUNTIMES["claude"]
        env = EnvironmentSetup(
            packages={"gem": ["rails", "bundler"]},
            env_vars={},
            setup_script="",
        )
        script = build_wrapper_script(config, "sk-test", "hello", environment=env)
        assert "gem install --silent" in script

    def test_go_packages(self):
        config = RUNTIMES["claude"]
        env = EnvironmentSetup(
            packages={"go": ["golang.org/x/tools/cmd/goimports@latest"]},
            env_vars={},
            setup_script="",
        )
        script = build_wrapper_script(config, "sk-test", "hello", environment=env)
        assert "go install" in script


# --- Environment CRUD tests ---


@pytest.mark.django_db
class TestCreateEnvironment:
    def test_create_full(self, client: Client, auth_headers):
        resp = client.post(
            "/environments",
            data=json.dumps({
                "name": "data-science",
                "packages": {"pip": ["pandas", "numpy"], "apt": ["ffmpeg"]},
                "env_vars": {"DB_URL": "postgres://...", "DEBUG": "1"},
                "setup_script": "echo ready",
                "networking": {"type": "unrestricted"},
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "data-science"
        assert data["packages"] == {"pip": ["pandas", "numpy"], "apt": ["ffmpeg"]}
        assert data["setup_script"] == "echo ready"
        assert data["networking"] == {"type": "unrestricted"}
        assert data["version"] == 1
        assert data["type"] == "environment"
        assert data["archived_at"] is None
        # env_vars must NOT be in response
        assert "env_vars" not in data

        # Version record was created
        assert EnvironmentVersion.objects.filter(
            environment_id=data["id"], version=1
        ).exists()

    def test_create_minimal(self, client: Client, auth_headers):
        resp = client.post(
            "/environments",
            data=json.dumps({"name": "minimal"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "minimal"
        assert data["packages"] == {}
        assert data["setup_script"] is None
        assert data["networking"] == {"type": "unrestricted"}

    def test_create_limited_networking(self, client: Client, auth_headers):
        resp = client.post(
            "/environments",
            data=json.dumps({
                "name": "restricted",
                "networking": {
                    "type": "limited",
                    "allowed_hosts": ["api.example.com"],
                    "allow_package_managers": True,
                },
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["networking"]["type"] == "limited"
        assert data["networking"]["allowed_hosts"] == ["api.example.com"]

    def test_create_invalid_package_manager(self, client: Client, auth_headers):
        resp = client.post(
            "/environments",
            data=json.dumps({
                "name": "bad",
                "packages": {"homebrew": ["wget"]},
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "Unknown package manager" in str(resp.json()["detail"])

    def test_create_invalid_networking_type(self, client: Client, auth_headers):
        resp = client.post(
            "/environments",
            data=json.dumps({
                "name": "bad",
                "networking": {"type": "open"},
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_create_invalid_json(self, client: Client, auth_headers):
        resp = client.post(
            "/environments", data="not json",
            content_type="application/json", **auth_headers,
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestListEnvironments:
    def test_list(self, client: Client, auth_headers, environment):
        resp = client.get("/environments", **auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["name"] == "test-env"

    def test_list_excludes_archived(self, client: Client, auth_headers, environment):
        from django.utils import timezone
        environment.archived_at = timezone.now()
        environment.save()

        resp = client.get("/environments", **auth_headers)
        assert resp.json()["data"] == []

    def test_list_other_user_not_visible(self, client: Client, auth_headers):
        other = User.objects.create_user(username="other", password="pass")
        Environment.objects.create(user=other, name="other-env", version=1)

        resp = client.get("/environments", **auth_headers)
        assert resp.json()["data"] == []


@pytest.mark.django_db
class TestGetEnvironment:
    def test_get(self, client: Client, auth_headers, environment):
        resp = client.get(f"/environments/{environment.id}", **auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-env"
        assert "env_vars" not in data

    def test_get_not_found(self, client: Client, auth_headers):
        resp = client.get(f"/environments/{uuid.uuid4()}", **auth_headers)
        assert resp.status_code == 404


@pytest.mark.django_db
class TestUpdateEnvironment:
    def test_update_packages(self, client: Client, auth_headers, environment):
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({
                "version": 1,
                "packages": {"pip": ["requests"]},
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["packages"] == {"pip": ["requests"]}
        assert data["version"] == 2

        assert EnvironmentVersion.objects.filter(
            environment=environment, version=2
        ).exists()

    def test_update_version_mismatch(self, client: Client, auth_headers, environment):
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({"version": 99, "name": "new"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 409
        assert "Version mismatch" in resp.json()["detail"]

    def test_update_archived(self, client: Client, auth_headers, environment):
        from django.utils import timezone
        environment.archived_at = timezone.now()
        environment.save()

        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({"version": 1, "name": "new"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 409

    def test_update_no_change(self, client: Client, auth_headers, environment):
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({"version": 1, "name": "test-env"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.json()["version"] == 1

    def test_update_networking_type_increments_version(
        self, client: Client, auth_headers, environment
    ):
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({
                "version": 1,
                "networking": {"type": "limited", "allowed_hosts": ["api.example.com"]},
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 2
        assert data["networking"]["type"] == "limited"
        assert data["networking"]["allowed_hosts"] == ["api.example.com"]

        new_version = EnvironmentVersion.objects.get(environment=environment, version=2)
        assert new_version.networking_type == "limited"
        assert new_version.networking_config == {"allowed_hosts": ["api.example.com"]}

    def test_update_networking_no_change_keeps_version(
        self, client: Client, auth_headers, environment
    ):
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({
                "version": 1,
                "networking": {"type": "unrestricted"},
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 1
        assert not EnvironmentVersion.objects.filter(
            environment=environment, version=2
        ).exists()


@pytest.mark.django_db
class TestEnvironmentLifecycle:
    def test_archive(self, client: Client, auth_headers, environment):
        resp = client.post(f"/environments/{environment.id}/archive", **auth_headers)
        assert resp.status_code == 200
        assert resp.json()["archived_at"] is not None

        environment.refresh_from_db()
        assert environment.is_archived

    def test_archive_already_archived(self, client: Client, auth_headers, environment):
        from django.utils import timezone
        environment.archived_at = timezone.now()
        environment.save()

        resp = client.post(f"/environments/{environment.id}/archive", **auth_headers)
        assert resp.status_code == 409

    def test_delete_no_sessions(self, client: Client, auth_headers, environment):
        resp = client.delete(f"/environments/{environment.id}/delete", **auth_headers)
        assert resp.status_code == 200
        assert not Environment.objects.filter(pk=environment.id).exists()

    def test_delete_with_sessions_rejected(self, client: Client, auth_headers, environment, user):
        AgentSession.objects.create(
            user=user, environment=environment,
            runtime="claude", prompt="test", status="completed",
        )
        resp = client.delete(f"/environments/{environment.id}/delete", **auth_headers)
        assert resp.status_code == 409
        assert "existing sessions" in resp.json()["detail"]


@pytest.mark.django_db
class TestEnvironmentVersions:
    def test_list_versions(self, client: Client, auth_headers, environment):
        # Update to create version 2
        environment.packages = {"pip": ["requests"]}
        environment.version = 2
        environment.save()
        EnvironmentVersion.objects.create(
            environment=environment, version=2, name=environment.name,
            packages=environment.packages, env_vars=environment.env_vars,
            setup_script=environment.setup_script,
            networking_type=environment.networking_type,
            networking_config=environment.networking_config,
        )

        resp = client.get(f"/environments/{environment.id}/versions", **auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["version"] == 2
        assert data[1]["version"] == 1


# --- Session + Environment integration ---


@pytest.mark.django_db
class TestSessionEnvironmentIntegration:
    def test_create_session_with_environment(
        self, client: Client, auth_headers, runtime_key, environment, agent, mock_sprites
    ):
        _, mock_fs = mock_sprites
        resp = client.post(
            "/sessions",
            data=json.dumps({
                "agent_id": str(agent.id),
                "prompt": "analyze data",
                "environment_id": str(environment.id),
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["environment_id"] == str(environment.id)

        # Verify wrapper script includes environment setup
        written_script = mock_fs.write_text.call_args[0][0]
        assert "apt-get" in written_script
        assert "pip install" in written_script
        assert "DATABASE_URL" in written_script
        assert "echo 'ready'" in written_script

    def test_create_session_inherits_agent_environment(
        self, client: Client, auth_headers, runtime_key, environment, user, mock_sprites
    ):
        agent = Agent.objects.create(
            user=user, name="Agent", model="claude-sonnet-4-6",
            runtime="claude", environment=environment, version=1,
        )
        _, mock_fs = mock_sprites
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["environment_id"] == str(environment.id)

        written_script = mock_fs.write_text.call_args[0][0]
        assert "pip install" in written_script

    def test_explicit_environment_overrides_agent(
        self, client: Client, auth_headers, runtime_key, environment, user, mock_sprites
    ):
        other_env = Environment.objects.create(
            user=user, name="other-env",
            packages={"npm": ["express"]}, version=1,
        )
        agent = Agent.objects.create(
            user=user, name="Agent", model="claude-sonnet-4-6",
            runtime="claude", environment=environment, version=1,
        )
        _, mock_fs = mock_sprites
        resp = client.post(
            "/sessions",
            data=json.dumps({
                "agent_id": str(agent.id),
                "prompt": "hello",
                "environment_id": str(other_env.id),
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["environment_id"] == str(other_env.id)

        written_script = mock_fs.write_text.call_args[0][0]
        assert "npm install" in written_script
        # Should NOT have the original environment's pip packages
        assert "pandas" not in written_script

    def test_create_session_with_archived_environment_rejected(
        self, client: Client, auth_headers, runtime_key, environment, agent
    ):
        from django.utils import timezone
        environment.archived_at = timezone.now()
        environment.save()

        resp = client.post(
            "/sessions",
            data=json.dumps({
                "agent_id": str(agent.id),
                "prompt": "hello",
                "environment_id": str(environment.id),
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 409
        assert "archived environment" in resp.json()["detail"]

    def test_create_session_environment_not_found(
        self, client: Client, auth_headers, runtime_key, agent
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps({
                "agent_id": str(agent.id),
                "prompt": "hello",
                "environment_id": str(uuid.uuid4()),
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 404
        assert "Environment not found" in resp.json()["detail"]

    def test_get_session_includes_environment_id(
        self, client: Client, auth_headers, user, environment
    ):
        session = AgentSession.objects.create(
            user=user, environment=environment,
            runtime="claude", prompt="test", status="completed", exit_code=0,
        )
        resp = client.get(f"/sessions/{session.id}", **auth_headers)
        assert resp.status_code == 200
        assert resp.json()["environment_id"] == str(environment.id)

    def test_session_without_environment(
        self, client: Client, auth_headers, runtime_key, agent, mock_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        assert resp.json()["environment_id"] is None


# --- Agent + Environment integration ---


@pytest.mark.django_db
class TestAgentEnvironmentIntegration:
    def test_create_agent_with_environment(
        self, client: Client, auth_headers, environment
    ):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "My Agent",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "environment_id": str(environment.id),
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["environment_id"] == str(environment.id)

    def test_create_agent_without_environment(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "My Agent",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["environment_id"] is None

    def test_update_agent_environment(
        self, client: Client, auth_headers, environment, user
    ):
        agent = Agent.objects.create(
            user=user, name="Agent", model="claude-sonnet-4-6",
            runtime="claude", version=1,
        )
        AgentVersion.objects.create(
            agent=agent, version=1, name=agent.name,
            model=agent.model, runtime=agent.runtime,
        )

        resp = client.put(
            f"/agents/{agent.id}",
            data=json.dumps({
                "version": 1,
                "environment_id": str(environment.id),
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["environment_id"] == str(environment.id)
        assert resp.json()["version"] == 2
