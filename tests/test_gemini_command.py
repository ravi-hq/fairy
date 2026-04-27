"""Direct unit tests for `build_gemini_command`.

Mutation-tested. Each test pins one mutation-killable property of the
Gemini per-turn argv:

  - ``mode="run"`` → exact 3-element argv ending in ``stream-json``.
  - ``mode="continue"`` → exact 4-element argv with ``--resume`` at
    index 1, before the output-format pair.
  - Both modes start with ``gemini`` and end with ``stream-json``.
  - ``--output-format`` is always immediately followed by
    ``stream-json`` — a mutant that swaps the value or splits the pair
    is caught.
  - Only ``mode="continue"`` contains ``--resume``.
  - The two argvs differ only by the inserted ``--resume`` (length
    differs by 1).

Tests are sync, no Django imports — required so hammett (mutmut's
runner, which doesn't load pytest plugins) can execute them.
``SessionSpec`` is duck-typed via ``SimpleNamespace``; the function
doesn't read any spec attributes today.
"""

from types import SimpleNamespace

from agent_on_demand.runtimes.gemini_command import build_gemini_command


def _spec():
    return SimpleNamespace()


# ---------- exact argv per mode ----------


def test_run_mode_exact_argv():
    """``mode="run"`` produces exactly ``["gemini", "--output-format",
    "stream-json"]`` — pin the full list so a mutant that drops, adds,
    or reorders any element is caught."""
    assert build_gemini_command(_spec(), "run") == [
        "gemini",
        "--output-format",
        "stream-json",
    ]


def test_continue_mode_exact_argv():
    """``mode="continue"`` produces exactly ``["gemini", "--resume",
    "--output-format", "stream-json"]`` — pin the full list including
    ``--resume`` placement."""
    assert build_gemini_command(_spec(), "continue") == [
        "gemini",
        "--resume",
        "--output-format",
        "stream-json",
    ]


# ---------- shared shape ----------


def test_run_mode_starts_with_gemini_binary():
    """First element is the literal ``"gemini"`` binary name. A mutant
    that swaps the binary or empties the prefix is caught."""
    assert build_gemini_command(_spec(), "run")[0] == "gemini"


def test_continue_mode_starts_with_gemini_binary():
    """First element is the literal ``"gemini"`` binary name even with
    ``--resume`` injected — pin so a mutant that puts ``--resume``
    ahead of the binary is caught."""
    assert build_gemini_command(_spec(), "continue")[0] == "gemini"


def test_run_mode_ends_with_stream_json():
    """Last element is ``"stream-json"`` — the value that lets the
    worker parse a single line-delimited JSON stream. A mutant that
    swaps the value or appends after it is caught."""
    assert build_gemini_command(_spec(), "run")[-1] == "stream-json"


def test_continue_mode_ends_with_stream_json():
    """Last element is ``"stream-json"`` in continue mode too."""
    assert build_gemini_command(_spec(), "continue")[-1] == "stream-json"


# ---------- output-format pair is contiguous ----------


def test_run_mode_output_format_pair_is_contiguous():
    """``--output-format`` is immediately followed by ``stream-json``
    — a mutant that splits the pair (e.g. inserts another flag
    between them) or swaps the value to ``json``/``text`` is caught."""
    argv = build_gemini_command(_spec(), "run")
    idx = argv.index("--output-format")
    assert argv[idx + 1] == "stream-json"


def test_continue_mode_output_format_pair_is_contiguous():
    """``--output-format`` is immediately followed by ``stream-json``
    in continue mode too — pin so the ``--resume`` insertion doesn't
    accidentally land between the flag and its value."""
    argv = build_gemini_command(_spec(), "continue")
    idx = argv.index("--output-format")
    assert argv[idx + 1] == "stream-json"


# ---------- --resume is mode-exclusive ----------


def test_resume_flag_only_in_continue_mode():
    """``--resume`` appears in continue mode but never in run mode. A
    mutant that always emits ``--resume`` (or never emits it) breaks
    one of these assertions."""
    assert "--resume" in build_gemini_command(_spec(), "continue")
    assert "--resume" not in build_gemini_command(_spec(), "run")


def test_resume_flag_at_index_one_in_continue_mode():
    """``--resume`` is at index 1 — directly after the binary name and
    before ``--output-format``. A mutant that moves it to a different
    position would still produce a syntactically valid argv but is
    semantically wrong (the Gemini CLI parses subcommand-style flags
    from left to right)."""
    assert build_gemini_command(_spec(), "continue")[1] == "--resume"


# ---------- branch differential ----------


def test_continue_argv_is_run_argv_with_resume_inserted():
    """The two argvs differ only by ``--resume`` inserted at index 1.
    Pin the differential so a mutant that changes any other element
    in one branch but not the other is caught."""
    run_argv = build_gemini_command(_spec(), "run")
    continue_argv = build_gemini_command(_spec(), "continue")
    assert len(continue_argv) == len(run_argv) + 1
    assert continue_argv[0] == run_argv[0]
    assert continue_argv[1] == "--resume"
    assert continue_argv[2:] == run_argv[1:]
