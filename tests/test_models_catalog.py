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
