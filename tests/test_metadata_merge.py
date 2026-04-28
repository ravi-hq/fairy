"""Direct unit tests for `merge_metadata`.

Mutation-tested. Each test isolates one mutation-killable branch:

  - the `dict(current)` copy (vs. mutating `current` in place)
  - the `for k, v in patch.items()` source (vs. `current.items()`)
  - the `if v == ""` literal check (vs. truthiness or `is None`)
  - the `merged.pop(k, None)` default (vs. `pop(k)` which would KeyError)
  - the `merged[k] = v` assignment (vs. setdefault, no-op)
  - the `return merged` (vs. returning current)

Tests are sync, no Django fixtures, no pytest-asyncio, no parametrize —
required so hammett (mutmut's runner, which doesn't load pytest plugins)
can execute them.
"""

from agent_on_demand.validation.metadata_merge import merge_metadata


def test_empty_patch_returns_unchanged_copy():
    """An empty patch is the no-op case. The returned dict equals
    `current` and is a *separate* object — the `is not` check kills
    a mutmut mutant that drops the `dict(current)` copy and returns
    `current` directly. (The caller's `if merged != agent.metadata`
    comparison is value-based so it would still work either way; the
    isolation matters for callers that mutate the result.)"""
    current = {"a": "1", "b": "2"}
    result = merge_metadata(current, {})
    assert result == {"a": "1", "b": "2"}
    assert result is not current


def test_new_key_added():
    """A patch key absent from `current` is inserted with its value.
    Distinguishes mutants that iterate `current.items()` instead of
    `patch.items()` (those would never add the new key)."""
    assert merge_metadata({"a": "1"}, {"b": "2"}) == {"a": "1", "b": "2"}


def test_existing_key_overwritten():
    """A patch key that already exists in `current` is overwritten with
    the new value. Distinguishes mutants that drop the assignment or
    swap to `setdefault` (which would keep the old value)."""
    assert merge_metadata({"a": "1"}, {"a": "2"}) == {"a": "2"}


def test_empty_string_value_deletes_existing_key():
    """The literal empty string `""` triggers a delete. This is the
    Anthropic-compatible semantic SDKs depend on. Distinguishes mutants
    that swap `if v == ""` to `if v != ""` (would delete every non-empty
    value) or `if not v` (would also match 0 / False / None)."""
    assert merge_metadata({"a": "1"}, {"a": ""}) == {}


def test_empty_string_value_for_missing_key_is_noop():
    """Deleting a key that wasn't in `current` must be silent — no
    KeyError. Distinguishes mutants that drop the `None` default from
    `merged.pop(k, None)`, which would raise KeyError here."""
    assert merge_metadata({}, {"missing": ""}) == {}


def test_zero_value_does_not_delete():
    """Only the literal empty string triggers a delete. `0` is a
    falsy value but is a legitimate metadata value to store. Pins the
    `== ""` check against truthiness-based mutants like `if not v`."""
    assert merge_metadata({"count": "1"}, {"count": 0}) == {"count": 0}


def test_none_value_does_not_delete():
    """`None` is also falsy but should pass through as a stored value
    rather than triggering a delete. Pins `== ""` vs `is None`."""
    assert merge_metadata({"x": "1"}, {"x": None}) == {"x": None}


def test_mixed_add_overwrite_delete_in_one_call():
    """All three operations in one patch — pinned together so a refactor
    that handles them in different code paths still produces the same
    final dict."""
    current = {"a": "1", "b": "2", "c": "3"}
    patch = {"a": "X", "b": "", "d": "4"}  # overwrite a, delete b, add d
    assert merge_metadata(current, patch) == {"a": "X", "c": "3", "d": "4"}


def test_does_not_mutate_current():
    """`current` must be untouched after the call so the caller's
    pre/post-merge comparison still works. Distinguishes a mutant that
    drops the `dict(current)` copy and operates on `current` in place."""
    current = {"a": "1", "b": "2"}
    merge_metadata(current, {"a": "X", "b": ""})
    assert current == {"a": "1", "b": "2"}


def test_does_not_mutate_patch():
    """`patch` is also caller-owned input. Pinning this too — even
    though the current implementation only reads `patch`, a refactor
    that pops processed keys could violate it without changing the
    return value."""
    patch = {"a": "X", "b": ""}
    merge_metadata({"a": "1", "b": "2"}, patch)
    assert patch == {"a": "X", "b": ""}


def test_empty_inputs_return_empty_dict():
    """Both inputs empty → empty dict out. Edge case at the boundary."""
    assert merge_metadata({}, {}) == {}


def test_only_deletes_in_patch_against_empty_current():
    """A patch full of deletes against an empty `current` is a no-op —
    no KeyError, returns empty dict."""
    assert merge_metadata({}, {"a": "", "b": "", "c": ""}) == {}


def test_value_types_other_than_str_are_preserved():
    """The function's contract doesn't restrict patch values to strings;
    the API validator allows any JSON value. Anything that isn't the
    literal `""` is stored as-is — int, list, dict, bool all flow
    through."""
    result = merge_metadata({}, {"n": 42, "l": [1, 2], "d": {"x": "y"}, "b": True})
    assert result == {"n": 42, "l": [1, 2], "d": {"x": "y"}, "b": True}


def test_patch_iteration_independent_of_current_keys():
    """If `current` has a key whose value happens to be `""`, that key
    is *not* deleted unless `patch` explicitly says so — distinguishes a
    mutant that scans `current.items()` for empty values."""
    assert merge_metadata({"keep": ""}, {}) == {"keep": ""}


def test_idempotent_overwrite_is_equal():
    """Overwriting with the same value yields an equal dict."""
    assert merge_metadata({"a": "1"}, {"a": "1"}) == {"a": "1"}


def test_delete_of_missing_key_leaves_others_alone():
    """A delete-missing-key patch doesn't disturb other entries in
    `current`."""
    assert merge_metadata({"a": "1"}, {"b": ""}) == {"a": "1"}


def test_delete_existing_key_leaves_siblings():
    """Deleting one key in `current` doesn't affect the others."""
    assert merge_metadata({"a": "1", "b": "2"}, {"a": ""}) == {"b": "2"}
