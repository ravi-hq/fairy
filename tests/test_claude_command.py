"""Direct unit tests for `build_claude_command`.

Mutation-tested. Each test pins one mutation-killable property of the
per-turn argv handed to Claude Code's CLI:

  - The argv prologue is exactly
    ``["claude", "--dangerously-skip-permissions", "--print",
    "--verbose", "--output-format", "stream-json", ...]`` — every flag
    is load-bearing for log streaming and unattended execution.
  - ``mode="continue"`` selects ``--resume``; ``mode="run"`` selects
    ``--session-id``. Exclusively one or the other, never both, never
    neither.
  - The session id flag sits at index ``-2`` and the id literal at
    index ``-1`` — the trailing pair shape ``["--<flag>", "<id>"]`` is
    what Claude's argv parser expects.
  - ``runtime_session_id`` is forwarded verbatim (no transformation).
  - A falsy ``runtime_session_id`` (``None`` or ``""``) in ``mode="run"``
    becomes the empty-string placeholder ``""`` (Claude allocates a
    fresh id on its own).
  - A falsy ``runtime_session_id`` in ``mode="continue"`` raises
    ``ValueError`` — there is no session to resume.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them. ``SessionSpec`` is duck-typed via
``SimpleNamespace``.
"""

from types import SimpleNamespace

import pytest

from agent_on_demand.runtimes.claude_command import build_claude_command


def _spec(runtime_session_id=None):
    return SimpleNamespace(runtime_session_id=runtime_session_id)


# ---------- argv prologue (flags pinned in exact order) ----------


def test_prologue_starts_with_claude_binary_name():
    """First element is ``"claude"`` exactly — pin so a mutant that
    swaps to ``"Claude"`` or any path-prefix variant is caught."""
    argv = build_claude_command(_spec("s"), "run")
    assert argv[0] == "claude"


def test_prologue_pins_dangerously_skip_permissions_at_index_1():
    """``--dangerously-skip-permissions`` is the only way to run Claude
    Code unattended on a Sprite. Drop it and every turn hangs on a
    permissions prompt that no human can answer."""
    argv = build_claude_command(_spec("s"), "run")
    assert argv[1] == "--dangerously-skip-permissions"


def test_prologue_pins_print_verbose_stream_json_in_order():
    """The trio that produces the JSON event stream the worker tails.
    Pin the order so a mutant reordering them is caught."""
    argv = build_claude_command(_spec("s"), "run")
    assert argv[2] == "--print"
    assert argv[3] == "--verbose"
    assert argv[4] == "--output-format"
    assert argv[5] == "stream-json"


def test_prologue_first_six_elements_exact():
    """Combined assertion: the six leading elements never change shape
    regardless of mode or session id. Distinguishes a mutant that
    shuffles or drops any single flag."""
    argv = build_claude_command(_spec("s"), "run")
    assert argv[:6] == [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
    ]


def test_prologue_identical_across_modes():
    """The first six elements do not depend on ``mode`` — pin so a
    mutant that branches earlier than the resume/session-id flag is
    caught."""
    run_argv = build_claude_command(_spec("s"), "run")
    cont_argv = build_claude_command(_spec("s"), "continue")
    assert run_argv[:6] == cont_argv[:6]


# ---------- mode branch: --resume vs --session-id ----------


def test_continue_mode_uses_resume_flag():
    """``mode="continue"`` selects ``--resume`` at index ``-2``."""
    argv = build_claude_command(_spec("sess-1"), "continue")
    assert argv[-2] == "--resume"


def test_run_mode_uses_session_id_flag():
    """``mode="run"`` selects ``--session-id`` at index ``-2``."""
    argv = build_claude_command(_spec("sess-1"), "run")
    assert argv[-2] == "--session-id"


def test_continue_mode_does_not_include_session_id_flag():
    """The mode branch is exclusive — ``--session-id`` must NOT appear
    when ``mode="continue"``. Pin so a mutant that emits both flags is
    caught."""
    argv = build_claude_command(_spec("sess-1"), "continue")
    assert "--session-id" not in argv


