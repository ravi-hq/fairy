"""Monkeypatch sprites-py's websocket connect to use a short close_timeout.

The default close_timeout (10s) makes every `sprite.command(...).run()` /
`.output()` / `.combined_output()` block for the full timeout on shutdown.
Lowering it to 0.5s yields a ~22x speedup with no call-site changes. Import
this module once at app startup before any `sprite.command(...)` call.
"""

import sprites.websocket as _ws

_orig_connect = _ws.websockets.connect


def _patched_connect(*args, **kwargs):
    kwargs.setdefault("close_timeout", 0.5)
    return _orig_connect(*args, **kwargs)


_ws.websockets.connect = _patched_connect  # type: ignore[misc,assignment]
