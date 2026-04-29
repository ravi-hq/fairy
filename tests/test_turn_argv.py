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
  - Any ``mode`` other than the literal lowercase ``"run"`` or
    ``"continue"`` raises ``ValueError`` with an exact message — pins
    the validation tuple so a mutant that uppercases or string-wraps
    the literals dies.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them. ``Runtime`` and ``SessionSpec`` are
duck-typed via ``SimpleNamespace``.
"""

from types import SimpleNamespace

import pytest

from agent_on_demand.session_service.turn.argv import (
    _ENV_SOURCE_SHIM,
    build_env_source_shim,
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


# ---------- mode validation ----------
#
# These tests replace what used to be a `typing.cast(Literal[...], mode)`
# — a runtime no-op that mutmut couldn't kill (5 known-equivalent
# survivors). Runtime validation gives mutmut a real surface to mutate
# (the tuple `("run", "continue")`) and gives callers a clear failure
# at the boundary instead of an opaque downstream error.
#
# Exact-equality assertions on `str(exc.value)` (not `match=`) — mutmut's
# string-wrap mutation `"XXmust be 'run'...XX"` would survive a substring
# match. Same lesson as PR #232's CI fix.


def test_invalid_mode_raises_value_error():
    """A mode that isn't ``"run"`` or ``"continue"`` raises
    ``ValueError`` before ``runtime.build_command`` is ever called."""
    calls: list = []
    with pytest.raises(ValueError) as exc_info:
        build_turn_argv(_runtime(argv=["x"], recorder=calls), _spec(), "garbage")
    assert str(exc_info.value) == "mode must be 'run' or 'continue', got 'garbage'"
    assert calls == []


def test_empty_string_mode_raises_value_error():
    """An empty mode string raises ``ValueError`` — pins that the
    validation isn't a truthiness check (``if not mode``) but a
    membership check against the literal tuple."""
    with pytest.raises(ValueError) as exc_info:
        build_turn_argv(_runtime(argv=["x"]), _spec(), "")
    assert str(exc_info.value) == "mode must be 'run' or 'continue', got ''"


def test_uppercase_run_mode_raises_value_error():
    """``mode="RUN"`` raises — pins the literal lowercase ``"run"``
    in the validation tuple. Kills a mutant that uppercases the first
    tuple entry (``("RUN", "continue")``) since ``"RUN"`` would then
    pass through silently."""
    with pytest.raises(ValueError) as exc_info:
        build_turn_argv(_runtime(argv=["x"]), _spec(), "RUN")
    assert str(exc_info.value) == "mode must be 'run' or 'continue', got 'RUN'"


def test_uppercase_continue_mode_raises_value_error():
    """``mode="CONTINUE"`` raises — pins the literal lowercase
    ``"continue"`` in the validation tuple. Kills a mutant that
    uppercases the second tuple entry (``("run", "CONTINUE")``)."""
    with pytest.raises(ValueError) as exc_info:
        build_turn_argv(_runtime(argv=["x"]), _spec(), "CONTINUE")
    assert str(exc_info.value) == "mode must be 'run' or 'continue', got 'CONTINUE'"


# ---------- extra_env (per-turn telemetry exports) ----------
#
# `extra_env` injects per-turn env vars (W3C TRACEPARENT, OTel exporter
# config, etc.) into the bash shim between `source /tmp/aod-env` and
# `exec`. Pinning the exact rendered shim shape keeps the surface clear
# for mutmut: a quoting or ordering bug here breaks every claude turn's
# trace context propagation silently.


def test_extra_env_none_yields_baseline_shim():
    """``extra_env=None`` is the default and renders the baseline
    no-extras shim, identical to passing nothing."""
    argv = build_turn_argv(_runtime(argv=["claude"]), _spec(), "run", extra_env=None)
    assert argv[2] == _ENV_SOURCE_SHIM


def test_extra_env_empty_yields_baseline_shim():
    """An empty dict is the same as ``None`` — no assignments emitted."""
    argv = build_turn_argv(_runtime(argv=["claude"]), _spec(), "run", extra_env={})
    assert argv[2] == _ENV_SOURCE_SHIM


def test_extra_env_appears_between_source_and_exec():
    """Assignments land *after* ``source /tmp/aod-env`` (so they win
    against any duplicate set there) and *before* ``set +a; exec``
    (so ``set -a`` exports them into the runtime CLI's environment).
    """
    argv = build_turn_argv(
        _runtime(argv=["claude"]),
        _spec(),
        "run",
        extra_env={"TRACEPARENT": "00-abc-01"},
    )
    shim = argv[2]
    src_idx = shim.index("source /tmp/aod-env")
    set_off_idx = shim.index("set +a")
    var_idx = shim.index("TRACEPARENT=")
    assert src_idx < var_idx < set_off_idx


def test_extra_env_keys_emitted_in_sorted_order():
    """Keys are sorted so the rendered shim is deterministic across
    runs. Pin so a future caller that relies on dict-insertion order
    can't introduce flakiness."""
    argv = build_turn_argv(
        _runtime(argv=["claude"]),
        _spec(),
        "run",
        extra_env={"ZULU": "z", "ALPHA": "a", "MIKE": "m"},
    )
    shim = argv[2]
    a_idx = shim.index("ALPHA=")
    m_idx = shim.index("MIKE=")
    z_idx = shim.index("ZULU=")
    assert a_idx < m_idx < z_idx


def test_extra_env_values_are_shell_quoted():
    """Values flow through ``shlex.quote`` so a value containing spaces,
    quotes, or shell metacharacters can't break the shim or inject
    arbitrary commands. Pin so a refactor that drops the quoting is
    caught."""
    argv = build_turn_argv(
        _runtime(argv=["claude"]),
        _spec(),
        "run",
        extra_env={"X": "a b'c;d"},
    )
    # shlex.quote wraps in single quotes and escapes embedded ones.
    assert "X='a b'\"'\"'c;d'" in argv[2]


def test_build_env_source_shim_baseline():
    """Direct test on the shim builder. Empty/None input → exact
    baseline string."""
    assert build_env_source_shim(None) == _ENV_SOURCE_SHIM
    assert build_env_source_shim({}) == _ENV_SOURCE_SHIM


def test_build_env_source_shim_with_extras():
    """Exact rendered shape with two exports — pins the order of the
    parts joined with ``; ``."""
    rendered = build_env_source_shim({"FOO": "bar", "BAZ": "qux"})
    assert rendered == ('set -a; source /tmp/aod-env; BAZ=qux; FOO=bar; set +a; exec "$@"')
