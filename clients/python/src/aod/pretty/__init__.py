"""Optional, runtime-scoped pretty-printers for session output streams.

These helpers parse a specific runtime's stdout format into human-readable
lines. They're intentionally not exported from the top-level `aod` namespace
— the SDK core is runtime-agnostic; formatters are not.

Currently shipped:
    - `aod.pretty.claude.ClaudeFormatter` — Claude `stream-json` output.
"""

from .claude import ClaudeFormatter

__all__ = ["ClaudeFormatter"]
