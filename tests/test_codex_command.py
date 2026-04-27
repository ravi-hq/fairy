"""Direct unit tests for `build_codex_command`.

Mutation-tested. Each test pins one mutation-killable property of the
Codex per-turn argv:

  - ``mode="run"`` produces the exact 4-element initial argv.
  - ``mode="continue"`` produces the exact 6-element resume argv with
    ``resume --last`` inserted in that order — this is what makes
    Codex reattach to its own persisted conversation state.
  - Both argvs share the ``["codex", "exec"]`` prefix and trailing
    ``--json`` flag; Codex's JSON streaming is positional on ``--json``
    being last.
  - ``run`` mode contains neither ``resume`` nor ``--last``; ``continue``
    contains both, in that order, and the two argvs differ only by
    those two inserted elements.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them. ``SessionSpec`` is duck-typed via
``SimpleNamespace``.
"""

from types import SimpleNamespace

from agent_on_demand.runtimes.codex_command import build_codex_command


def _spec():
    return SimpleNamespace()


# ---------- exact argv for each mode ----------


def test_run_mode_produces_exact_argv():
    """``mode="run"`` returns the literal 4-element argv. Pin so a
    mutant that drops ``--json``, swaps the bypass flag, or changes
    the subcommand is caught."""
    assert build_codex_command(_spec(), "run") == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
    ]


def test_continue_mode_produces_exact_argv():
    """``mode="continue"`` returns the literal 6-element argv with
    ``resume --last`` in that order. Pin so a mutant that swaps them,
    drops one, or appends them at the end has nothing to hide
    behind."""
    assert build_codex_command(_spec(), "continue") == [
        "codex",
        "exec",
        "resume",
        "--last",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
    ]


# ---------- shared structure ----------


def test_both_modes_start_with_codex_exec():
    """Both argvs share the ``["codex", "exec"]`` prefix. Pin
    separately so a mutant that drops ``exec`` from one branch is
    caught even if it spelled the rest correctly."""
    assert build_codex_command(_spec(), "run")[:2] == ["codex", "exec"]
    assert build_codex_command(_spec(), "continue")[:2] == ["codex", "exec"]


def test_both_modes_contain_dangerous_bypass_flag():
    """Both argvs carry ``--dangerously-bypass-approvals-and-sandbox``.
    Pin so a mutant that drops the bypass flag (which would make Codex
    interactively prompt and hang in the worker) is caught."""
    assert "--dangerously-bypass-approvals-and-sandbox" in build_codex_command(_spec(), "run")
    assert "--dangerously-bypass-approvals-and-sandbox" in build_codex_command(
        _spec(), "continue"
    )


def test_both_modes_contain_json_flag():
    """Both argvs carry ``--json``. Pin separately from the bypass flag
    so a mutant that drops one but not the other is caught."""
    assert "--json" in build_codex_command(_spec(), "run")
    assert "--json" in build_codex_command(_spec(), "continue")


def test_json_flag_is_trailing_in_both_modes():
    """``--json`` is the last argv element in both modes. Codex's JSON
    streaming output is positionally tied to this being terminal — a
    mutant that re-orders the trailing flags would silently break
    log parsing."""
    assert build_codex_command(_spec(), "run")[-1] == "--json"
    assert build_codex_command(_spec(), "continue")[-1] == "--json"


# ---------- branch-distinguishing properties ----------


def test_run_mode_omits_resume_and_last():
    """``mode="run"`` contains neither ``resume`` nor ``--last``. A
    mutant that always emits the resume form (e.g. swaps the ``if``
    branch) would still type-check but would make first turns try to
    resume a conversation that doesn't exist yet."""
    argv = build_codex_command(_spec(), "run")
    assert "resume" not in argv
    assert "--last" not in argv


def test_continue_mode_contains_resume_and_last():
    """``mode="continue"`` contains both ``resume`` and ``--last``.
    Pin both to defeat a mutant that drops one of them — Codex needs
    the pair to actually reattach to the prior conversation."""
    argv = build_codex_command(_spec(), "continue")
    assert "resume" in argv
    assert "--last" in argv


def test_resume_precedes_last_in_continue_mode():
    """``resume`` precedes ``--last``. Codex's CLI parses these
    positionally: ``resume`` is the subcommand and ``--last`` is its
    flag. A mutant that swaps them silently changes the meaning to an
    invalid CLI invocation that may exit non-zero with no other signal
    in the log."""
    argv = build_codex_command(_spec(), "continue")
    assert argv.index("resume") < argv.index("--last")


def test_continue_argv_is_run_argv_with_resume_last_inserted():
    """The two argvs differ only by the inserted ``resume --last``
    pair. Pin the length delta so any mutant that adds, drops, or
    duplicates an element in either branch is caught."""
    run_argv = build_codex_command(_spec(), "run")
    continue_argv = build_codex_command(_spec(), "continue")
    assert len(continue_argv) == len(run_argv) + 2
    assert continue_argv == [run_argv[0], run_argv[1], "resume", "--last", *run_argv[2:]]


# ---------- mode is mutually exclusive ----------


def test_mode_branches_are_distinct():
    """``run`` and ``continue`` produce different argvs. Pin so a
    mutant that collapses the branch (e.g. ``if False:``) is caught
    even if both literal lists happened to be syntactically valid."""
    assert build_codex_command(_spec(), "run") != build_codex_command(_spec(), "continue")


def test_no_third_branch_creeps_in_for_run():
    """Only ``mode="continue"`` triggers the resume form; any value
    other than ``"continue"`` falls through to the ``run`` argv. Pin
    the falsey-mode behavior (we use ``"run"`` as the canonical run
    value) so a mutant that adds a third branch (e.g.
    ``elif mode == "..."``) and silently changes the fall-through is
    caught."""
    assert build_codex_command(_spec(), "run") == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
    ]
