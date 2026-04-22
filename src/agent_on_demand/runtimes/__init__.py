from agent_on_demand.runtimes.base import Runtime
from agent_on_demand.runtimes.claude import ClaudeRuntime
from agent_on_demand.runtimes.codex import CodexRuntime
from agent_on_demand.runtimes.gemini import GeminiRuntime
from agent_on_demand.runtimes.opencode import OpencodeRuntime

RUNTIMES: dict[str, Runtime] = {
    "claude": ClaudeRuntime(),
    "codex": CodexRuntime(),
    "gemini": GeminiRuntime(),
    "opencode": OpencodeRuntime(),
}

__all__ = ["Runtime", "RUNTIMES"]
