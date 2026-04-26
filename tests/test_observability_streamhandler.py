"""Cover the StreamHandler-when-none branch of init_otel.

The existing tests in `test_observability.py` exercise the
`StreamHandler-already-present` path — pytest configures one by default,
so the `if not any(StreamHandler...)` check finds an existing handler
and skips re-attaching.

The other branch (no existing StreamHandler → attach a fresh one) only
fires on bare-bones environments. Without a test, removing the entire
`if not any` block would be undetectable; that block is critical because
attaching only the OTel `LoggingHandler` to root suppresses Python's
implicit stderr fallback, leaving Render/etc. with zero app log output.
"""

from __future__ import annotations

import logging


from agent_on_demand import observability


def test_init_otel_attaches_streamhandler_when_none_present(monkeypatch, mocker):
    """With HONEYCOMB_API_KEY set and root having no StreamHandler,
    init_otel must attach a fresh StreamHandler so Python's implicit
    stderr fallback isn't suppressed by the OTel-only handler. The
    formatter must use the expected format so Render/etc. log capture
    can parse it.

    Patches `logging.StreamHandler` with a tracking spy and arranges
    the `if not any(...StreamHandler...)` check to be True so we
    deterministically reach the attach branch (pytest's own
    LogCaptureHandler is a StreamHandler subclass, which makes
    fixture-based root-state manipulation fragile)."""
    from opentelemetry.sdk._logs import LoggingHandler

    # Force the "no StreamHandler" branch by short-circuiting the `any(...)`
    # check inside init_otel's module-local namespace. This is the cleanest
    # way to deterministically reach the attach branch regardless of what
    # pytest's logging plugin has done to root.
    monkeypatch.setattr(observability, "_otel_initialized", False)
    monkeypatch.setenv("HONEYCOMB_API_KEY", "fake-key-streamhandler-test")

    # Subclass StreamHandler so isinstance() in observability.py still
    # passes, but we can detect new instances by class identity.
    class _TrackingStreamHandler(logging.StreamHandler):
        instances: list = []

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            type(self).instances.append(self)

    mocker.patch("agent_on_demand.observability.logging.StreamHandler", _TrackingStreamHandler)
    # Also force the `not any(...StreamHandler...)` predicate to True by
    # replacing root.handlers with an empty list for the duration of the call.
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_level = root.level
    root.handlers = []
    try:
        observability.init_otel(service_name="aod-streamhandler-test")
    finally:
        # Pop anything init_otel attached and restore the original handler
        # set — we asserted via the spy, not via root state.
        for h in list(root.handlers):
            if h not in saved:
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        for h in saved:
            if h not in root.handlers:
                root.addHandler(h)
        root.setLevel(saved_level)

    assert observability._otel_initialized is True

    # init_otel constructs exactly one StreamHandler in the attach-fresh
    # branch (the LoggingHandler is a different class, instantiated separately).
    assert _TrackingStreamHandler.instances, (
        "init_otel must instantiate a StreamHandler when root has none — "
        "without it, attaching the OTel LoggingHandler suppresses Python's "
        "implicit stderr fallback and Render captures no app logs."
    )
    h = _TrackingStreamHandler.instances[0]
    assert not isinstance(h, LoggingHandler)
    assert h.formatter is not None
    assert h.formatter._fmt == "%(levelname)s %(name)s: %(message)s"
    assert h.level == logging.INFO
