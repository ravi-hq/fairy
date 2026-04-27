"""Direct unit tests for `build_opencode_command`.

Mutation-tested. Each test pins one mutation-killable property of the
opencode CLI argv:

  - The argv always begins with ``["opencode", "run"]`` — these two
    tokens identify the binary + subcommand and any reorder breaks
    invocation.
  - ``--model`` is immediately followed by ``spec.model``, and
    ``--format`` is immediately followed by ``json``. A mutant that
    swaps the value or drops it would produce nonsense argv.
  - ``spec.model`` is inlined verbatim — opencode is a meta-runtime
    and accepts ``provider/model_id``; any transformation would break
    real model strings.
  - ``mode == "continue"`` appends ``--continue`` as the trailing
    element; ``mode == "run"`` does NOT append it.
  - The ``run`` and ``continue`` argvs differ only by the trailing
    ``--continue`` (length differs by exactly 1).
  - Mode is mutually exclusive — there is no third branch.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them. ``SessionSpec`` is duck-typed via
``SimpleNamespace``.
"""

from types import SimpleNamespace

from agent_on_demand.runtimes.opencode_command import build_opencode_command


def _spec(model: str = "anthropic/claude-sonnet-4-6"):
    return SimpleNamespace(model=model)


# ---------- exact argv shape ----------


def test_run_mode_exact_argv():
    """Full-argv assertion for ``mode="run"`` pins every token and its
    position. A mutant that drops, reorders, or rewrites any element is
    caught here."""
    argv = build_opencode_command(_spec(model="anthropic/claude-sonnet-4-6"), "run")
    assert argv == [
        "opencode",
        "run",
        "--model",
        "anthropic/claude-sonnet-4-6",
        "--format",
        "json",
    ]


def test_continue_mode_exact_argv():
    """Full-argv assertion for ``mode="continue"`` — same prefix as
    run, with ``--continue`` appended at the end."""
    argv = build_opencode_command(_spec(model="openai/gpt-5"), "continue")
    assert argv == [
        "opencode",
        "run",
        "--model",
        "openai/gpt-5",
        "--format",
        "json",
        "--continue",
    ]


# ---------- positional invariants ----------


def test_argv_starts_with_opencode_run():
    """The first two tokens are always ``["opencode", "run"]``. Pin
    separately so a mutant that swaps these two would be caught even
    if a future change makes the rest of the argv variable."""
    for mode in ("run", "continue"):
        argv = build_opencode_command(_spec(), mode)
        assert argv[0] == "opencode"
        assert argv[1] == "run"


def test_model_flag_immediately_precedes_model_value():
    """``--model`` and the model string are adjacent in that order. A
    mutant that swaps the order or drops the value (leaving the flag
    naked) would be caught."""
    argv = build_opencode_command(_spec(model="anthropic/claude-sonnet-4-6"), "run")
    idx = argv.index("--model")
    assert argv[idx + 1] == "anthropic/claude-sonnet-4-6"


def test_format_flag_immediately_precedes_json():
    """``--format`` is always immediately followed by ``"json"``. A
    mutant that swaps to ``"text"``/``"yaml"`` or drops the value would
    be caught."""
    argv = build_opencode_command(_spec(), "run")
    idx = argv.index("--format")
    assert argv[idx + 1] == "json"


def test_model_string_inlined_verbatim_with_slash():
    """``spec.model`` lands in the argv unchanged — no provider strip,
    no normalization. The ``provider/model_id`` form must round-trip
    through the argv exactly so opencode resolves the right backend."""
    argv = build_opencode_command(_spec(model="google/gemini-2.5-pro"), "run")
    assert "google/gemini-2.5-pro" in argv


# ---------- mode branching ----------


def test_continue_appends_continue_flag_at_tail():
    """``mode="continue"`` puts ``--continue`` at index -1 (the trailing
    element). A mutant that inserts the flag earlier in the argv would
    still produce a syntactically valid argv but is wrong — opencode's
    flag-order parser tolerates it, so the behavior is silent."""
    argv = build_opencode_command(_spec(), "continue")
    assert argv[-1] == "--continue"


def test_run_does_not_contain_continue_flag():
    """``mode="run"`` must NOT include ``--continue``; otherwise every
    fresh session resumes a phantom prior conversation."""
    argv = build_opencode_command(_spec(), "run")
    assert "--continue" not in argv


def test_continue_argv_is_run_argv_plus_trailing_continue():
    """The ``continue`` argv equals the ``run`` argv with one extra
    element (``--continue``) appended. Pinning the differential shape
    catches mutants that change a non-trailing element when
    ``mode == "continue"``."""
    spec = _spec(model="anthropic/claude-sonnet-4-6")
    run_argv = build_opencode_command(spec, "run")
    continue_argv = build_opencode_command(spec, "continue")
    assert len(continue_argv) == len(run_argv) + 1
    assert continue_argv[:-1] == run_argv
    assert continue_argv[-1] == "--continue"