def test_run_mode_does_not_include_resume_flag():
    """``--resume`` must NOT appear when ``mode="run"``. Pin so a mutant
    that emits both flags is caught."""
    argv = build_claude_command(_spec("sess-1"), "run")
    assert "--resume" not in argv


def test_each_mode_emits_exactly_one_of_the_two_flags():
    """Pin the exclusive-or: every legal mode picks exactly one of
    ``--resume`` or ``--session-id``, never neither (which would fail
    Claude's parser)."""
    for mode in ("run", "continue"):
        argv = build_claude_command(_spec("s"), mode)
        has_resume = "--resume" in argv
        has_sid = "--session-id" in argv
        assert has_resume ^ has_sid


# ---------- session id placement and literal forwarding ----------


def test_session_id_is_final_element():
    """The session id literal sits at index ``-1``. Claude's argv parser
    consumes it as the value of the preceding flag — a mutant that
    swaps the order strands the id."""
    argv = build_claude_command(_spec("abc-123"), "run")
    assert argv[-1] == "abc-123"


def test_session_id_forwarded_verbatim_no_transformation():
    """The id string is passed through untouched — no lowercasing, no
    stripping, no quoting. Pin so a mutant that calls ``.lower()`` or
    similar is caught."""
    argv = build_claude_command(_spec("MiXeD-Case_42"), "run")
    assert argv[-1] == "MiXeD-Case_42"


def test_session_id_forwarded_verbatim_in_continue_mode():
    """Same forwarding contract holds for ``mode="continue"`` — pin
    both modes so a mutant that branches on mode for the id value is
    caught."""
    argv = build_claude_command(_spec("abc-123"), "continue")
    assert argv[-1] == "abc-123"


# ---------- None/empty session id falls back to "" ----------


def test_none_session_id_becomes_empty_string_in_run_mode():
    """``runtime_session_id=None`` → trailing element is ``""``. The
    empty placeholder keeps the argv positional shape so the parser
    doesn't consume the next flag as the id."""
    argv = build_claude_command(_spec(None), "run")
    assert argv[-1] == ""


def test_continue_mode_raises_on_missing_session_id():
    """``mode="continue"`` with a falsy ``runtime_session_id`` is a
    programming error — ``--resume ""`` doesn't identify any session.
    Pin both ``None`` and ``""`` so a mutant that drops the guard or
    narrows it to only one falsy value is caught."""
    with pytest.raises(ValueError, match="continue"):
        build_claude_command(_spec(None), "continue")
    with pytest.raises(ValueError, match="continue"):
        build_claude_command(_spec(""), "continue")


# ---------- full argv shape ----------


def test_full_argv_shape_run_mode():
    """End-to-end shape for ``mode="run"`` — the exact list handed to
    ``sprite.command(...)``. Distinguishes a mutant that injects a stray
    flag or drops one silently."""
    argv = build_claude_command(_spec("sess-xyz"), "run")
    assert argv == [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--session-id",
        "sess-xyz",
    ]


def test_full_argv_shape_continue_mode():
    """End-to-end shape for ``mode="continue"`` — only the second-to-
    last flag flips from ``--session-id`` to ``--resume``."""
    argv = build_claude_command(_spec("sess-xyz"), "continue")
    assert argv == [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--resume",
        "sess-xyz",
    ]


def test_full_argv_shape_run_mode_with_none_session_id():
    """End-to-end shape when no session id has been allocated yet —
    trailing pair is ``["--session-id", ""]`` exactly."""
    argv = build_claude_command(_spec(None), "run")
    assert argv == [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--session-id",
        "",
    ]


def test_argv_length_is_eight():
    """Total argv length is always 8 — pin so a mutant that drops or
    duplicates an element is caught."""
    assert len(build_claude_command(_spec("s"), "run")) == 8
    assert len(build_claude_command(_spec("s"), "continue")) == 8
    assert len(build_claude_command(_spec(None), "run")) == 8
