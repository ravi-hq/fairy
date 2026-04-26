"""Direct unit tests for the environment-validation module.

Mutation-tested. Three validators (packages / env_vars / networking)
plus their constants. Each test isolates one mutation-killable branch.

Tests are sync, no Django fixtures, no parametrize — required so
hammett (mutmut's runner) can execute them.
"""

import pytest

from agent_on_demand.environment_validation import (
    ENV_VAR_KEY_RE,
    VALID_NETWORKING_TYPES,
    VALID_PACKAGE_MANAGERS,
    validate_env_vars,
    validate_networking,
    validate_packages,
)


# ---------- packages ----------


def test_validate_packages_empty_dict_accepted():
    assert validate_packages({}) == {}


def test_validate_packages_each_known_manager_accepted():
    """Every manager in the allowlist works. Distinguishes a mutant
    that drops one of the literals from the set."""
    for manager in ("apt", "cargo", "gem", "go", "npm", "pip"):
        v = {manager: ["pkg"]}
        assert validate_packages(v) == v


def test_validate_packages_unknown_manager_rejects():
    with pytest.raises(ValueError, match="Unknown package manager: yarn"):
        validate_packages({"yarn": ["express"]})


def test_validate_packages_error_lists_valid_options():
    """Operators see the list of valid managers in the error so they
    can correct the typo. Pinned because dropping the listing would
    still raise — but with a less helpful message."""
    with pytest.raises(ValueError) as exc:
        validate_packages({"yarn": ["x"]})
    detail = str(exc.value)
    for known in ("apt", "cargo", "gem", "go", "npm", "pip"):
        assert known in detail


def test_validate_packages_error_listing_is_sorted():
    """Sorted output → stable error message. Distinguishes a mutant
    that drops the ``sorted(...)`` call."""
    with pytest.raises(ValueError) as exc:
        validate_packages({"yarn": ["x"]})
    detail = str(exc.value)
    # Alphabetical: apt before cargo before gem...
    assert detail.index("apt") < detail.index("cargo") < detail.index("gem")


def test_validate_packages_returns_input_object_for_caller_chaining():
    """Pydantic field validators must return the validated value so
    the caller can chain them. Pinned so a refactor that drops the
    return statement doesn't slip past the type checker."""
    v = {"pip": ["pandas"]}
    assert validate_packages(v) is v


def test_validate_packages_first_unknown_short_circuits():
    """A dict with one valid + one invalid manager rejects on the
    invalid one — no partial success."""
    with pytest.raises(ValueError, match="Unknown package manager: yarn"):
        validate_packages({"pip": ["pandas"], "yarn": ["express"]})


# ---------- env_vars ----------


def test_validate_env_vars_empty_dict_accepted():
    assert validate_env_vars({}) == {}


def test_validate_env_vars_alphanumeric_underscore_accepted():
    """The regex permits any standard POSIX shell variable name."""
    v = {"MY_VAR": "v", "ABC_123": "v", "_LEADING_UNDER": "v", "x": "v"}
    assert validate_env_vars(v) == v


def test_validate_env_vars_leading_digit_rejects():
    """Leading digit is the textbook reason POSIX env-var names have a
    regex — would parse as a number in some shells."""
    with pytest.raises(ValueError, match="Invalid env_var key '1BAD'"):
        validate_env_vars({"1BAD": "v"})


def test_validate_env_vars_hyphen_rejects():
    """Hyphen would break ``KEY=value`` parsing in the bash heredoc
    (``BAD-KEY`` parses as ``BAD`` minus value of ``KEY``)."""
    with pytest.raises(ValueError, match="Invalid env_var key 'BAD-KEY'"):
        validate_env_vars({"BAD-KEY": "v"})


def test_validate_env_vars_dot_rejects():
    """Dots would similarly confuse the shell."""
    with pytest.raises(ValueError, match="Invalid env_var key 'BAD.KEY'"):
        validate_env_vars({"BAD.KEY": "v"})


def test_validate_env_vars_empty_key_rejects():
    """Empty key would produce ``=value`` — a syntax error."""
    with pytest.raises(ValueError, match="Invalid env_var key ''"):
        validate_env_vars({"": "v"})


def test_validate_env_vars_embedded_newline_rejects():
    """A literal newline inside a key would split the
    ``KEY=value`` heredoc line into two — turning ``BAD\\nINJECT=evil``
    on the second line into a separate shell assignment. ``.match()``
    against ``r"^...$"`` would have accepted this (``$`` matches before
    a trailing ``\\n`` in default mode); ``.fullmatch()`` rejects."""
    with pytest.raises(ValueError, match="Invalid env_var key"):
        validate_env_vars({"GOOD\nINJECT": "v"})


