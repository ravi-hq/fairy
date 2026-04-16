from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    name: str
    cmd: str  # shell command template — $PROMPT is substituted by the wrapper script
    env_var: str  # which env var holds the API key


RUNTIMES: dict[str, RuntimeConfig] = {
    "claude": RuntimeConfig(
        name="claude",
        cmd='claude --print --verbose --output-format stream-json -p "$PROMPT"',
        env_var="ANTHROPIC_API_KEY",
    ),
    "codex": RuntimeConfig(
        name="codex",
        cmd='echo "$PROMPT" | codex exec --full-auto --json',
        env_var="CODEX_API_KEY",
    ),
    "gemini": RuntimeConfig(
        name="gemini",
        cmd='gemini --output-format stream-json -p "$PROMPT"',
        env_var="GEMINI_API_KEY",
    ),
}
