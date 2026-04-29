"""Worker task tracing: outer span + W3C trace propagation across the queue.

Every Procrastinate task runs in the worker process where psycopg
auto-instrumentation would otherwise emit each SQL query as a root span.
`@traced_task` wraps the task body in a CONSUMER-kind span so those queries
become children — and pulls a W3C traceparent out of `_otel_carrier` (when the
enqueue site injected one) so the worker span attaches to the originating
request's trace instead of starting a fresh root.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from opentelemetry.trace import SpanKind, Status, StatusCode

from agent_on_demand.observability import get_tracer

_KW_TO_ATTR = {
    "session_id": "aod.session_id",
    "user_id": "aod.user_id",
    "turn_id": "aod.turn_id",
    "handle": "aod.handle",
}


def traced_task(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap a Procrastinate task body in a CONSUMER span.

    The wrapper accepts an optional `_otel_carrier` kwarg (W3C traceparent
    dict from `inject_carrier()`). When present, the new span is attached to
    that remote context so the worker trace hangs off the request that
    enqueued it. When absent, the span is a fresh root.

    `_otel_carrier` is stripped before delegation — wrapped functions never
    see it.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            carrier = kwargs.pop("_otel_carrier", None)
            tracer = get_tracer()

            span_kwargs: dict[str, Any] = {"kind": SpanKind.CONSUMER}
            if carrier:
                from opentelemetry.trace.propagation.tracecontext import (
                    TraceContextTextMapPropagator,
                )

                span_kwargs["context"] = TraceContextTextMapPropagator().extract(carrier)

            with tracer.start_as_current_span(f"task {name}", **span_kwargs) as span:
                span.set_attribute("procrastinate.task.name", name)
                for kwarg_name, attr_name in _KW_TO_ATTR.items():
                    if kwarg_name in kwargs and kwargs[kwarg_name] is not None:
                        span.set_attribute(attr_name, kwargs[kwarg_name])
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR))
                    raise

        return wrapper

    return decorator


def inject_carrier() -> dict[str, str]:
    """Snapshot the current OTel context into a W3C traceparent dict.

    Returns `{}` when there's no active span — the worker side treats an
    empty carrier as "no parent" and starts a root span.
    """
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier
