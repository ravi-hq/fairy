"""Optional, runtime-scoped pretty-printers for session output streams.

These helpers parse a specific runtime's stdout format into human-readable
lines. They're intentionally not exported from the top-level `aod` namespace
— the SDK core is runtime-agnostic; formatters are not.

The `Formatter` protocol pins the shape every formatter implements
(`consume(event) -> Iterator[str]`, `feed(chunk) -> Iterator[str]`,
`flush() -> Iterator[str]`); the `formatter_for(runtime)` factory picks
the right one by runtime name and falls back to a passthrough for
runtimes the SDK doesn't yet have a typed formatter for.

Shipped today:
    - `aod.pretty.claude.ClaudeFormatter` — Claude `stream-json` output.
    - `aod.pretty.GenericFormatter` — passthrough fallback that emits
      stdout lines verbatim.
"""

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from ..models import StreamEvent
from .claude import ClaudeFormatter


@runtime_checkable
class Formatter(Protocol):
    """Shape every runtime-specific pretty-printer implements.

    `consume()` is the event-oriented entry point — pass a `StreamEvent`
    from `client.sessions.stream(...)` and get zero or more display
    lines back. `feed()` and `flush()` are the lower-level escape hatches
    for callers that already extracted stdout bytes themselves.
    """

    def consume(self, event: StreamEvent) -> Iterator[str]: ...
    def feed(self, chunk: str) -> Iterator[str]: ...
    def flush(self) -> Iterator[str]: ...


class GenericFormatter:
    """Runtime-agnostic passthrough formatter.

    Returned by `formatter_for(...)` when the runtime doesn't have a
    typed formatter yet. Yields the raw stdout chunk verbatim, split on
    newlines (so each line is one display line), with no JSON parsing.
    Suitable for any runtime whose stdout format is human-readable text.
    """

    def __init__(self) -> None:
        self._buf = ""

    def consume(self, event: StreamEvent) -> Iterator[str]:
        if event.type != "output":
            return
        if event.extra.get("stream") != "stdout":
            return
        data = event.extra.get("data")
        if not isinstance(data, str):
            return
        yield from self.feed(data)

    def feed(self, chunk: str) -> Iterator[str]:
        self._buf += chunk
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                yield line

    def flush(self) -> Iterator[str]:
        # Match feed()'s \r-stripping so a trailing "dangling\r" (no \n)
        # doesn't smuggle a stray carriage return into the last line.
        tail = self._buf.rstrip("\r")
        if tail.strip():
            yield tail
        self._buf = ""


def formatter_for(runtime: str | None = None) -> Formatter:
    """Return the formatter that knows how to read `runtime`'s stdout.

    Currently pattern-matches `runtime` against the registry of typed
    formatters. Unknown runtimes fall back to `GenericFormatter`, which
    emits stdout lines verbatim — useful for any runtime whose output
    is human-readable text.

    Match rules (substring on the runtime name, lowercase):
      - "claude" → `ClaudeFormatter`
      - everything else (including `None`) → `GenericFormatter`
    """
    name = (runtime or "").lower()
    if "claude" in name:
        return ClaudeFormatter()
    return GenericFormatter()


__all__ = ["ClaudeFormatter", "Formatter", "GenericFormatter", "formatter_for"]
