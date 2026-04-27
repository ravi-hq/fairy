"""Direct unit tests for `build_turn_argv`.

Mutation-tested. Each test pins one mutation-killable property of the
per-turn argv construction:

  - The first four elements are the bash-shim prologue, in exact order.
  - The shim string sources ``/tmp/aod-env`` with the precise
    ``set -a; source ...; set +a; exec "$@"`` sequence — a quoting or
    word-order bug here silently breaks env-var sourcing for every
    turn against every runtime.
  - The runtime's ``build_command(spec, mode)`` return is appended
    verbatim after the ``--`` separator, in order.
  - ``mode`` is forwarded to ``runtime.build_command`` literally
    (``"run"`` and ``"continue"`` both reach the runtime).
  - An empty ``build_command`` return → argv is exactly the 4-element
    shim with no trailing items.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them. ``Runtime`` and ``SessionSpec`` are
duck-typed via ``SimpleNamespace``.
"""

from types import SimpleNamespace

from agent_on_demand.session_service.turn_argv import (
    _ENV_SOURCE_SHIM,
    build_turn_argv,
)


def _runtime(argv=None, recorder=None):
    """Stub Runtime whose ``build_command`` returns ``argv`` and records
    its call args into ``recorder`` (if provided)."""

    def build_command(spec, mode):
        if recorder is not None:
            recorder.append((spec, mode))
        return list(argv) if argv is not None else []

    return SimpleNamespace(build_command=build_command)


def _spec():
    return SimpleNamespace()


# ---------- shim prologue ----------


def test_argv_begins_with_bash_lc_shim_and_separator():
    """The first four elements are exactly ``["bash", "-lc",
    _ENV_SOURCE_SHIM, "--"]`` in that order. Pin so a refactor that
    reorders them, drops the ``--``, or substitutes a different shell
    would have to be a deliberate change."""
    argv = build_turn_argv(_runtime(argv=["claude", "--print"]), _spec(), "run")
    assert argv[0] == "bash"
    assert argv[1] == "-lc"
    assert argv[2] == _ENV_SOURCE_SHIM
    assert argv[3] == "--"


def test_shim_string_is_exact():
    """Full-string assertion to kill wrapper mutants like ``XXset
    -aXX...``. The exact bytes matter — ``set -a`` exports every
    sourced var, and ``exec "$@"`` replaces the bash process with the
    runtime CLI so signals propagate."""
    assert _ENV_SOURCE_SHIM == 'set -a; source /tmp/aod-env; set +a; exec "$@"'


def test_shim_constant_is_used_in_argv():
    """The third element of the argv must BE the module-level shim
    constant, not a copy that has drifted. Identity-of-string check
    pins the indirection."""
    argv = build_turn_argv(_runtime(argv=["x"]), _spec(), "run")
    assert argv[2] == _ENV_SOURCE_SHIM


# ---------- runtime argv pass-through ----------


def test_runtime_argv_appended_verbatim_after_separator():
    """The runtime's argv lands after ``--`` in the order it returned
    them. Pin so a mutant that reverses the splat or wraps each item
    has nothing to hide behind."""
    argv = build_turn_argv(
        _runtime(argv=["claude", "--print", "--model", "sonnet"]),
        _spec(),
        "run",
    )
    assert argv[4:] == ["claude", "--print", "--model", "sonnet"]


def test_full_argv_shape_with_runtime_argv():
    """End-to-end shape: the prologue + runtime argv concatenate to
    exactly what gets handed to ``sprite.command(...)``."""
    argv = build_turn_argv(_runtime(argv=["codex", "exec"]), _spec(), "run")
    assert argv == [
        "bash",
        "-lc",
        _ENV_SOURCE_SHIM,
        "--",
        "codex",
        "exec",
    ]


def test_empty_runtime_argv_yields_only_shim():
    """A runtime that returns ``[]`` from ``build_command`` produces
    exactly the 4-element shim with no trailing items. Distinguishes a
    mutant that injects a stray empty string or duplicates the
    separator."""
    argv = build_turn_argv(_runtime(argv=[]), _spec(), "run")
    assert argv == ["bash", "-lc", _ENV_SOURCE_SHIM, "--"]
    assert len(argv) == 4


# ---------- mode forwarding ----------


def test_mode_run_is_forwarded_literally():
    """``mode="run"`` reaches ``runtime.build_command`` as the literal
    string ``"run"`` — no rewrite, no fall-through to a default."""
    calls: list = []
    argv = build_turn_argv(_runtime(argv=["x"], recorder=calls), _spec(), "run")
    assert argv == ["bash", "-lc", _ENV_SOURCE_SHIM, "--", "x"]
    assert len(calls) == 1
    assert calls[0][1] == "run"


def test_mode_continue_is_forwarded_literally():
    """``mode="continue"`` reaches ``runtime.build_command`` as the
    literal string ``"continue"`` — pin both legal mode values so a
    mutant that hard-codes one is caught."""
    calls: list = []
    argv = build_turn_argv(_runtime(argv=["x"], recorder=calls), _spec(), "continue")
    assert argv == ["bash", "-lc", _ENV_SOURCE_SHIM, "--", "x"]
    assert len(calls) == 1
    assert calls[0][1] == "continue"


def test_spec_is_forwarded_to_build_command():
    """The exact spec object passed in is what ``build_command``
    receives — no copy, no rewrap. Pin so a mutant that swaps spec for
    None or a sentinel is caught."""
    calls: list = []
    spec = _spec()
    build_turn_argv(_runtime(argv=[], recorder=calls), spec, "run")
    assert calls[0][0] is spec
