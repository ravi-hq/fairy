"""Per-runtime stream-json → OpenTelemetry bridge.

The runtime CLIs we shell out to (`claude --output-format stream-json`,
`gemini --output-format stream-json`, `codex exec --json`) emit one JSON
event per line on stdout. PR #284 already records a `runtime.output`
span event per chunk so the trace timeline shows when output arrived;
this module goes one step further: it line-buffers stdout, parses each
event, and turns the structured shape into trace data:

  - One **child span** per `tool_use` block, started when the assistant
    message announces it and ended when the matching `user` message
    carries the `tool_result`. The duration is the wall-clock the tool
    took to execute, which is exactly the "dead time" the operator was
    seeing on the original trace.
  - **Span events** for non-durational signals (assistant text/thinking,
    system init, the final `result` envelope) so they show up as
    annotations on the parent span timeline.

Adapters live in this same module — only `claude` is wired in for now;
adding `codex`/`gemini` is just another entry in `_ADAPTERS`. The line
buffering, span lifecycle, and orphan-close logic are runtime-agnostic
and live entirely in `RuntimeTraceEmitter`.

The reference for the Claude event taxonomy is the CLI pretty-printer
in `clients/python/src/aod/pretty/claude.py` — keep the two in sync if
the upstream stream-json shape changes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.trace import Span, Tracer

logger = logging.getLogger(__name__)

# A trace action is a tuple the emitter knows how to apply:
#   ("event", name: str, attrs: dict)
#   ("tool_start", tool_id: str, span_name: str, attrs: dict)
#   ("tool_end",   tool_id: str, attrs: dict)
TraceAction = tuple[Any, ...]
Adapter = Callable[[dict], Iterable[TraceAction]]


class RuntimeTraceEmitter:
    """Stateful per-turn bridge between runtime stdout and OTel spans.

    Construction is cheap and IO-free. `feed(stream, data)` is called once
    per chunk by `LogChunkSink.drain()`; only stdout is parsed (stream-json
    is stdout-only on every runtime we support). `finish()` runs after the
    drain loop and closes any tool spans the runtime never matched up
    (turn timed out mid-call, runtime crashed, etc.).
    """

    def __init__(self, parent_span: Span, runtime: str, tracer: Tracer):
        self._parent = parent_span
        self._tracer = tracer
        self._adapter = _ADAPTERS.get(runtime)
        self._line_buf = bytearray()
        self._open_tool_spans: dict[str, Span] = {}

    def feed(self, stream: str, data: bytes) -> None:
        if self._adapter is None or stream != "stdout":
            return
        self._line_buf.extend(data)
        while True:
            idx = self._line_buf.find(b"\n")
            if idx < 0:
                break
            line = bytes(self._line_buf[:idx])
            del self._line_buf[: idx + 1]
            self._handle_line(line)

    def finish(self) -> None:
        """Close any tool spans that never saw a matching tool_result.

        Without this, an aborted turn leaves spans that never end and
        never export, which Honeycomb shows as "incomplete" traces.
        """
        for span in self._open_tool_spans.values():
            span.set_attribute("aod.tool_status", "abandoned")
            span.end()
        self._open_tool_spans.clear()

    def _handle_line(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(obj, dict):
            return
        try:
            actions = list(self._adapter(obj)) if self._adapter else []
        except Exception:
            # An adapter bug must not crash the drain loop — that would
            # abort the turn. Log and move on.
            logger.exception("runtime trace adapter raised on event")
            return
        for action in actions:
            self._apply(action)

    def _apply(self, action: TraceAction) -> None:
        kind = action[0]
        if kind == "event":
            _, name, attrs = action
            self._parent.add_event(name, attributes=_clean_attrs(attrs))
        elif kind == "tool_start":
            _, tool_id, span_name, attrs = action
            if not tool_id or tool_id in self._open_tool_spans:
                return
            span = self._tracer.start_span(span_name, attributes=_clean_attrs(attrs))
            self._open_tool_spans[tool_id] = span
        elif kind == "tool_end":
            _, tool_id, attrs = action
            if tool_id not in self._open_tool_spans:
                return
            span = self._open_tool_spans.pop(tool_id)
            for k, v in _clean_attrs(attrs).items():
                span.set_attribute(k, v)
            span.end()


def _clean_attrs(attrs: dict | None) -> dict:
    """Drop None values — OTel attribute setters reject them."""
    if not attrs:
        return {}
    return {k: v for k, v in attrs.items() if v is not None}


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def _claude_adapter(obj: dict) -> Iterable[TraceAction]:
    """Map one Claude stream-json event to zero or more trace actions.

    Mirrors the event taxonomy in `clients/python/src/aod/pretty/claude.py`:
      - `system` (init / task_started / …) → span event
      - `assistant.message.content[]`:
          - `text` → assistant_text event (length only)
          - `thinking` → thinking event (length only)
          - `tool_use` → start a child span keyed by tool_use_id
      - `user.message.content[]` `tool_result` → end the matching child span
      - `result` (terminal) → span event with usage + cost attrs
    """
    kind = obj.get("type")
    if kind == "system":
        subtype = str(obj.get("subtype") or "unknown")
        attrs: dict[str, Any] = {"aod.subtype": subtype}
        if subtype == "init":
            attrs["aod.model"] = obj.get("model")
            tools = obj.get("tools") or []
            attrs["aod.tools_count"] = len(tools)
        yield ("event", f"claude.system.{subtype}", attrs)
        return

    if kind == "assistant":
        message = obj.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text") or ""
                yield ("event", "claude.assistant_text", {"aod.length": len(text)})
            elif btype == "thinking":
                text = block.get("thinking") or ""
                yield ("event", "claude.thinking", {"aod.length": len(text)})
            elif btype == "tool_use":
                tool_id = str(block.get("id") or "")
                if not tool_id:
                    continue
                tool_name = str(block.get("name") or "?")
                yield (
                    "tool_start",
                    tool_id,
                    "runtime.tool_use",
                    {"aod.tool_name": tool_name, "aod.tool_id": tool_id},
                )
        return

    if kind == "user":
        message = obj.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            tool_id = str(block.get("tool_use_id") or "")
            if not tool_id:
                continue
            yield (
                "tool_end",
                tool_id,
                {"aod.is_error": bool(block.get("is_error", False))},
            )
        return

    if kind == "result":
        attrs = {"aod.subtype": str(obj.get("subtype") or "")}
        for key in ("duration_ms", "num_turns", "total_cost_usd"):
            value = obj.get(key)
            if value is not None:
                attrs[f"aod.{key}"] = value
        yield ("event", "claude.result", attrs)
        return


_ADAPTERS: dict[str, Adapter] = {
    "claude": _claude_adapter,
}