def test_validate_env_vars_returns_input_object():
    v = {"MY_VAR": "value"}
    assert validate_env_vars(v) is v


# ---------- networking ----------


def test_validate_networking_unrestricted_default_accepted():
    """Default config — no allowed_hosts needed."""
    v = {"type": "unrestricted"}
    assert validate_networking(v) is v


def test_validate_networking_missing_type_defaults_to_unrestricted():
    """``type`` is optional and defaults to ``unrestricted`` —
    matches the Create request's default factory. Distinguishes a
    mutant that drops the ``.get(... "unrestricted")`` default."""
    v = {}
    assert validate_networking(v) is v


def test_validate_networking_limited_with_list_accepted():
    v = {"type": "limited", "allowed_hosts": ["example.com", "api.example.com"]}
    assert validate_networking(v) is v


def test_validate_networking_limited_with_empty_list_accepted():
    """Empty list means "deny all" — valid config, distinct from
    omitted ``allowed_hosts``."""
    v = {"type": "limited", "allowed_hosts": []}
    assert validate_networking(v) is v


def test_validate_networking_limited_with_missing_allowed_hosts_accepted():
    """``allowed_hosts`` is optional; default is empty list. The
    ``isinstance`` check operates on the default, which is a list, so
    no error."""
    v = {"type": "limited"}
    assert validate_networking(v) is v


def test_validate_networking_unknown_type_rejects():
    """Exact-match assertion — distinguishes a mutant that wraps the
    literal in extra characters (e.g. ``"XXmessageXX"``)."""
    with pytest.raises(ValueError) as exc:
        validate_networking({"type": "open"})
    assert str(exc.value) == "networking.type must be 'unrestricted' or 'limited'"


def test_validate_networking_limited_with_string_hosts_rejects():
    """Non-list ``allowed_hosts`` would silently bypass the firewall
    config — this branch must reject. Exact-match assertion so a
    mutant that wraps the literal in extra characters (e.g.
    ``"XXmessageXX"``) is still caught."""
    with pytest.raises(ValueError) as exc:
        validate_networking({"type": "limited", "allowed_hosts": "evil.example.com"})
    assert str(exc.value) == "networking.allowed_hosts must be a list"


def test_validate_networking_limited_with_dict_hosts_rejects():
    with pytest.raises(ValueError) as exc:
        validate_networking({"type": "limited", "allowed_hosts": {"evil": "x"}})
    assert str(exc.value) == "networking.allowed_hosts must be a list"


def test_validate_networking_unrestricted_with_string_hosts_accepted():
    """The hosts-shape check only runs when type is ``limited`` —
    ``unrestricted`` ignores ``allowed_hosts`` entirely. Distinguishes
    a mutant that runs the check unconditionally."""
    # Should not raise — unrestricted ignores allowed_hosts.
    v = {"type": "unrestricted", "allowed_hosts": "anything goes"}
    assert validate_networking(v) is v


# ---------- exported constants ----------


def test_valid_package_managers_contents():
    assert VALID_PACKAGE_MANAGERS == frozenset({"apt", "cargo", "gem", "go", "npm", "pip"})


def test_valid_package_managers_is_frozenset():
    """Frozenset = immutable + hashable. Pin so a refactor to mutable
    set doesn't slip in."""
    assert isinstance(VALID_PACKAGE_MANAGERS, frozenset)


def test_valid_networking_types_contents():
    assert VALID_NETWORKING_TYPES == frozenset({"unrestricted", "limited"})


def test_env_var_key_re_accepts_valid_names():
    assert ENV_VAR_KEY_RE.fullmatch("MY_VAR") is not None
    assert ENV_VAR_KEY_RE.fullmatch("_x") is not None
    assert ENV_VAR_KEY_RE.fullmatch("a") is not None


def test_env_var_key_re_rejects_invalid_names():
    """Validator pairs the regex with ``.fullmatch()`` — must match the
    *whole* string, not a substring or up-to-trailing-``\\n``. Pinned
    because switching to ``.match()`` (or re-adding ``^...$`` anchors)
    would let ``MY_VAR=evil`` or ``GOOD\\nBAD`` slip through."""
    assert ENV_VAR_KEY_RE.fullmatch("1bad") is None
    assert ENV_VAR_KEY_RE.fullmatch("bad-name") is None
    assert ENV_VAR_KEY_RE.fullmatch("") is None
    # Substring match would accept this; fullmatch rejects.
    assert ENV_VAR_KEY_RE.fullmatch("good=evil") is None
    # Default-mode ``$`` matches before a trailing ``\n`` — fullmatch
    # rejects, ``.match()`` against ``r"^...$"`` would have accepted.
    assert ENV_VAR_KEY_RE.fullmatch("GOOD\nBAD") is None
