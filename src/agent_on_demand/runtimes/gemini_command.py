"""Build the per-turn argv for the Gemini CLI.

Extracted from `runtimes/gemini.py` so the run-vs-continue branch can be
mutation-tested in isolation. The original
`GeminiRuntime.build_command` is now a thin delegator.

Two branches, both pinned by `tests/test_gemini_command.py`:

  - ``mode="run"`` → ``["gemini", "--output-format", "stream-json"]``
  - ``mode="continue"`` → ``["gemini", "--resume", "--output-format",
    "stream-json"]`` — ``--resume`` is inserted immediately after the
    binary so it precedes any output-format flags, matching how the
    Gemini CLI parses subcommand-style options.

Both modes always use ``--output-format stream-json`` so the worker can
parse a single line-delimited JSON stream regardless of which mode the
session is running in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import SessionSpec


def build_gemini_command(
    _spec: SessionSpec,
    mode: Literal["run", "continue"],
) -> list[str]:
    """Return the Gemini CLI argv for a single turn.

    ``_spec`` is currently unused — Gemini's CLI doesn't take any
    spec-derived flags at the top level — but the parameter is kept so
    this function matches the ``Runtime.build_command`` shape and can
    accept spec-derived flags later without changing every caller. The
    leading underscore signals intentional non-use to readers and lint
    rules (ARG001).
    """
    if mode == "continue":
        return ["gemini", "--resume", "--output-format", "stream-json"]
    return ["gemini", "--output-format", "stream-json"]
