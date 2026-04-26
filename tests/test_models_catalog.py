import pytest

from agent_on_demand.models_catalog import MODELS, ModelDef


EXPECTED_MODELS = {
    "anthropic/claude-opus-4-6",
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-haiku-4-5",
    "anthropic/claude-opus-4-0-20250514",
    "anthropic/claude-sonnet-4-0-20250514",
    "anthropic/claude-sonnet-4-5-20250514",
    "anthropic/claude-3-5-haiku-20241022",
    "openai/gpt-4.1",
    "openai/o3",
    "openai/o4-mini",
    "google/gemini-2.5-pro",
    "google/gemini-2.5-flash",
}


def test_models_catalog_importable():
    assert MODELS is not None
    assert ModelDef is not None


def test_all_entries_have_provider_matching_id_prefix():
    for key, model_def in MODELS.items():
        prefix = key.split("/")[0]
        assert model_def.provider == prefix, (
            f"{key}: provider '{model_def.provider}' does not match id prefix '{prefix}'"
        )
        assert model_def.id == key, f"ModelDef.id '{model_def.id}' does not match dict key '{key}'"


def test_models_catalog_covers_all_expected_keys():
    assert set(MODELS) >= EXPECTED_MODELS


def test_model_def_is_frozen():
    defn = ModelDef(id="test/model", provider="test")
    with pytest.raises((AttributeError, TypeError)):
        defn.provider = "other"  # type: ignore[misc]


def test_runtimes_field_defaults_to_none():
    defn = ModelDef(id="anthropic/claude-sonnet-4-6", provider="anthropic")
    assert defn.runtimes is None


def test_every_model_is_servable_by_at_least_one_runtime():
    """No orphaned models: every entry in MODELS must be servable by at
    least one runtime whose `providers` set includes the model's
    provider. Otherwise creating an agent with that model would always
    fail at session-create with a runtime/provider mismatch — a
    catalog/runtime drift bug that's painful to diagnose in production."""
    from agent_on_demand.runtimes import RUNTIMES

    for model_id, model in MODELS.items():
        servers = [
            name for name, runtime in RUNTIMES.items() if model.provider in runtime.providers
        ]
        assert servers, (
            f"Model {model_id!r} has provider {model.provider!r} "
            f"but no runtime in RUNTIMES advertises support for it. "
            f"Either add the provider to a runtime's providers set, or "
            f"remove the model from MODELS."
        )

        # If the model restricts to specific runtimes, every restriction
        # target must (a) actually exist in RUNTIMES and (b) be among the
        # servers we just computed.
        if model.runtimes is not None:
            for restricted_to in model.runtimes:
                assert restricted_to in RUNTIMES, (
                    f"Model {model_id!r}.runtimes references {restricted_to!r} "
                    f"which is not in RUNTIMES."
                )
                assert restricted_to in servers, (
                    f"Model {model_id!r}.runtimes restricts to {restricted_to!r} "
                    f"but that runtime does not advertise provider {model.provider!r}."
                )


def test_every_runtime_provider_has_at_least_one_model():
    """Reverse-direction sanity check: every provider advertised by some
    runtime has at least one model in MODELS targeting it. Otherwise a
    runtime is half-wired — listed in RUNTIMES, mentioned in docs, but
    unusable because no model targets it."""
    from agent_on_demand.runtimes import RUNTIMES

    advertised_providers = {p for runtime in RUNTIMES.values() for p in runtime.providers}
    used_providers = {model.provider for model in MODELS.values()}
    orphan_providers = advertised_providers - used_providers
    assert not orphan_providers, (
        f"Runtime(s) advertise provider(s) {sorted(orphan_providers)!r} but no model "
        f"in MODELS targets them. Either remove from runtime.providers or "
        f"add a model entry."
    )
