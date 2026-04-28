"""Direct unit tests for `check_runtime_model_compat`.

Mutation-tested. Covers both branches (provider mismatch, runtime
allowlist) and the ``None`` happy path. The function is duck-typed —
runtime needs ``.name`` + ``.providers``, model needs ``.id`` +
``.provider`` + ``.runtimes``. Tests use ``SimpleNamespace`` so the
test module doesn't pull Django / sprites imports.
"""

from types import SimpleNamespace

from agent_on_demand.validation.runtime_model_compat import check_runtime_model_compat


def _runtime(name="claude", providers=frozenset({"anthropic"})):
    return SimpleNamespace(name=name, providers=providers)


def _model(id="anthropic/claude-sonnet", provider="anthropic", runtimes=None):
    return SimpleNamespace(id=id, provider=provider, runtimes=runtimes)


# ---------- happy path ----------


def test_compatible_runtime_and_model_returns_none():
    """Provider matches, no runtime allowlist — runtime can serve."""
    assert check_runtime_model_compat(_runtime(), _model()) is None


def test_compatible_with_runtime_in_explicit_allowlist_returns_none():
    """Model has an explicit ``runtimes`` allowlist that includes the
    runtime's name — still compatible."""
    runtime = _runtime(name="claude")
    model = _model(runtimes=frozenset({"claude", "codex"}))
    assert check_runtime_model_compat(runtime, model) is None


# ---------- provider mismatch ----------


def test_provider_mismatch_returns_error_message():
    """Runtime serves only ``anthropic``; model is ``openai``. Reject
    with a typed message."""
    runtime = _runtime(name="claude", providers=frozenset({"anthropic"}))
    model = _model(id="openai/gpt-4", provider="openai")
    err = check_runtime_model_compat(runtime, model)
    assert err is not None
    assert "claude" in err
    assert "openai/gpt-4" in err
    assert "openai" in err


def test_provider_mismatch_message_format_is_pinned():
    """SDKs parse the message — the substring ``cannot serve model``
    must remain present so consumers can detect this specific class
    of error."""
    runtime = _runtime(name="claude", providers=frozenset({"anthropic"}))
    model = _model(id="openai/gpt-4", provider="openai")
    err = check_runtime_model_compat(runtime, model)
    assert "cannot serve model" in err


def test_provider_mismatch_lists_runtime_providers_in_message():
    """Operators see the runtime's accepted providers in the message
    so they can pick a compatible model. Pinned because the
    sorted-providers listing is a discoverable detail."""
    runtime = _runtime(name="multi", providers=frozenset({"anthropic", "openai", "google"}))
    model = _model(id="other/foo", provider="other")
    err = check_runtime_model_compat(runtime, model)
    # All three providers must appear, regardless of how the set was
    # constructed (sorted in the output).
    assert "anthropic" in err
    assert "openai" in err
    assert "google" in err


def test_provider_mismatch_provider_listing_is_sorted():
    """Sorted output makes the message stable — same set of providers
    always renders the same string. Distinguishes a mutant that drops
    the ``sorted(...)`` call (and produces unstable output)."""
    runtime = _runtime(name="multi", providers=frozenset({"openai", "anthropic"}))
    model = _model(id="other/foo", provider="other")
    err = check_runtime_model_compat(runtime, model)
    # 'anthropic' must appear before 'openai' — sorted, not insertion order.
    assert err.index("anthropic") < err.index("openai")


# ---------- runtime allowlist ----------


def test_provider_match_but_runtime_not_in_allowlist_returns_error():
    """Provider matches, but the model's ``runtimes`` allowlist
    excludes this runtime. The use case is models that only work under
    specific runtime CLIs (different invocation conventions, etc)."""
    runtime = _runtime(name="claude", providers=frozenset({"anthropic"}))
    model = _model(
        id="anthropic/special",
        provider="anthropic",
        runtimes=frozenset({"codex", "gemini"}),
    )
    err = check_runtime_model_compat(runtime, model)
    assert err is not None
    assert "claude" in err
    assert "anthropic/special" in err


def test_runtime_not_supported_message_format_is_pinned():
    """The message says ``not supported on runtime`` — pin the
    substring so SDK clients can detect this branch separately from
    provider-mismatch."""
    runtime = _runtime(name="claude", providers=frozenset({"anthropic"}))
    model = _model(provider="anthropic", runtimes=frozenset({"codex"}))
    err = check_runtime_model_compat(runtime, model)
    assert "not supported on runtime" in err


def test_provider_mismatch_takes_priority_over_runtime_allowlist():
    """Both checks fail — provider mismatch is reported first.
    Distinguishes a refactor that swaps the order of the two checks."""
    runtime = _runtime(name="claude", providers=frozenset({"anthropic"}))
    model = _model(
        id="openai/gpt-4",
        provider="openai",  # mismatched
        runtimes=frozenset({"codex"}),  # also excludes claude
    )
    err = check_runtime_model_compat(runtime, model)
    assert "cannot serve model" in err
    assert "not supported on runtime" not in err


# ---------- runtimes=None edge case ----------


def test_model_with_runtimes_none_skips_allowlist_check():
    """``runtimes=None`` is the default — model accepts any runtime
    whose providers include its provider. Pinned because dropping the
    ``is not None`` check would treat ``None`` as a falsy empty
    allowlist and reject everything."""
    runtime = _runtime(name="claude", providers=frozenset({"anthropic"}))
    model = _model(provider="anthropic", runtimes=None)
    assert check_runtime_model_compat(runtime, model) is None


def test_model_with_empty_frozenset_runtimes_rejects_everything():
    """An *empty* allowlist is different from ``None`` — it accepts
    no runtime. Pinned because an ``if not model.runtimes`` check
    would treat empty and None the same."""
    runtime = _runtime(name="claude", providers=frozenset({"anthropic"}))
    model = _model(provider="anthropic", runtimes=frozenset())
    err = check_runtime_model_compat(runtime, model)
    assert err is not None
    assert "not supported on runtime" in err
