"""sprites_patch monkey-patches websockets.connect to use a 0.5s
close_timeout instead of the default 10s. The patch fires at import
time and persists for the process.

Tests pin two contracts:
  - Calling the patched connect with no close_timeout adds 0.5
  - Calling it with an explicit close_timeout preserves the caller's value

A regression that flipped `setdefault` to `[]=` would silently override
explicit timeouts; a regression that dropped the patch entirely would
make every sprite.command roundtrip 22x slower in production.
"""

from __future__ import annotations

# Importing the module triggers the monkey-patch. Subsequent imports are
# no-ops because Python caches modules.
import agent_on_demand.sprites_patch  # noqa: F401


def test_patched_connect_sets_default_close_timeout(mocker):
    """When the caller passes no close_timeout kwarg, the patch fills in 0.5."""
    captured: dict = {}

    def fake_connect(*args, **kwargs):
        captured.update(kwargs)
        return mocker.MagicMock()

    # Replace the underlying websockets.connect with our spy. The patched
    # function calls _orig_connect, which we capture here.
    # Patch the captured original-connect reference inside the patch
    # module (not the module-attribute on websockets, which is bound
    # only at sprites_patch import time).
    mocker.patch("agent_on_demand.sprites_patch._orig_connect", side_effect=fake_connect)

    # The patched connect is what's currently bound on _ws.websockets.connect
    # via sprites_patch's import-time mutation. Because we just replaced
    # connect with our spy, we have to re-import the patch module... but
    # because Python caches it, we can't. Easier: invoke the patched
    # function directly via the closure.
    from agent_on_demand.sprites_patch import _patched_connect

    _patched_connect("ws://example/")

    assert captured.get("close_timeout") == 0.5


def test_patched_connect_preserves_explicit_close_timeout(mocker):
    """`setdefault` semantics: when the caller passes close_timeout=N, the
    patch must NOT overwrite N — otherwise tests that need a longer
    timeout (or future call-sites that explicitly opt out) get clobbered."""
    captured: dict = {}

    def fake_connect(*args, **kwargs):
        captured.update(kwargs)
        return mocker.MagicMock()

    # Patch the captured original-connect reference inside the patch
    # module (not the module-attribute on websockets, which is bound
    # only at sprites_patch import time).
    mocker.patch("agent_on_demand.sprites_patch._orig_connect", side_effect=fake_connect)

    from agent_on_demand.sprites_patch import _patched_connect

    _patched_connect("ws://example/", close_timeout=30)

    assert captured.get("close_timeout") == 30
