"""Resolve the inner worker thread's result tuple into a final session status.

Extracted from `_execute_turn_body` so the success/failure branching gets
direct, sync-only coverage under mutmut. The "completed" branch must be
guarded by `kind == "exit" AND value == 0` — any drift here either marks
real failures as success or vice versa, and integration tests don't fan
out across enough exit codes to catch every mutant.
"""

from __future__ import annotations


def compute_final_status(
    result_holder: list[tuple[str, int | str]],
) -> tuple[str, int | None]:
    """Resolve the worker thread's result tuple into (status, exit_code).

    `result_holder` carries at most one entry, set by the inner
    `_run_command` thread:
      - ``("exit", N)``: process exited with code N. status="completed"
        iff N == 0; otherwise "failed". exit_code is N.
      - ``("error", str)``: thread raised before recording an exit.
        status="failed", exit_code=None.
      - ``[]``: thread crashed before populating; treat as failure with
        no exit code.
    """
    if not result_holder:
        return "failed", None
    kind, value = result_holder[0]
    if kind != "exit" or not isinstance(value, int):
        # Non-"exit" kind covers the documented `("error", str)` shape;
        # the isinstance check defends against a malformed tuple where
        # the inner thread populated `value` with the wrong type. We
        # can't `assert` here because asserts are stripped under -O.
        return "failed", None
    if value == 0:
        return "completed", 0
    return "failed", value
