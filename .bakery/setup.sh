#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv &>/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

uv sync --extra dev
uv pip install -e . --quiet

# Make venv tools available on PATH for gate commands
ln -sf "$(pwd)/.venv/bin/ruff" /usr/local/bin/ruff 2>/dev/null || true
ln -sf "$(pwd)/.venv/bin/pytest" /usr/local/bin/pytest 2>/dev/null || true
ln -sf "$(pwd)/.venv/bin/mypy" /usr/local/bin/mypy 2>/dev/null || true
ln -sf "$(pwd)/.venv/bin/python" /usr/local/bin/python 2>/dev/null || true
