"""One-time initialisation of the OpenTelemetry SDK (the three signals).

    traces  -> "what happened to ONE request, step by step"        (Jaeger)
    metrics -> "how is the system doing overall, over time"        (Prometheus -> Grafana)
    logs    -> "what did the code say", linked to traces by id     (Loki)

Pipeline:   your app --OTLP/HTTP--> OpenTelemetry Collector --> Jaeger / Prometheus / Loki
The app only knows about the Collector. Swapping backends (Datadog, Tempo, New Relic...) is a
Collector config change - zero code changes. That is the industry-standard design.
"""
from __future__ import annotations

import logging
import os
import socket

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from .config import TelemetryConfig
from .logging import configure_logging

logger = logging.getLogger(__name__)
_handle: "TelemetryHandle | None" = None


class TelemetryHandle:
    """Keeps the providers so we can flush buffered data on shutdown."""

    def __init__(self, tracer_provider=None, meter_provider=None, logger_provider=None) -> None:
        self.tracer_provider = tracer_provider
        self.meter_provider = meter_provider
        self.logger_provider = logger_provider

    def shutdown(self) -> None:
        # Batch processors buffer data; without flush the last seconds of telemetry are lost.
        for provider in (self.tracer_provider, self.meter_provider, self.logger_provider):
            if provider is None:
                continue
            try:
                provider.shutdown()
            except Exception:  # never let telemetry break shutdown
                logger.exception("telemetry provider shutdown failed")


def init_telemetry(config: TelemetryConfig | None = None) -> TelemetryHandle:
    """Call once at process start. Safe to call twice (second call is a no-op)."""
    global _handle
    if _handle is not None:
        return _handle

    config = config or TelemetryConfig.from_env()
    configure_logging(config.log_level, config.log_format)

    if not config.enabled or config.exporter == "none":
        logger.info("telemetry disabled")
        _handle = TelemetryHandle()
        return _handle

    # Resource = "who is sending this data". Attached to every span, metric and log.
    resource = Resource.create(
        {
            "service.name": config.service_name,
            "service.version": config.service_version,
            "deployment.environment": config.environment,
            "service.instance.id": f"{socket.gethostname()}-{os.getpid()}",
        }
    )

    # ---- traces ------------------------------------------------------------------------------
    # ParentBased: if an upstream service already decided to sample, follow it (keeps traces whole).
    tracer_provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(config.trace_sample_ratio))
    )
    tracer_provider.add_span_processor(BatchSpanProcessor(_span_exporter(config)))
    trace.set_tracer_provider(tracer_provider)

    # ---- metrics -----------------------------------------------------------------------------
    reader = PeriodicExportingMetricReader(
        _metric_exporter(config), export_interval_millis=config.metric_export_interval_ms
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    # ---- logs --------------------------------------------------------------------------------
    logger_provider = None
    if config.exporter == "otlp":  # in console mode our JSON stdout logs are enough
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{config.otlp_endpoint}/v1/logs"))
        )
        set_logger_provider(logger_provider)
        # Bridge: every stdlib `logging` call is also forwarded to OpenTelemetry.
        otel_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        # Never forward the exporter's OWN warnings through the exporter (feedback loop when the collector is down).
        otel_handler.addFilter(lambda record: not record.name.startswith("opentelemetry"))
        logging.getLogger().addHandler(otel_handler)

    _handle = TelemetryHandle(tracer_provider, meter_provider, logger_provider)
    logger.info(
        "telemetry initialised",
        extra={"exporter": config.exporter, "endpoint": config.otlp_endpoint, "sample_ratio": config.trace_sample_ratio},
    )
    return _handle


def shutdown_telemetry() -> None:
    global _handle
    if _handle is not None:
        _handle.shutdown()
        _handle = None


def _span_exporter(config: TelemetryConfig):
    if config.exporter == "console":
        return ConsoleSpanExporter()
    return OTLPSpanExporter(endpoint=f"{config.otlp_endpoint}/v1/traces")


def _metric_exporter(config: TelemetryConfig):
    if config.exporter == "console":
        return ConsoleMetricExporter()
    return OTLPMetricExporter(endpoint=f"{config.otlp_endpoint}/v1/metrics")
