"""Tracing helpers: spans as context managers and decorators.

Vocabulary
    trace = the whole journey of one request          span = one step inside it (has start/end time)
    attributes = key/value facts on a span            events = timestamped notes inside a span

Rules of thumb
    * Span names are LOW cardinality ("service.classify"), never "classify ticket T-1234".
    * Put the variable part (ticket id, model) in attributes instead.
    * Never put secrets or raw user text in attributes.
"""
from __future__ import annotations

import functools
import inspect
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

TRACER_NAME = "assistant-app"


def get_tracer(name: str = TRACER_NAME) -> trace.Tracer:
    return trace.get_tracer(name)


@contextmanager
def start_span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
) -> Iterator[Span]:
    """`with start_span("tool.network_check", {"tool.risk": "diagnostic"}) as span: ...`

    On exception the error is recorded on the span (shows red in Jaeger) and re-raised.
    """
    with get_tracer().start_as_current_span(
        name, kind=kind, attributes=dict(attributes or {}), record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            raise


def traced(name: str | None = None, attributes: Mapping[str, Any] | None = None) -> Callable:
    """Decorator version. Works on both `def` and `async def`."""

    def decorator(func: Callable) -> Callable:
        span_name = name or f"{func.__module__.rsplit('.', 1)[-1]}.{func.__qualname__}"

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with start_span(span_name, attributes):
                    return await func(*args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with start_span(span_name, attributes):
                return func(*args, **kwargs)

        return sync_wrapper

    return decorator


def current_trace_id() -> str | None:
    """Hex trace id of the active span - return it to clients so support can find the trace."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
