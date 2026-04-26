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

import pytest

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


# --- Per-instrumentor failure isolation ---
#
# `_instrument_libraries` wraps each auto-instrumentor in its own try/except
# so a missing optional dep can't take the web process down. The Django one
# was covered in #148; these add the same shape for psycopg and requests.
# Without them, a regression that swallowed the warning OR took the whole
# block down on the second/third instrumentor would slip past CI.


@pytest.fixture
def _otel_with_noop_exporters(monkeypatch):
    """Reset OTel state and swap in no-op exporters so the BatchProcessors
    don't try to flush to api.honeycomb.io with a fake key (avoids the
    `Failed to export ... 401, reason: Unauthorized` log noise)."""

    class _NoopExporter:
        def export(self, _records):
            return 0

        def shutdown(self):
            return None

        def force_flush(self, _timeout_millis=30000):
            return True

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        lambda **_kw: _NoopExporter(),
    )
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter",
        lambda **_kw: _NoopExporter(),
    )
    monkeypatch.setattr(observability, "_otel_initialized", False)
    monkeypatch.setenv("HONEYCOMB_API_KEY", "fake-key-instrumentor-tests")
    yield


def test_instrument_libraries_warns_on_psycopg_failure(
    monkeypatch, mocker, _otel_with_noop_exporters
):
    """If psycopg's instrumentor raises (e.g. import-error in a stripped
    deployment), init_otel must log a warning and continue — not crash
    the web process before Django serves its first request."""
    fake_psycopg = mocker.MagicMock()
    fake_psycopg.return_value.instrument.side_effect = RuntimeError("psycopg boom")
    mocker.patch("opentelemetry.instrumentation.psycopg.PsycopgInstrumentor", fake_psycopg)
    warn = mocker.patch.object(observability.logger, "warning")

    observability.init_otel(service_name="aod-test-psycopg-warn")

    assert observability._otel_initialized is True
    failing = [c for c in warn.call_args_list if "psycopg instrumentation failed" in str(c)]
    assert failing, "expected a warning for the psycopg instrumentation failure"


def test_instrument_libraries_warns_on_requests_failure(
    monkeypatch, mocker, _otel_with_noop_exporters
):
    """If requests' instrumentor raises, init_otel must log a warning
    and continue. requests is an optional dep — without this test, a
    refactor that took down the whole try/except chain on the third
    instrumentor would silently break web startup."""
    fake_requests = mocker.MagicMock()
    fake_requests.return_value.instrument.side_effect = RuntimeError("requests boom")
    mocker.patch("opentelemetry.instrumentation.requests.RequestsInstrumentor", fake_requests)
    warn = mocker.patch.object(observability.logger, "warning")

    observability.init_otel(service_name="aod-test-requests-warn")

    assert observability._otel_initialized is True
    failing = [c for c in warn.call_args_list if "requests instrumentation failed" in str(c)]
    assert failing, "expected a warning for the requests instrumentation failure"
