"""Pin the source-path → e2e-test mapping in scripts/scope_e2e.py.

These tests treat the mapping as a contract: removing a rule, changing
a target, or breaking the runtime selection should fail at least one
test here. Mutmut-style protection for a script that's not under the
mutation gate (the script is too small to mutate-test usefully — these
explicit tests pin it instead).
"""

import pytest

from scripts.scope_e2e import (
    ALL_E2E,
    DEFAULT_RUNTIME,
    RUNTIME_PATHS,
    compute_scope,
)


# === Runtime-specific files trigger their own runtime ===


@pytest.mark.parametrize("runtime,path", RUNTIME_PATHS.items())
def test_runtime_file_scopes_to_that_runtime(runtime, path):
    scope = compute_scope([path])
    assert scope["runtimes"] == [runtime]
    # Each runtime change should pull in the session-spawning tests.
    assert "tests/e2e/test_sessions.py" in scope["tests"]
    assert "tests/e2e/test_skills.py" in scope["tests"]
    assert "tests/e2e/test_mcp.py" in scope["tests"]


def test_two_runtimes_changed_unions_runtimes():
    scope = compute_scope(
        [
            "src/agent_on_demand/runtimes/codex.py",
            "src/agent_on_demand/runtimes/gemini.py",
        ]
    )
    assert scope["runtimes"] == ["codex", "gemini"]


# === Cross-cutting files trigger ALL e2e tests ===


@pytest.mark.parametrize(
    "path",
    [
        "src/agent_on_demand/auth.py",
        "src/agent_on_demand/crypto.py",
        "src/agent_on_demand/runtimes/__init__.py",
        "src/agent_on_demand/runtimes/base.py",
        "src/config/settings.py",
        "src/config/urls.py",
        "src/agent_on_demand/observability.py",
        "src/agent_on_demand/signals.py",
        "src/agent_on_demand/tasks.py",
        "src/agent_on_demand/urls.py",
        "src/agent_on_demand/models_catalog.py",
        "src/agent_on_demand/models/auth.py",
    ],
)
def test_cross_cutting_files_pull_in_all_e2e(path):
    scope = compute_scope([path])
    expected = {f"tests/e2e/{f}" for f in ALL_E2E}
    assert set(scope["tests"]) == expected
    # Default runtime applied since no runtime file changed.
    assert scope["runtimes"] == [DEFAULT_RUNTIME]


# === Resource-scoped files map to their resource's e2e file ===


@pytest.mark.parametrize(
    "path,expected_test",
    [
        ("src/agent_on_demand/views/agents.py", "test_agents.py"),
        ("src/agent_on_demand/models/agents.py", "test_agents.py"),
        ("src/agent_on_demand/views/environments.py", "test_environments.py"),
        ("src/agent_on_demand/models/environments.py", "test_environments.py"),
        ("src/agent_on_demand/views/sessions.py", "test_sessions.py"),
        ("src/agent_on_demand/models/sessions.py", "test_sessions.py"),
        ("src/agent_on_demand/stream.py", "test_sessions.py"),
        ("src/agent_on_demand/session_state.py", "test_sessions.py"),
    ],
)
def test_resource_scoped_change_runs_only_that_resource(path, expected_test):
    scope = compute_scope([path])
    assert scope["tests"] == [f"tests/e2e/{expected_test}"]


def test_versioning_pulls_in_both_versioned_resources():
    scope = compute_scope(["src/agent_on_demand/versioning.py"])
    assert scope["tests"] == [
        "tests/e2e/test_agents.py",
        "tests/e2e/test_environments.py",
    ]


def test_session_service_pulls_in_session_spawning_tests():
    scope = compute_scope(["src/agent_on_demand/session_service/tasks.py"])
    assert set(scope["tests"]) == {
        "tests/e2e/test_sessions.py",
        "tests/e2e/test_skills.py",
        "tests/e2e/test_mcp.py",
    }


# === Test files themselves: PASS_THROUGH ===


def test_e2e_test_file_change_runs_only_that_file():
    scope = compute_scope(["tests/e2e/test_skills.py"])
    assert scope["tests"] == ["tests/e2e/test_skills.py"]


def test_e2e_conftest_change_does_not_pass_through():
    """conftest.py is not a test file; it shouldn't match PASS_THROUGH."""
    scope = compute_scope(["tests/e2e/conftest.py"])
    assert scope["tests"] == []


# === Unrelated files: empty scope ===


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/openapi.yaml",
        "tests/test_auth.py",  # unit test, not e2e
        "scripts/check_mutmut.py",
        ".github/workflows/ci.yml",
    ],
)
def test_unrelated_files_yield_empty_scope(path):
    scope = compute_scope([path])
    assert scope == {"tests": [], "runtimes": []}


def test_empty_change_set_yields_empty_scope():
    scope = compute_scope([])
    assert scope == {"tests": [], "runtimes": []}


# === Combinations ===


def test_mixed_change_unions_test_files():
    """A PR touching a view AND a runtime gets both the resource e2e and
    the runtime-specific session tests."""
    scope = compute_scope(
        [
            "src/agent_on_demand/views/agents.py",
            "src/agent_on_demand/runtimes/claude.py",
        ]
    )
    assert set(scope["tests"]) == {
        "tests/e2e/test_agents.py",
        "tests/e2e/test_sessions.py",
        "tests/e2e/test_skills.py",
        "tests/e2e/test_mcp.py",
    }
    assert scope["runtimes"] == ["claude"]


def test_default_runtime_when_no_runtime_file_changed():
    """A change that triggers e2e but doesn't touch a runtime file falls
    back to the default runtime (claude — the cheapest)."""
    scope = compute_scope(["src/agent_on_demand/views/sessions.py"])
    assert scope["runtimes"] == [DEFAULT_RUNTIME]
