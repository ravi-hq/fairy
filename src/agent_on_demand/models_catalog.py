from dataclasses import dataclass


@dataclass(frozen=True)
class ModelDef:
    id: str  # canonical "provider/model_id" string
    provider: str  # "anthropic", "openai", "google"
    runtimes: frozenset[str] | None = (
        None  # optional restriction; None = any runtime whose providers includes this provider
    )


MODELS: dict[str, ModelDef] = {
    "anthropic/claude-opus-4-6": ModelDef(id="anthropic/claude-opus-4-6", provider="anthropic"),
    "anthropic/claude-sonnet-4-6": ModelDef(id="anthropic/claude-sonnet-4-6", provider="anthropic"),
    "anthropic/claude-haiku-4-5": ModelDef(id="anthropic/claude-haiku-4-5", provider="anthropic"),
    "anthropic/claude-opus-4-0-20250514": ModelDef(
        id="anthropic/claude-opus-4-0-20250514", provider="anthropic"
    ),
    "anthropic/claude-sonnet-4-0-20250514": ModelDef(
        id="anthropic/claude-sonnet-4-0-20250514", provider="anthropic"
    ),
    "anthropic/claude-sonnet-4-5-20250514": ModelDef(
        id="anthropic/claude-sonnet-4-5-20250514", provider="anthropic"
    ),
    "anthropic/claude-3-5-haiku-20241022": ModelDef(
        id="anthropic/claude-3-5-haiku-20241022", provider="anthropic"
    ),
    "openai/gpt-4.1": ModelDef(id="openai/gpt-4.1", provider="openai"),
    "openai/o3": ModelDef(id="openai/o3", provider="openai"),
    "openai/o4-mini": ModelDef(id="openai/o4-mini", provider="openai"),
    "google/gemini-2.5-pro": ModelDef(id="google/gemini-2.5-pro", provider="google"),
    "google/gemini-2.5-flash": ModelDef(id="google/gemini-2.5-flash", provider="google"),
}
