from dataclasses import dataclass
from enum import StrEnum


class AgentModel(StrEnum):
    # Claude
    CLAUDE_OPUS_4_6 = "claude-opus-4-6"
    CLAUDE_SONNET_4_6 = "claude-sonnet-4-6"
    CLAUDE_HAIKU_4_5 = "claude-haiku-4-5"
    CLAUDE_OPUS_4 = "claude-opus-4-0-20250514"
    CLAUDE_SONNET_4 = "claude-sonnet-4-0-20250514"
    CLAUDE_SONNET_4_5 = "claude-sonnet-4-5-20250514"
    CLAUDE_HAIKU_3_5 = "claude-3-5-haiku-20241022"
    # OpenAI / Codex
    GPT_4_1 = "gpt-4.1"
    O3 = "o3"
    O4_MINI = "o4-mini"
    # Gemini
    GEMINI_2_5_PRO = "gemini-2.5-pro"
    GEMINI_2_5_FLASH = "gemini-2.5-flash"

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(m.value, m.value) for m in cls]

    @classmethod
    def values(cls) -> set[str]:
        return {m.value for m in cls}


# Which runtime each model family maps to
MODEL_RUNTIME_MAP: dict[str, str] = {
    "claude-opus-4-6": "claude",
    "claude-sonnet-4-6": "claude",
    "claude-haiku-4-5": "claude",
    "claude-opus-4-0-20250514": "claude",
    "claude-sonnet-4-0-20250514": "claude",
    "claude-sonnet-4-5-20250514": "claude",
    "claude-3-5-haiku-20241022": "claude",
    "gpt-4.1": "codex",
    "o3": "codex",
    "o4-mini": "codex",
    "gemini-2.5-pro": "gemini",
    "gemini-2.5-flash": "gemini",
}


@dataclass(frozen=True)
class RuntimeConfig:
    name: str
    cmd: str  # shell command template — $PROMPT is substituted by the wrapper script
    continue_cmd: str  # command template for continuing an existing session
    env_var: str  # which env var holds the API key


RUNTIMES: dict[str, RuntimeConfig] = {
    "claude": RuntimeConfig(
        name="claude",
        cmd='claude --print --verbose --output-format stream-json -p "$PROMPT"',
        continue_cmd='claude --print --verbose --output-format stream-json --continue -p "$PROMPT"',
        env_var="ANTHROPIC_API_KEY",
    ),
    "codex": RuntimeConfig(
        name="codex",
        cmd='echo "$PROMPT" | codex exec --full-auto --json',
        continue_cmd='codex exec resume --last --full-auto --json "$PROMPT"',
        env_var="CODEX_API_KEY",
    ),
    "gemini": RuntimeConfig(
        name="gemini",
        cmd='gemini --output-format stream-json -p "$PROMPT"',
        continue_cmd='gemini --resume --output-format stream-json -p "$PROMPT"',
        env_var="GEMINI_API_KEY",
    ),
    "claude-oauth": RuntimeConfig(
        name="claude-oauth",
        cmd='claude --print --verbose --output-format stream-json -p "$PROMPT"',
        continue_cmd='claude --print --verbose --output-format stream-json --continue -p "$PROMPT"',
        env_var="CLAUDE_CODE_AUTH_TOKEN",
    ),
}
