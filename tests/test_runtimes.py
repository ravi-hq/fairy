"""Generic registry-level tests for the runtime package. Per-runtime
build_command / write_config / skills behavior is exercised in
`tests/runtimes/test_{claude,codex,gemini,opencode}.py`."""

from __future__ import annotations

from agent_on_demand.runtimes import RUNTIMES, Runtime


def test_all_runtimes_defined():
    assert set(RUNTIMES) == {"claude", "codex", "gemini", "opencode"}


def test_every_runtime_has_non_empty_providers():
    for name, runtime in RUNTIMES.items():
        assert runtime.name == name
        assert runtime.providers, f"{name} has empty providers"


def test_every_runtime_implements_the_protocol():
    """Runtime is a Protocol — instances don't inherit from it, but must
    still expose `name`, `providers`, `skills_root`, `install`, `build_command`,
    and `write_config`."""
    required_attrs = ("name", "providers", "skills_root")
    required_methods = ("install", "build_command", "write_config")
    for runtime in RUNTIMES.values():
        for attr in required_attrs:
            assert hasattr(runtime, attr), f"{runtime.name} missing {attr}"
        for method in required_methods:
            assert callable(getattr(runtime, method, None)), (
                f"{runtime.name}.{method} must be callable"
            )


def test_runtime_module_exports():
    from agent_on_demand import runtimes

    assert runtimes.Runtime is Runtime
    assert runtimes.RUNTIMES is RUNTIMES
