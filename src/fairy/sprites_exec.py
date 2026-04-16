import shlex

from fairy.runtimes import RuntimeConfig


def build_wrapper_script(config: RuntimeConfig, api_key: str, prompt: str) -> str:
    """Build a shell script that exports the API key and runs the agent.

    Uses a wrapper script instead of passing env= on the exec call because:
    1. env= replaces the entire environment (no PATH -> binary not found)
    2. env= appears in WebSocket URL query params (API key in server logs)
    """
    return f"""#!/bin/bash
set -euo pipefail
export {config.env_var}={shlex.quote(api_key)}
export PROMPT={shlex.quote(prompt)}

# Setup working directory
cd /home/sprite
mkdir -p .gemini
if [ ! -d .git ]; then
    git init -q
    git add -A 2>/dev/null || true
    git commit -q -m "init" --allow-empty 2>/dev/null || true
fi

exec {config.cmd}
"""
