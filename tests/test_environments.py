import json
import uuid

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import (
    Agent,
    AgentSession,
    AgentVersion,
    APIKey,
    Environment,
    EnvironmentVersion,
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
        environment=env,
        version=1,
        name=env.name,
        packages=env.packages,
        env_vars=env.env_vars,
        setup_script=env.setup_script,
        networking_type=env.networking_type,
        networking_config=env.networking_config,
    )
    return env


@pytest.fixture
def agent(user):
    return Agent.objects.create(
        user=user,
        name="Test Agent",
        model="claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )


# --- Provisioning stage tests (via recording fake) ---


def _index_of(cmds: list[str], needle: str) -> int:
    for i, c in enumerate(cmds):
        if needle in c:
            return i
    raise AssertionError(f"{needle!r} not found in commands: {cmds}")


@pytest.mark.django_db
class TestProvisioningStages:
    def test_packages_install_in_canonical_order(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
        env = Environment.objects.create(
            user=user,
            name="multi-pkg",
            packages={"pip": ["pandas"], "apt": ["ffmpeg"], "npm": ["express"]},
            version=1,
        )
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hi",
                    "environment_id": str(env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        cmds = fake_sprites.last_sprite().command_strings()
        # Order: apt → npm → pip (alphabetical across PACKAGE_MANAGER_ORDER)
        apt_i = _index_of(cmds, "apt-get install")
        npm_i = _index_of(cmds, "npm install --global")
        pip_i = _index_of(cmds, "pip install")
        assert apt_i < npm_i < pip_i

    def test_env_vars_written_to_env_file(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
        env = Environment.objects.create(
            user=user,
            name="vars",
            env_vars={"DATABASE_URL": "postgres://localhost/db", "DEBUG": "1"},
            version=1,
        )
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hi",
                    "environment_id": str(env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        env_file = fake_sprites.last_sprite().write_map()["/tmp/aod-env"]
        # API key is first, then alphabetical env vars
        assert "ANTHROPIC_API_KEY=" in env_file
        assert "DATABASE_URL=" in env_file
        assert "DEBUG=" in env_file

    def test_env_file_chmod_600(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hi"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        cmds = fake_sprites.last_sprite().command_strings()
        assert "chmod 600 /tmp/aod-env" in cmds

    def test_user_setup_script_runs_once(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
        env = Environment.objects.create(
            user=user,
            name="setup",
            setup_script="createdb myapp\necho done",
            version=1,
        )
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hi",
                    "environment_id": str(env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        cmds = fake_sprites.last_sprite().command_strings()
        setup_cmds = [c for c in cmds if "createdb myapp" in c]
        assert len(setup_cmds) == 1

    def test_stage_order_env_then_packages_then_clone_then_setup(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
        env = Environment.objects.create(
            user=user,
            name="full",
            packages={"pip": ["pandas"]},
            env_vars={"KEY": "val"},
            setup_script="echo setup",
            version=1,
        )
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hi",
                    "environment_id": str(env.id),
                    "resources": [
                        {"type": "github_repository", "url": "https://github.com/org/repo"}
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        cmds = fake_sprites.last_sprite().command_strings()
        chmod_env_i = _index_of(cmds, "chmod 600 /tmp/aod-env")
        pip_i = _index_of(cmds, "pip install")
        clone_i = _index_of(cmds, "git clone")
        setup_i = _index_of(cmds, "echo setup")
        run_chmod_i = _index_of(cmds, "chmod +x /run-agent.sh")
        assert chmod_env_i < pip_i < clone_i < setup_i < run_chmod_i

    def test_empty_environment_no_package_commands(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
        env = Environment.objects.create(
            user=user, name="empty", packages={}, env_vars={}, setup_script="", version=1
        )
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hi",
                    "environment_id": str(env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        cmds = fake_sprites.last_sprite().command_strings()
        assert not any("pip install" in c for c in cmds)
        assert not any("apt-get install" in c for c in cmds)

    def test_cargo_packages_each_get_own_command(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
        env = Environment.objects.create(
            user=user,
            name="cargo",
            packages={"cargo": ["ripgrep@14.0.0", "fd-find"]},
            version=1,
        )
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hi",
                    "environment_id": str(env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        cmds = fake_sprites.last_sprite().command_strings()
        cargo_cmds = [c for c in cmds if "cargo install" in c]
        assert len(cargo_cmds) == 2

    def test_gem_and_go_packages(
        self, client: Client, auth_headers, runtime_key, user, agent, fake_sprites
    ):
        env = Environment.objects.create(
            user=user,
            name="misc",
            packages={"gem": ["rails"], "go": ["golang.org/x/tools/cmd/goimports@latest"]},
            version=1,
        )
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hi",
                    "environment_id": str(env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        cmds = fake_sprites.last_sprite().command_strings()
        assert any("gem install rails" in c for c in cmds)
        assert any("go install" in c for c in cmds)


# --- Environment CRUD tests (HTTP layer, unchanged) ---


@pytest.mark.django_db
class TestCreateEnvironment:
    def test_create_full(self, client: Client, auth_headers):
        resp = client.post(
            "/environments",
            data=json.dumps(
                {
                    "name": "data-science",
                    "packages": {"pip": ["pandas", "numpy"], "apt": ["ffmpeg"]},
                    "env_vars": {"DB_URL": "postgres://...", "DEBUG": "1"},
                    "setup_script": "echo ready",
                    "networking": {"type": "unrestricted"},
                }
            ),
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
        assert "env_vars" not in data

        assert EnvironmentVersion.objects.filter(environment_id=data["id"], version=1).exists()

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
            data=json.dumps(
                {
                    "name": "restricted",
                    "networking": {
                        "type": "limited",
                        "allowed_hosts": ["api.example.com"],
                        "allow_package_managers": True,
                    },
                }
            ),
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
            data=json.dumps(
                {
                    "name": "bad",
                    "packages": {"homebrew": ["wget"]},
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "Unknown package manager" in str(resp.json()["detail"])

    def test_create_invalid_networking_type(self, client: Client, auth_headers):
        resp = client.post(
            "/environments",
            data=json.dumps(
                {
                    "name": "bad",
                    "networking": {"type": "open"},
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_create_invalid_json(self, client: Client, auth_headers):
        resp = client.post(
            "/environments",
            data="not json",
            content_type="application/json",
            **auth_headers,
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
            data=json.dumps(
                {
                    "version": 1,
                    "packages": {"pip": ["requests"]},
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["packages"] == {"pip": ["requests"]}
        assert data["version"] == 2

        assert EnvironmentVersion.objects.filter(environment=environment, version=2).exists()

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
            data=json.dumps(
                {
                    "version": 1,
                    "networking": {"type": "limited", "allowed_hosts": ["api.example.com"]},
                }
            ),
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
            data=json.dumps(
                {
                    "version": 1,
                    "networking": {"type": "unrestricted"},
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 1
        assert not EnvironmentVersion.objects.filter(environment=environment, version=2).exists()


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
            user=user,
            environment=environment,
            runtime="claude",
            prompt="test",
            status="completed",
        )
        resp = client.delete(f"/environments/{environment.id}/delete", **auth_headers)
        assert resp.status_code == 409
        assert "existing sessions" in resp.json()["detail"]


@pytest.mark.django_db
class TestEnvironmentVersions:
    def test_list_versions(self, client: Client, auth_headers, environment):
        environment.packages = {"pip": ["requests"]}
        environment.version = 2
        environment.save()
        EnvironmentVersion.objects.create(
            environment=environment,
            version=2,
            name=environment.name,
            packages=environment.packages,
            env_vars=environment.env_vars,
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
        self, client: Client, auth_headers, runtime_key, environment, agent, fake_sprites
    ):
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "analyze data",
                    "environment_id": str(environment.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["environment_id"] == str(environment.id)
        sprite = fake_sprites.last_sprite()
        cmds = sprite.command_strings()
        assert any("apt-get install" in c for c in cmds)
        assert any("pip install" in c for c in cmds)
        assert any("echo 'ready'" in c for c in cmds)
        assert "DATABASE_URL=" in sprite.write_map()["/tmp/aod-env"]

    def test_create_session_inherits_agent_environment(
        self, client: Client, auth_headers, runtime_key, environment, user, fake_sprites
    ):
        agent = Agent.objects.create(
            user=user,
            name="Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            environment=environment,
            version=1,
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["environment_id"] == str(environment.id)
        cmds = fake_sprites.last_sprite().command_strings()
        assert any("pip install" in c for c in cmds)

    def test_explicit_environment_overrides_agent(
        self, client: Client, auth_headers, runtime_key, environment, user, fake_sprites
    ):
        other_env = Environment.objects.create(
            user=user,
            name="other-env",
            packages={"npm": ["express"]},
            version=1,
        )
        agent = Agent.objects.create(
            user=user,
            name="Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            environment=environment,
            version=1,
        )
        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "environment_id": str(other_env.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["environment_id"] == str(other_env.id)
        cmds = fake_sprites.last_sprite().command_strings()
        assert any("npm install --global express" in c for c in cmds)
        assert not any("pandas" in c for c in cmds)

    def test_create_session_with_archived_environment_rejected(
        self, client: Client, auth_headers, runtime_key, environment, agent
    ):
        from django.utils import timezone

        environment.archived_at = timezone.now()
        environment.save()

        resp = client.post(
            "/sessions",
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "environment_id": str(environment.id),
                }
            ),
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
            data=json.dumps(
                {
                    "agent_id": str(agent.id),
                    "prompt": "hello",
                    "environment_id": str(uuid.uuid4()),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 404
        assert "Environment not found" in resp.json()["detail"]

    def test_get_session_includes_environment_id(
        self, client: Client, auth_headers, user, environment
    ):
        session = AgentSession.objects.create(
            user=user,
            environment=environment,
            runtime="claude",
            prompt="test",
            status="completed",
            exit_code=0,
        )
        resp = client.get(f"/sessions/{session.id}", **auth_headers)
        assert resp.status_code == 200
        assert resp.json()["environment_id"] == str(environment.id)

    def test_session_without_environment(
        self, client: Client, auth_headers, runtime_key, agent, fake_sprites
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
    def test_create_agent_with_environment(self, client: Client, auth_headers, environment):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "My Agent",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "environment_id": str(environment.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["environment_id"] == str(environment.id)

    def test_create_agent_without_environment(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "My Agent",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["environment_id"] is None

    def test_update_agent_environment(self, client: Client, auth_headers, environment, user):
        agent = Agent.objects.create(
            user=user,
            name="Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
        )
        AgentVersion.objects.create(
            agent=agent,
            version=1,
            name=agent.name,
            model=agent.model,
            runtime=agent.runtime,
        )

        resp = client.put(
            f"/agents/{agent.id}",
            data=json.dumps(
                {
                    "version": 1,
                    "environment_id": str(environment.id),
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["environment_id"] == str(environment.id)
        assert resp.json()["version"] == 2
