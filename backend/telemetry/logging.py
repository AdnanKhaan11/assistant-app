"""Structured logging with automatic trace correlation.

Every log line gets `trace_id` and `span_id` when written inside a span, so in Grafana you can jump
from a log line to the exact trace that produced it.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from opentelemetry import trace

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


def _trace_fields() -> dict[str, str]:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {"trace_id": format(ctx.trace_id, "032x"), "span_id": format(ctx.span_id, "016x")}


class JsonFormatter(logging.Formatter):
    """One JSON object per line - easy for Loki / ELK / CloudWatch to parse."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_trace_fields(),
        }
        # anything passed via logger.info("...", extra={"ticket_id": "T-1"}) is included
        payload.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = _trace_fields()
        return f"{base} [trace_id={fields['trace_id'][:8]}]" if fields else base


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Idempotent: replaces handlers on the root logger."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if fmt == "json" else TextFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("opentelemetry").setLevel(logging.WARNING)  # exporter retry noise
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs at INFO
