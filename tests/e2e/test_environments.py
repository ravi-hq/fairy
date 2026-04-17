"""E2E tests for environments: CRUD and session integration."""

import pytest

from tests.e2e.conftest import RUNTIME_MODELS, _unique, stream_all_output


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime(e2e_runtimes):
    """Use the first configured runtime for environment tests."""
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
    """Verify that environment setup (packages, env vars, setup script) works
    end-to-end inside a real agent session."""

    @pytest.mark.slow
    def test_packages_installed(
        self, api, create_agent, create_session, create_environment, runtime
    ):
        """Verify pip packages from the environment are available."""
        env = create_environment(
            name=_unique("e2e-pkg"),
            packages={"pip": ["cowsay"]},
        )
        agent = create_agent(
            name=_unique("e2e-pkg-agent"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env["id"],
        )
        session = create_session(
            agent_id=agent["id"],
            prompt=(
                "Run this exact command and print the output: "
                "python3 -c \"import cowsay; print('COWSAY_IMPORT_OK')\""
            ),
            timeout=300,
        )
        result, events = api.run_session(session["id"], timeout=300)
        assert result["status"] == "completed", (
            f"Session failed: status={result['status']}, exit={result.get('exit_code')}"
        )

        output = stream_all_output(events)
        assert "COWSAY_IMPORT_OK" in output, (
            f"Package import marker not found in output: {output[:500]}"
        )

    @pytest.mark.slow
    def test_env_vars_available(
        self, api, create_agent, create_session, create_environment, runtime
    ):
        """Verify environment variables are exported in the session."""
        env = create_environment(
            name=_unique("e2e-envvar"),
            env_vars={"FAIRY_E2E_SECRET": "magic_value_42"},
        )
        agent = create_agent(
            name=_unique("e2e-envvar-agent"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env["id"],
        )
        session = create_session(
            agent_id=agent["id"],
            prompt=(
                "Run this exact command and print the output: "
                "echo \"ENVVAR=$FAIRY_E2E_SECRET\""
            ),
            timeout=180,
        )
        result, events = api.run_session(session["id"])
        assert result["status"] == "completed"

        output = stream_all_output(events)
        assert "magic_value_42" in output, (
            f"Env var value not found in output: {output[:500]}"
        )

    @pytest.mark.slow
    def test_setup_script_runs(
        self, api, create_agent, create_session, create_environment, runtime
    ):
        """Verify the setup_script executes before the agent."""
        env = create_environment(
            name=_unique("e2e-setup"),
            setup_script="echo 'FAIRY_SETUP_COMPLETE' > /tmp/fairy_setup_marker",
        )
        agent = create_agent(
            name=_unique("e2e-setup-agent"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env["id"],
        )
        session = create_session(
            agent_id=agent["id"],
            prompt=(
                "Read the file /tmp/fairy_setup_marker and print its contents."
            ),
            timeout=180,
        )
        result, events = api.run_session(session["id"])
        assert result["status"] == "completed"

        output = stream_all_output(events)
        assert "FAIRY_SETUP_COMPLETE" in output, (
            f"Setup marker not found in output: {output[:500]}"
        )

    @pytest.mark.slow
    def test_full_environment_integration(
        self, api, create_agent, create_session, create_environment, runtime
    ):
        """Test packages + env vars + setup script all together."""
        env = create_environment(
            name=_unique("e2e-full"),
            packages={"pip": ["cowsay"]},
            env_vars={"FAIRY_E2E_COMBO": "combo_value"},
            setup_script="echo 'SETUP_RAN' > /tmp/fairy_combo_marker",
        )
        agent = create_agent(
            name=_unique("e2e-full-agent"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env["id"],
        )
        session = create_session(
            agent_id=agent["id"],
            prompt=(
                "Run these three commands and print all their output:\n"
                "1. python3 -c \"import cowsay; print('PKG_OK')\"\n"
                "2. echo \"ENV=$FAIRY_E2E_COMBO\"\n"
                "3. cat /tmp/fairy_combo_marker"
            ),
            timeout=300,
        )
        result, events = api.run_session(session["id"], timeout=300)
        assert result["status"] == "completed"

        output = stream_all_output(events)
        assert "PKG_OK" in output, f"Package check failed: {output[:500]}"
        assert "combo_value" in output, f"Env var check failed: {output[:500]}"
        assert "SETUP_RAN" in output, f"Setup script check failed: {output[:500]}"

    @pytest.mark.slow
    def test_limited_networking_blocks_disallowed_host(
        self, api, create_agent, create_session, create_environment, runtime
    ):
        """A domain outside allowed_hosts resolves to DNS REFUSED inside the sprite."""
        if runtime not in ("claude", "claude-oauth"):
            pytest.skip("Hardcoded allowed_hosts assume the claude runtime's API host")

        env = create_environment(
            name=_unique("e2e-netblock"),
            networking={
                "type": "limited",
                "allowed_hosts": ["api.anthropic.com"],
            },
            setup_script=(
                "python3 -c \"import socket; socket.gethostbyname('example.com')\" "
                "2>/dev/null && echo EXAMPLE_RESOLVED > /tmp/fairy_net_block "
                "|| echo EXAMPLE_BLOCKED > /tmp/fairy_net_block"
            ),
        )
        agent = create_agent(
            name=_unique("e2e-netblock-agent"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env["id"],
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Read the file /tmp/fairy_net_block and print its contents.",
            timeout=180,
        )
        result, events = api.run_session(session["id"])
        output = stream_all_output(events)
        assert "EXAMPLE_BLOCKED" in output, (
            f"Expected example.com to be blocked by DNS policy. Output: {output[:500]}"
        )
        assert "EXAMPLE_RESOLVED" not in output, (
            f"example.com should not resolve under limited networking. Output: {output[:500]}"
        )
        assert result["status"] == "completed"

    @pytest.mark.slow
    def test_limited_networking_allows_listed_host(
        self, api, create_agent, create_session, create_environment, runtime
    ):
        """A domain inside allowed_hosts resolves normally inside the sprite."""
        if runtime not in ("claude", "claude-oauth"):
            pytest.skip("Hardcoded allowed_hosts assume the claude runtime's API host")

        env = create_environment(
            name=_unique("e2e-netallow"),
            networking={
                "type": "limited",
                "allowed_hosts": ["api.anthropic.com", "api.github.com"],
            },
            setup_script=(
                "python3 -c \"import socket; socket.gethostbyname('api.github.com')\" "
                "2>/dev/null && echo GITHUB_RESOLVED > /tmp/fairy_net_allow "
                "|| echo GITHUB_BLOCKED > /tmp/fairy_net_allow"
            ),
        )
        agent = create_agent(
            name=_unique("e2e-netallow-agent"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env["id"],
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Read the file /tmp/fairy_net_allow and print its contents.",
            timeout=180,
        )
        result, events = api.run_session(session["id"])
        output = stream_all_output(events)
        assert "GITHUB_RESOLVED" in output, (
            f"Expected api.github.com to resolve under allow-list. Output: {output[:500]}"
        )
        assert result["status"] == "completed"

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
