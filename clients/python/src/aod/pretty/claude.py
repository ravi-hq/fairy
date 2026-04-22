"""Pretty-print Claude `stream-json` output for CLI display.

The `claude` runtime runs with `--output-format stream-json`, writing one
JSON object per line to stdout. Each line is one of:
  - system.init / system.task_started / system.task_progress
  - assistant.message with content blocks (thinking, tool_use, text)
  - user.message with content blocks (usually tool_result — skipped)
  - result (terminal)

`ClaudeFormatter` accumulates stdout bytes, splits on newline, parses each
line, and yields a one-liner per event ready to print. Tool-result and
task_progress noise is dropped; subagent activity is indented.

Use `.consume(event)` when iterating a session stream — it filters to
`output` events on the `stdout` stream automatically:

    from aod import Client
    from aod.pretty.claude import ClaudeFormatter

    fmt = ClaudeFormatter()
    with client.sessions.stream(session_id) as events:
        for event in events:
            for line in fmt.consume(event):
                print(line)
        for line in fmt.flush():
            print(line)

Use `.feed(chunk)` directly for lower-level integrations (e.g. when you've
already extracted the stdout bytes yourself).
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from ..models import StreamEvent

_TOOL_EMOJI = {
    "Bash": "🔧",
    "Read": "📖",
    "Write": "📝",
    "Edit": "✏️ ",
    "MultiEdit": "✏️ ",
    "NotebookEdit": "📓",
    "Grep": "🔍",
    "Glob": "🔎",
    "WebFetch": "🌐",
    "WebSearch": "🌐",
    "Task": "🤖",
    "Agent": "🤖",
    "TodoWrite": "📋",
    "ExitPlanMode": "📐",
    "EnterPlanMode": "📐",
}
_DEFAULT_TOOL_EMOJI = "🛠️ "


class ClaudeFormatter:
    """Stateful formatter that turns Claude stream-json into display lines."""

    def __init__(self) -> None:
        self._buf = ""

    def consume(self, event: StreamEvent) -> Iterator[str]:
        """Yield formatted lines for a session stream event.

        Skips non-`output` events and `stderr` output — those are surfaced
        verbatim by the caller (e.g. for progress bars or error messages
        the agent writes to stderr).
        """
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
                result = self._format(line)
                if result:
                    yield result

    def flush(self) -> Iterator[str]:
        if self._buf.strip():
            result = self._format(self._buf)
            self._buf = ""
            if result:
                yield result

    def _format(self, line: str) -> str | None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return line
        indent = "  " if event.get("parent_tool_use_id") else ""
        kind = event.get("type")
        if kind == "system":
            body = _fmt_system(event)
        elif kind == "assistant":
            body = _fmt_assistant(event)
        elif kind == "result":
            body = _fmt_result(event)
        else:
            body = None
        if body is None:
            return None
        return "\n".join(indent + line for line in body.splitlines())


def _fmt_system(event: dict) -> str | None:
    subtype = event.get("subtype")
    if subtype == "init":
        model = event.get("model", "?")
        tools = len(event.get("tools") or [])
        mcps = [s.get("name") for s in (event.get("mcp_servers") or [])]
        mcp_str = f", mcp=[{', '.join(mcps)}]" if mcps else ""
        return f"⚙️  Session init · model={model}, tools={tools}{mcp_str}"
    if subtype == "task_started":
        desc = event.get("description") or "(no description)"
        return f"🚀 Task spawned · {desc}"
    return None


def _fmt_assistant(event: dict) -> str | None:
    blocks = (event.get("message") or {}).get("content") or []
    parts: list[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype == "thinking":
            text = (block.get("thinking") or "").strip()
            if text:
                parts.append(f"💭 {_one_line(text, 200)}")
        elif btype == "text":
            text = (block.get("text") or "").strip()
            if text:
                parts.append(f"✉️  {text}")
        elif btype == "tool_use":
            parts.append(_fmt_tool_use(block))
    return "\n".join(parts) if parts else None


def _fmt_tool_use(block: dict) -> str:
    name = block.get("name") or "?"
    emoji = _TOOL_EMOJI.get(name, _DEFAULT_TOOL_EMOJI)
    detail = _tool_detail(name, block.get("input") or {})
    return f"{emoji} {name} · {detail}" if detail else f"{emoji} {name}"


def _tool_detail(name: str, args: dict) -> str:
    if name == "Bash":
        return _one_line(str(args.get("command") or "").strip(), 140)
    if name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        return str(args.get("file_path") or "") or "(no path)"
    if name in ("Grep", "Glob"):
        pattern = args.get("pattern") or ""
        path = args.get("path") or ""
        return f"{pattern}" + (f" in {path}" if path else "")
    if name in ("WebFetch", "WebSearch"):
        return str(args.get("url") or args.get("query") or "")
    if name in ("Task", "Agent"):
        return _one_line(str(args.get("description") or args.get("prompt") or ""), 140)
    if name == "TodoWrite":
        todos = args.get("todos") or []
        return f"{len(todos)} todo(s)"
    for key in ("description", "name", "query", "prompt", "path"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return _one_line(val, 140)
    return ""


def _fmt_result(event: dict) -> str:
    subtype = event.get("subtype") or ""
    marker = "✨" if subtype == "success" else "⚠️ "
    parts = [f"agent {(event.get('duration_ms') or 0) / 1000:.1f}s"]
    turns = event.get("num_turns")
    if turns:
        parts.append(f"{turns} turns")
    cost = event.get("total_cost_usd")
    if cost is not None:
        parts.append(f"tokens ${cost:.4f}")
    return f"{marker} Done · {', '.join(parts)}"


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
