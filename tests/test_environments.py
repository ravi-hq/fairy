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
    UserCredential,
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
    cred = UserCredential(user=user, kind="provider:anthropic")
    cred.set_value("fake-anthropic-key")
    cred.save()
    return cred


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
        model="anthropic/claude-sonnet-4-6",
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
        cmds = fake_sprites.last_sprite().shell_strings()
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
        cmds = fake_sprites.last_sprite().shell_strings()
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
        cmds = fake_sprites.last_sprite().shell_strings()
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
        cmds = fake_sprites.last_sprite().shell_strings()
        chmod_env_i = _index_of(cmds, "chmod 600 /tmp/aod-env")
        pip_i = _index_of(cmds, "pip install")
        clone_i = _index_of(cmds, "git clone")
        setup_i = _index_of(cmds, "echo setup")
        assert chmod_env_i < pip_i < clone_i < setup_i

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
        cmds = fake_sprites.last_sprite().shell_strings()
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
        cmds = fake_sprites.last_sprite().shell_strings()
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
        cmds = fake_sprites.last_sprite().shell_strings()
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

    def test_create_duplicate_active_name(self, client: Client, auth_headers, environment):
        resp = client.post(
            "/environments",
            data=json.dumps({"name": environment.name}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 409
        assert environment.name in resp.json()["detail"]
        assert Environment.objects.filter(user=environment.user, name=environment.name).count() == 1

    def test_create_reuses_archived_name(self, client: Client, auth_headers, environment):
        from django.utils import timezone

        environment.archived_at = timezone.now()
        environment.save()

        resp = client.post(
            "/environments",
            data=json.dumps({"name": environment.name}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == environment.name


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

    def test_update_rename_to_existing_active_name(self, client: Client, auth_headers, environment):
        other = Environment.objects.create(user=environment.user, name="other-env", version=1)

        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({"version": 1, "name": other.name}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 409
        assert other.name in resp.json()["detail"]

        environment.refresh_from_db()
        assert environment.name == "test-env"
        assert environment.version == 1
        assert not EnvironmentVersion.objects.filter(environment=environment, version=2).exists()

    def test_update_rename_to_archived_name_succeeds(
        self, client: Client, auth_headers, environment
    ):
        from django.utils import timezone

        Environment.objects.create(
            user=environment.user,
            name="taken-then-archived",
            version=1,
            archived_at=timezone.now(),
        )

        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({"version": 1, "name": "taken-then-archived"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "taken-then-archived"


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
class TestEnvironmentErrorPaths:
    """Pin previously-uncovered error paths in views/environments.py.

    Covers the 405/404/422/400 branches across collection, detail, archive,
    delete, and versions endpoints. Each branch was reachable but had no
    test, so a refactor that returned the wrong status code (or a 500)
    would land silently.
    """

    def test_collection_rejects_unknown_method(self, client: Client, auth_headers):
        """PATCH /environments → 405."""
        resp = client.patch("/environments", **auth_headers)
        assert resp.status_code == 405
        assert resp.json()["detail"] == "Method not allowed"

    def test_detail_rejects_unknown_method(self, client: Client, auth_headers, environment):
        """DELETE /environments/{id} on the detail endpoint → 405. The dedicated
        delete endpoint is /environments/{id}/delete; this differs from agents
        which has no delete at all."""
        resp = client.delete(f"/environments/{environment.id}", **auth_headers)
        assert resp.status_code == 405

    def test_update_invalid_json(self, client: Client, auth_headers, environment):
        resp = client.put(
            f"/environments/{environment.id}",
            data="{not json",
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 400
        assert "Invalid JSON" in resp.json()["detail"]

    def test_update_invalid_package_manager_returns_422(
        self, client: Client, auth_headers, environment
    ):
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({"version": environment.version, "packages": {"unknown_mgr": ["x"]}}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_update_invalid_env_var_key_returns_422(
        self, client: Client, auth_headers, environment
    ):
        """env_var keys must match [A-Za-z_][A-Za-z0-9_]* — keys with hyphens
        or starting with digits are rejected at the validator level."""
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({"version": environment.version, "env_vars": {"BAD-KEY": "v"}}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_update_invalid_networking_type_returns_422(
        self, client: Client, auth_headers, environment
    ):
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({"version": environment.version, "networking": {"type": "wide-open"}}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_update_limited_networking_with_non_list_hosts_returns_422(
        self, client: Client, auth_headers, environment
    ):
        """allowed_hosts must be a list — a string would silently bypass the
        firewall config for limited networking."""
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps(
                {
                    "version": environment.version,
                    "networking": {"type": "limited", "allowed_hosts": "not-a-list"},
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_create_packages_non_string_list_returns_422(self, client: Client, auth_headers):
        """packages.<mgr> must be list[str] — a list of dicts or numbers must
        not coerce silently."""
        resp = client.post(
            "/environments",
            data=json.dumps({"name": "bad", "packages": {"pip": [123]}}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_create_env_vars_invalid_key_returns_422(self, client: Client, auth_headers):
        """env_vars keys must match [A-Za-z_][A-Za-z0-9_]*. Hyphens or
        leading digits would corrupt the /tmp/aod-env shell file when
        written as `KEY=value` lines and could be sourced into the
        agent's process environment as a different name. Pin the
        rejection on the create path — the update path is already
        tested but this validator runs on both."""
        resp = client.post(
            "/environments",
            data=json.dumps(
                {
                    "name": "bad",
                    "env_vars": {"BAD-KEY": "v"},
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "BAD-KEY" in str(resp.json()["detail"])

    def test_create_limited_networking_with_non_list_hosts_returns_422(
        self, client: Client, auth_headers
    ):
        """allowed_hosts must be a list — a string would silently bypass
        the firewall config for limited networking. The update path is
        already tested; pin the create path too so a refactor that
        diverges the two validators fails loudly."""
        resp = client.post(
            "/environments",
            data=json.dumps(
                {
                    "name": "bad",
                    "networking": {"type": "limited", "allowed_hosts": "evil.example.com"},
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_update_env_vars_change_increments_version(
        self, client: Client, auth_headers, environment
    ):
        """env_vars updates write through to the model and bump version —
        previously only the packages-update path was tested."""
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({"version": environment.version, "env_vars": {"NEW_KEY": "new-value"}}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == environment.version + 1
        environment.refresh_from_db()
        assert environment.env_vars == {"NEW_KEY": "new-value"}

    def test_update_setup_script_change_increments_version(
        self, client: Client, auth_headers, environment
    ):
        resp = client.put(
            f"/environments/{environment.id}",
            data=json.dumps({"version": environment.version, "setup_script": "echo new"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == environment.version + 1

    def test_archive_environment_not_found(self, client: Client, auth_headers):
        resp = client.post(f"/environments/{uuid.uuid4()}/archive", **auth_headers)
        assert resp.status_code == 404
        assert "Environment not found" in resp.json()["detail"]

    def test_delete_environment_not_found(self, client: Client, auth_headers):
        resp = client.delete(f"/environments/{uuid.uuid4()}/delete", **auth_headers)
        assert resp.status_code == 404

    def test_delete_environment_wrong_method_returns_405(
        self, client: Client, auth_headers, environment
    ):
        """The delete endpoint must reject anything but DELETE — POST to it
        was previously coverage-untested and a refactor that allowed POST
        would silently expose an unguarded delete path."""
        resp = client.post(f"/environments/{environment.id}/delete", **auth_headers)
        assert resp.status_code == 405

    def test_versions_environment_not_found(self, client: Client, auth_headers):
        resp = client.get(f"/environments/{uuid.uuid4()}/versions", **auth_headers)
        assert resp.status_code == 404


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
        cmds = sprite.shell_strings()
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
            model="anthropic/claude-sonnet-4-6",
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
        cmds = fake_sprites.last_sprite().shell_strings()
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
            model="anthropic/claude-sonnet-4-6",
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
        cmds = fake_sprites.last_sprite().shell_strings()
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
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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
            model="anthropic/claude-sonnet-4-6",
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
