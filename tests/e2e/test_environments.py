"""E2E tests for environments: CRUD and session integration."""

import pytest

from tests.e2e.conftest import RUNTIME_MODELS, _unique


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def runtime(e2e_runtimes):
    """Use the first configured runtime for environment tests. Class-scoped so
    class-level session fixtures can depend on it."""
    if not e2e_runtimes:
        pytest.skip("No runtimes configured in E2E_RUNTIMES")
    return e2e_runtimes[0]


# ---------------------------------------------------------------------------
# Environment CRUD
# ---------------------------------------------------------------------------


class TestEnvironmentCRUD:
    def test_create_full_environment(self, api, create_environment):
        env = create_environment(
            name=_unique("e2e-env"),
            packages={"pip": ["cowsay"], "apt": ["curl"]},
            env_vars={"FAIRY_TEST_VAR": "hello_e2e"},
            setup_script="echo 'setup_done' > /tmp/fairy_setup_marker",
            networking={"type": "unrestricted"},
        )
        assert env["name"].startswith("e2e-env-")
        assert env["packages"] == {"pip": ["cowsay"], "apt": ["curl"]}
        assert env["setup_script"] == "echo 'setup_done' > /tmp/fairy_setup_marker"
        assert env["version"] == 1
        assert env["type"] == "environment"
        # env_vars must NOT be exposed in response
        assert "env_vars" not in env

    def test_create_minimal_environment(self, api, create_environment):
        env = create_environment(name=_unique("e2e-minimal"))
        assert env["packages"] == {}
        assert env["setup_script"] is None
        assert env["networking"] == {"type": "unrestricted"}

    def test_get_environment(self, api, create_environment):
        env = create_environment(name=_unique("e2e-get"))
        resp = api.get_environment(env["id"])
        assert resp.status_code == 200
        assert resp.json()["id"] == env["id"]

    def test_list_environments(self, api, create_environment):
        env = create_environment(name=_unique("e2e-list"))
        resp = api.list_environments()
        assert resp.status_code == 200
        ids = [e["id"] for e in resp.json()["data"]]
        assert env["id"] in ids

    def test_update_environment(self, api, create_environment):
        env = create_environment(
            name=_unique("e2e-update"),
            packages={"pip": ["requests"]},
        )
        resp = api.update_environment(
            env["id"],
            version=1,
            packages={"pip": ["httpx"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["packages"] == {"pip": ["httpx"]}
        assert data["version"] == 2

    def test_update_version_mismatch(self, api, create_environment):
        env = create_environment(name=_unique("e2e-vmismatch"))
        resp = api.update_environment(env["id"], version=99, name="new")
        assert resp.status_code == 409

    def test_archive_environment(self, api, create_environment):
        env = create_environment(name=_unique("e2e-archive"))
        resp = api.archive_environment(env["id"])
        assert resp.status_code == 200
        assert resp.json()["archived_at"] is not None

    def test_archive_idempotent_409(self, api, create_environment):
        env = create_environment(name=_unique("e2e-arch2"))
        api.archive_environment(env["id"])
        resp = api.archive_environment(env["id"])
        assert resp.status_code == 409

    def test_list_excludes_archived(self, api, create_environment):
        env = create_environment(name=_unique("e2e-hidden"))
        api.archive_environment(env["id"])
        resp = api.list_environments()
        ids = [e["id"] for e in resp.json()["data"]]
        assert env["id"] not in ids

    def test_environment_versions(self, api, create_environment):
        env = create_environment(name=_unique("e2e-ver"))
        api.update_environment(env["id"], version=1, name=_unique("e2e-ver-v2"))
        resp = api.list_environment_versions(env["id"])
        assert resp.status_code == 200
        versions = resp.json()["data"]
        assert len(versions) == 2
        assert versions[0]["version"] == 2
        assert versions[1]["version"] == 1

    def test_invalid_package_manager_rejected(self, api):
        resp = api.create_environment(
            name=_unique("e2e-bad"),
            packages={"homebrew": ["wget"]},
        )
        assert resp.status_code == 422

    def test_limited_networking(self, api, create_environment):
        env = create_environment(
            name=_unique("e2e-limited"),
            networking={
                "type": "limited",
                "allowed_hosts": ["api.example.com"],
            },
        )
        assert env["networking"]["type"] == "limited"
        assert env["networking"]["allowed_hosts"] == ["api.example.com"]


# ---------------------------------------------------------------------------
# Environment + Session integration (requires real runtime)
# ---------------------------------------------------------------------------


class TestEnvironmentInSession:
    """Verify Environment config (packages, env_vars, setup_script) lands on
    the Sprite the way Fairy expects.

    Provisions ONE kitchen-sink environment (all three knobs set at once) +
    agent + session per parametrization, then probes the same Sprite via
    sprites-py for each aspect. One real session per e2e run instead of four —
    the previous structure was per-aspect.

    Verification stays decoupled from the agent: a failure points to env
    plumbing, not model behavior.
    """

    @pytest.fixture(scope="class")
    def provisioned(self, api, runtime, sprites_client):
        # All three knobs in one environment. Function-scoped create_*
        # factories can't be reused at class scope, so manage cleanup manually.
        env_resp = api.create_environment(
            name=_unique("e2e-envcheck"),
            packages={"pip": ["cowsay"]},
            env_vars={"FAIRY_E2E_SECRET": "magic_value_42"},
            setup_script=(
                "echo 'FAIRY_SETUP_COMPLETE' > /tmp/fairy_setup_marker\n"
                "env > /tmp/fairy-env-snapshot.txt"
            ),
        )
        env_resp.raise_for_status()
        env = env_resp.json()

        agent_resp = api.create_agent(
            name=_unique("e2e-envcheck-agent"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env["id"],
        )
        agent_resp.raise_for_status()
        agent = agent_resp.json()

        session_resp = api.create_session(
            agent_id=agent["id"], prompt="Say ok.", timeout=300,
        )
        session_resp.raise_for_status()
        session = session_resp.json()

        result = api.wait_for_session(session["id"], timeout=300)
        assert result["status"] == "completed", (
            f"Setup session failed: status={result['status']}, "
            f"exit={result.get('exit_code')}"
        )
        sprite = sprites_client.get_sprite(result["sprite_name"])

        yield sprite

        for cleanup in (
            lambda: api.terminate_session(session["id"]),
            lambda: api.delete_session(session["id"]),
            lambda: api.archive_agent(agent["id"]),
            lambda: api.archive_environment(env["id"]),
        ):
            try:
                cleanup()
            except Exception:
                pass

    @pytest.mark.slow
    def test_packages_installed(self, provisioned):
        out = provisioned.command(
            "python3", "-c", "import cowsay; print('COWSAY_IMPORT_OK')"
        ).output()
        assert b"COWSAY_IMPORT_OK" in out

    @pytest.mark.slow
    def test_env_vars_available(self, provisioned):
        # env exports live in the wrapper-script process and don't survive
        # across separate sprite.command() calls — the setup_script captures
        # them to a file so we can verify them out-of-band.
        snapshot = provisioned.command("cat", "/tmp/fairy-env-snapshot.txt").output()
        assert b"FAIRY_E2E_SECRET=magic_value_42" in snapshot

    @pytest.mark.slow
    def test_setup_script_runs(self, provisioned):
        marker = provisioned.command("cat", "/tmp/fairy_setup_marker").output()
        assert b"FAIRY_SETUP_COMPLETE" in marker

    def test_session_inherits_agent_environment(
        self, api, create_agent, create_session, create_environment, runtime
    ):
        """Session uses the agent's default environment when none is specified."""
        env = create_environment(name=_unique("e2e-inherit"))
        agent = create_agent(
            name=_unique("e2e-inherit-agent"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env["id"],
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say ok.",
            timeout=120,
        )
        resp = api.get_session(session["id"])
        assert resp.json()["environment_id"] == env["id"]

    def test_explicit_environment_overrides_agent(
        self, api, create_agent, create_session, create_environment, runtime
    ):
        """Explicit environment_id on session overrides the agent's default."""
        env1 = create_environment(name=_unique("e2e-default"))
        env2 = create_environment(name=_unique("e2e-override"))
        agent = create_agent(
            name=_unique("e2e-override-agent"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env1["id"],
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say ok.",
            environment_id=env2["id"],
            timeout=120,
        )
        resp = api.get_session(session["id"])
        assert resp.json()["environment_id"] == env2["id"]
