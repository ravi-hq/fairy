"""Direct unit tests for `compute_final_status`.

Mutation-tested. Each test pins one mutation-killable property of the
worker-thread result resolution:

  - `("exit", 0)` is the *only* path to ``"completed"`` — both the
    ``kind == "exit"`` and ``value == 0`` halves of the guard matter.
  - Non-zero exit codes (positive, negative-signal-style, OOM-style)
    must all map to ``"failed"`` while preserving the integer code.
  - Thread-error tuples (``("error", ...)``) drop the message and
    surface as ``("failed", None)``.
  - An empty holder (thread crashed before populating) is treated
    identically to ``("error", ...)``.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them.
"""

from agent_on_demand.session_service.turn.outcome import compute_final_status


def test_clean_exit_is_completed():
    assert compute_final_status([("exit", 0)]) == ("completed", 0)


def test_nonzero_exit_is_failed():
    assert compute_final_status([("exit", 1)]) == ("failed", 1)


def test_negative_exit_code_is_failed():
    # Signal-style negative exit codes must not be confused with success.
    # Pins the `value == 0` half of the completed-guard against `!= 0`
    # and `or`-style mutants.
    assert compute_final_status([("exit", -15)]) == ("failed", -15)


def test_oom_style_exit_is_failed():
    assert compute_final_status([("exit", 137)]) == ("failed", 137)


def test_error_tuple_is_failed_with_no_exit_code():
    # Pins the `kind == "exit"` half of the completed-guard — a "drop
    # the kind check" mutant would let this return ("completed", ...).
    assert compute_final_status([("error", "boom")]) == ("failed", None)


def test_empty_holder_is_failed_with_no_exit_code():
    assert compute_final_status([]) == ("failed", None)


def test_malformed_exit_tuple_with_string_value_is_failed_with_no_exit_code():
    # If the inner thread ever populated ``("exit", <non-int>)`` (a
    # programming error), the non-int must not flow through to the
    # IntegerField on the session/turn row. Pins the runtime
    # isinstance guard, which is what defends the contract under
    # ``python -O`` (where ``assert`` would be stripped).
    assert compute_final_status([("exit", "not-an-int")]) == ("failed", None)
