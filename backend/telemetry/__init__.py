"""Reusable OpenTelemetry package: traces + metrics + logs.

Copy this whole folder into any Python service and call `init_telemetry()` once at startup.
It has NO imports from the rest of the application.
"""
from .config import TelemetryConfig
from .instrumentation import instrument_fastapi, instrument_httpx
from .logging import configure_logging
from .metrics import get_metrics
from .setup import TelemetryHandle, init_telemetry, shutdown_telemetry
from .tracing import current_trace_id, get_tracer, start_span, traced

__all__ = [
    "TelemetryConfig",
    "TelemetryHandle",
    "configure_logging",
    "current_trace_id",
    "get_metrics",
    "get_tracer",
    "init_telemetry",
    "instrument_fastapi",
    "instrument_httpx",
    "shutdown_telemetry",
    "start_span",
    "traced",
]
