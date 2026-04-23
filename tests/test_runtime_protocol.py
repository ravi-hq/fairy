from typing import Literal, Protocol

from agent_on_demand.runtimes.base import Runtime


def test_runtime_is_protocol():
    assert issubclass(Runtime, Protocol)


def test_runtime_protocol_importable():
    from agent_on_demand.runtimes.base import Runtime as R

    assert R is Runtime


def test_stub_satisfies_runtime_protocol():
    """A minimal concrete class that fulfils the Runtime Protocol is accepted."""

    class StubRuntime:
        name = "stub"
        providers: set[str] = {"anthropic"}
        skills_root: str | None = None

        def install(self, sprite) -> None:
            pass

        def build_command(self, spec, mode: Literal["run", "continue"]) -> list[str]:
            return ["echo"]

        def write_config(self, sprite, spec, mcp_servers) -> None:
            pass

    stub = StubRuntime()
    assert stub.name == "stub"
    assert isinstance(stub.providers, set)
    assert stub.skills_root is None
    assert callable(stub.install)
    assert callable(stub.build_command)
    assert callable(stub.write_config)


def test_runtime_protocol_has_required_annotations():
    required_attrs = {"name", "providers", "skills_root", "skills_sh_agent"}
    annotations = Runtime.__annotations__
    assert required_attrs == set(annotations.keys())


def test_runtime_protocol_has_required_methods():
    required_methods = {"install", "build_command", "write_config"}
    for method in required_methods:
        assert callable(getattr(Runtime, method, None)), f"Runtime.{method} should be callable"
