#!/usr/bin/env python3
"""
project_structure.py  -  scaffold generator for the IT Support Assistant.

USAGE
    python project_structure.py                 # writes into ./assistant-app
    python project_structure.py my-folder       # writes into ./my-folder
    python project_structure.py --force         # overwrite files that already exist

WHAT YOU GET
    * backend/telemetry/*   COMPLETE   OpenTelemetry traces + metrics + logs (reusable in any app)
    * backend/llm/*         MOSTLY     provider-agnostic LLM layer (OpenAI, Groq, Gemini, Anthropic, Ollama...)
    * backend/tools/*       PARTIAL    tool contract + registry (guards/telemetry complete)
    * everything else       ~30%       skeleton + numbered "YOUR TASKS" for you to complete
    * deploy/*              COMPLETE   OTel Collector + Prometheus + Grafana + Jaeger + Loki (docker compose)

Existing files are skipped unless --force is given, so it is safe to re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

FILES: dict[str, str] = {}

# =============================================================================
# TELEMETRY PACKAGE  (COMPLETE - standalone, copy `backend/telemetry/` into any app)
# =============================================================================
FILES["backend/telemetry/__init__.py"] = r'''"""Reusable OpenTelemetry package: traces + metrics + logs.

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
'''

FILES["backend/telemetry/config.py"] = r'''"""Telemetry configuration - everything comes from environment variables (12-factor).

Standard OpenTelemetry variables are honoured (OTEL_SERVICE_NAME, OTEL_EXPORTER_OTLP_ENDPOINT),
plus a few of our own prefixed with TELEMETRY_.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TelemetryConfig:
    service_name: str = "assistant-app"
    service_version: str = "0.1.0"
    environment: str = "development"

    enabled: bool = True
    # otlp    -> send to an OpenTelemetry Collector (production / docker compose)
    # console -> print spans/metrics to stdout (learning, debugging, no infra needed)
    # none    -> disable exporting completely
    exporter: str = "otlp"
    otlp_endpoint: str = "http://localhost:4318"  # OTLP/HTTP; the SDK appends /v1/traces etc.

    trace_sample_ratio: float = 1.0  # 1.0 = keep every trace. Lower it (e.g. 0.1) under heavy traffic.
    metric_export_interval_ms: int = 10_000

    # Privacy: prompts/answers can contain personal data. Keep OFF unless you really need it.
    capture_content: bool = False

    log_level: str = "INFO"
    log_format: str = "json"  # json | text

    @classmethod
    def from_env(cls) -> "TelemetryConfig":
        return cls(
            service_name=os.getenv("OTEL_SERVICE_NAME", cls.service_name),
            service_version=os.getenv("SERVICE_VERSION", cls.service_version),
            environment=os.getenv("DEPLOYMENT_ENV", cls.environment),
            enabled=_env_bool("TELEMETRY_ENABLED", True),
            exporter=os.getenv("TELEMETRY_EXPORTER", cls.exporter).strip().lower(),
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", cls.otlp_endpoint).rstrip("/"),
            trace_sample_ratio=float(os.getenv("TELEMETRY_SAMPLE_RATIO", cls.trace_sample_ratio)),
            metric_export_interval_ms=int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", cls.metric_export_interval_ms)),
            capture_content=_env_bool("TELEMETRY_CAPTURE_CONTENT", False),
            log_level=os.getenv("LOG_LEVEL", cls.log_level).upper(),
            log_format=os.getenv("LOG_FORMAT", cls.log_format).lower(),
        )
'''

FILES["backend/telemetry/logging.py"] = r'''"""Structured logging with automatic trace correlation.

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
'''

FILES["backend/telemetry/setup.py"] = r'''"""One-time initialisation of the OpenTelemetry SDK (the three signals).

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
'''

FILES["backend/telemetry/tracing.py"] = r'''"""Tracing helpers: spans as context managers and decorators.

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
'''

FILES["backend/telemetry/metrics.py"] = r'''"""All metric instruments in ONE place (so dashboards and code never drift apart).

Metric types used
    Counter        only goes up                    -> requests, tool calls, retries
    Histogram      distribution of values          -> latency, token counts  (gives p50/p95/p99)
    ObservableGauge  value sampled on demand       -> number of active tickets

LLM metrics follow the OpenTelemetry GenAI semantic conventions (gen_ai.*), so any GenAI-aware
dashboard understands them. App-specific ones are prefixed `assistant.`.

CARDINALITY WARNING: attribute values become Prometheus labels. Use small fixed sets (model, tool,
category, status). NEVER use ticket ids, user ids or free text as a metric attribute.
"""
from __future__ import annotations

from typing import Callable, Iterable

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

METER_NAME = "assistant-app"

_LATENCY_BUCKETS = [0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]
_TOKEN_BUCKETS = [1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576]


class AssistantMetrics:
    def __init__(self) -> None:
        meter = metrics.get_meter(METER_NAME)

        # ---- LLM calls (GenAI semantic conventions) ----
        self.llm_duration = meter.create_histogram(
            "gen_ai.client.operation.duration", unit="s",
            description="Duration of one LLM request", explicit_bucket_boundaries_advisory=_LATENCY_BUCKETS,
        )
        self.llm_tokens = meter.create_histogram(
            "gen_ai.client.token.usage", unit="{token}",
            description="Tokens used per LLM request (input and output)", explicit_bucket_boundaries_advisory=_TOKEN_BUCKETS,
        )
        self.llm_retries = meter.create_counter(
            "assistant.llm.retries", unit="{retry}", description="LLM call retries after a transient failure"
        )

        # ---- assistant requests ----
        self.requests = meter.create_counter(
            "assistant.requests", unit="{request}", description="Chat requests handled (outcome = ok|error)"
        )
        self.request_duration = meter.create_histogram(
            "assistant.request.duration", unit="s",
            description="End-to-end time to answer one chat message", explicit_bucket_boundaries_advisory=_LATENCY_BUCKETS,
        )
        self.classifications = meter.create_counter(
            "assistant.classifications", unit="{classification}", description="Tickets classified, by category"
        )

        # ---- tools ----
        self.tool_calls = meter.create_counter(
            "assistant.tool.calls", unit="{call}", description="Tool executions (status = ok|error|blocked)"
        )
        self.tool_duration = meter.create_histogram(
            "assistant.tool.duration", unit="s",
            description="Tool execution time", explicit_bucket_boundaries_advisory=_LATENCY_BUCKETS,
        )
        self.confirmations = meter.create_counter(
            "assistant.confirmations", unit="{decision}", description="Human decisions on risky actions"
        )

    # ------------------------------------------------------------------ recording helpers
    def record_llm_call(
        self, *, provider: str, model: str, duration_s: float,
        input_tokens: int = 0, output_tokens: int = 0, error_type: str | None = None,
    ) -> None:
        attrs = {"gen_ai.operation.name": "chat", "gen_ai.system": provider, "gen_ai.request.model": model}
        if error_type:
            attrs["error.type"] = error_type
        self.llm_duration.record(duration_s, attrs)
        if not error_type:
            self.llm_tokens.record(input_tokens, {**attrs, "gen_ai.token.type": "input"})
            self.llm_tokens.record(output_tokens, {**attrs, "gen_ai.token.type": "output"})

    def record_llm_retry(self, *, provider: str, model: str, reason: str) -> None:
        self.llm_retries.add(1, {"gen_ai.system": provider, "gen_ai.request.model": model, "reason": reason})

    def record_request(self, *, duration_s: float, outcome: str) -> None:
        self.requests.add(1, {"outcome": outcome})
        self.request_duration.record(duration_s, {"outcome": outcome})

    def record_classification(self, category: str) -> None:
        self.classifications.add(1, {"category": category})

    def record_tool_call(self, *, tool: str, risk: str, status: str, duration_s: float) -> None:
        attrs = {"assistant.tool.name": tool, "assistant.tool.risk": risk, "status": status}
        self.tool_calls.add(1, attrs)
        self.tool_duration.record(duration_s, attrs)

    def record_confirmation(self, *, tool: str, approved: bool) -> None:
        self.confirmations.add(1, {"assistant.tool.name": tool, "decision": "approved" if approved else "denied"})

    def register_gauge(self, name: str, callback: Callable[[], int | float], description: str = "", unit: str = "1") -> None:
        """Gauge = a value that goes up AND down, read when the exporter asks. e.g. active tickets."""

        def _observe(_: CallbackOptions) -> Iterable[Observation]:
            yield Observation(callback())

        metrics.get_meter(METER_NAME).create_observable_gauge(name, callbacks=[_observe], description=description, unit=unit)


_instance: AssistantMetrics | None = None


def get_metrics() -> AssistantMetrics:
    """Lazily created singleton (instruments bind to whichever MeterProvider is active)."""
    global _instance
    if _instance is None:
        _instance = AssistantMetrics()
    return _instance
'''

FILES["backend/telemetry/instrumentation.py"] = r'''"""Auto-instrumentation: free spans for the web framework and outgoing HTTP calls.

    FastAPI  -> one server span per request (route, status code, duration) + http.server metrics
    httpx    -> one client span per call to an LLM provider (latency, status)  -> shows where time goes

SECURITY: httpx spans record the URL. Never put API keys in query strings (use headers).
"""
from __future__ import annotations

from typing import Any


def instrument_fastapi(app: Any, excluded_urls: str = "health,metrics") -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    # excluded_urls: comma separated regex - health checks would otherwise create thousands of useless traces
    FastAPIInstrumentor.instrument_app(app, excluded_urls=excluded_urls)


def instrument_httpx() -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()
'''

# =============================================================================
# DEPLOY / OBSERVABILITY STACK  (COMPLETE)
# =============================================================================
FILES["deploy/docker-compose.yml"] = r'''# Observability stack:  docker compose -f deploy/docker-compose.yml up -d
#
#   app --OTLP--> otel-collector --traces--> jaeger      (http://localhost:16686)
#                               \--metrics-> prometheus  (http://localhost:9090) -> grafana (http://localhost:3000)
#                               \--logs----> loki
# Grafana login: admin / admin  (anonymous viewer access is enabled for local dev)
# Run the app itself on your machine (uvicorn) and it reaches the collector on localhost:4318.
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.111.0
    command: ["--config=/etc/otelcol/config.yaml"]
    volumes:
      - ./otel-collector-config.yaml:/etc/otelcol/config.yaml:ro
    ports:
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP  <- the app sends here
    depends_on: [jaeger, loki]

  jaeger:
    image: jaegertracing/all-in-one:1.62.0
    environment:
      COLLECTOR_OTLP_ENABLED: "true"
    ports:
      - "16686:16686"

  prometheus:
    image: prom/prometheus:v2.54.1
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=15d
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./alerts.yml:/etc/prometheus/alerts.yml:ro
    ports:
      - "9090:9090"

  loki:
    image: grafana/loki:3.2.0
    ports:
      - "3100:3100"

  grafana:
    image: grafana/grafana:11.2.0
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/var/lib/grafana/dashboards:ro
    ports:
      - "3000:3000"
    depends_on: [prometheus, jaeger, loki]
'''

FILES["deploy/otel-collector-config.yaml"] = r'''# The Collector is the single gateway between the app and every backend.
# To switch vendor (Datadog, Grafana Cloud, Tempo, New Relic...) edit ONLY the exporters below.
receivers:
  otlp:
    protocols:
      grpc: { endpoint: 0.0.0.0:4317 }
      http: { endpoint: 0.0.0.0:4318 }

processors:
  memory_limiter:            # protects the collector from OOM under load
    check_interval: 1s
    limit_mib: 256
  batch: {}                  # groups data before sending = fewer, cheaper requests

exporters:
  otlp/jaeger:
    endpoint: jaeger:4317
    tls: { insecure: true }
  prometheus:                # Prometheus scrapes this endpoint (see prometheus.yml)
    endpoint: 0.0.0.0:8889
    resource_to_telemetry_conversion: { enabled: true }   # service.name -> service_name label
  otlphttp/loki:
    endpoint: http://loki:3100/otlp
  debug:
    verbosity: basic         # prints a one-line summary per batch to `docker compose logs otel-collector`

service:
  pipelines:
    traces:  { receivers: [otlp], processors: [memory_limiter, batch], exporters: [otlp/jaeger, debug] }
    metrics: { receivers: [otlp], processors: [memory_limiter, batch], exporters: [prometheus] }
    logs:    { receivers: [otlp], processors: [memory_limiter, batch], exporters: [otlphttp/loki] }
'''

FILES["deploy/prometheus.yml"] = r'''global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - /etc/prometheus/alerts.yml

scrape_configs:
  - job_name: otel-collector
    static_configs:
      - targets: ["otel-collector:8889"]
'''

FILES["deploy/alerts.yml"] = r'''groups:
  - name: assistant-app
    rules:
      - alert: LLMErrorRateHigh
        expr: |
          sum(rate(gen_ai_client_operation_duration_seconds_count{error_type!=""}[5m]))
            / sum(rate(gen_ai_client_operation_duration_seconds_count[5m])) > 0.05
        for: 5m
        labels: { severity: page }
        annotations:
          summary: "More than 5% of LLM calls are failing"

      - alert: AssistantSlowResponses
        expr: histogram_quantile(0.95, sum by (le) (rate(assistant_request_duration_seconds_bucket[5m]))) > 15
        for: 10m
        labels: { severity: warn }
        annotations:
          summary: "p95 time to answer a chat message is above 15s"

      - alert: ToolFailuresHigh
        expr: sum(rate(assistant_tool_calls_total{status="error"}[5m])) > 0.1
        for: 5m
        labels: { severity: warn }
        annotations:
          summary: "Tools are failing repeatedly"
'''

FILES["deploy/grafana/provisioning/datasources/datasources.yml"] = r'''apiVersion: 1
datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
  - name: Jaeger
    uid: jaeger
    type: jaeger
    access: proxy
    url: http://jaeger:16686
  - name: Loki
    uid: loki
    type: loki
    access: proxy
    url: http://loki:3100
'''

FILES["deploy/grafana/provisioning/dashboards/dashboards.yml"] = r'''apiVersion: 1
providers:
  - name: assistant-app
    type: file
    options:
      path: /var/lib/grafana/dashboards
'''

FILES["deploy/grafana/dashboards/assistant-overview.json"] = r'''{
  "title": "IT Support Assistant - Overview",
  "uid": "assistant-overview",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "10s",
  "time": { "from": "now-30m", "to": "now" },
  "panels": [
    { "id": 1, "type": "timeseries", "title": "Chat requests / sec (by outcome)", "gridPos": { "x": 0, "y": 0, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [ { "expr": "sum by (outcome) (rate(assistant_requests_total[5m]))", "legendFormat": "{{outcome}}" } ] },

    { "id": 2, "type": "timeseries", "title": "Answer latency p50 / p95", "gridPos": { "x": 12, "y": 0, "w": 12, "h": 8 },
      "fieldConfig": { "defaults": { "unit": "s" } },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [
        { "expr": "histogram_quantile(0.50, sum by (le) (rate(assistant_request_duration_seconds_bucket[5m])))", "legendFormat": "p50" },
        { "expr": "histogram_quantile(0.95, sum by (le) (rate(assistant_request_duration_seconds_bucket[5m])))", "legendFormat": "p95" } ] },

    { "id": 3, "type": "timeseries", "title": "LLM latency p95 by model", "gridPos": { "x": 0, "y": 8, "w": 12, "h": 8 },
      "fieldConfig": { "defaults": { "unit": "s" } },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [ { "expr": "histogram_quantile(0.95, sum by (le, gen_ai_request_model) (rate(gen_ai_client_operation_duration_seconds_bucket[5m])))", "legendFormat": "{{gen_ai_request_model}}" } ] },

    { "id": 4, "type": "timeseries", "title": "Tokens / sec (input vs output)", "gridPos": { "x": 12, "y": 8, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [ { "expr": "sum by (gen_ai_token_type) (rate(gen_ai_client_token_usage_sum[5m]))", "legendFormat": "{{gen_ai_token_type}}" } ] },

    { "id": 5, "type": "timeseries", "title": "LLM errors / sec (by error type)", "gridPos": { "x": 0, "y": 16, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [ { "expr": "sum by (error_type) (rate(gen_ai_client_operation_duration_seconds_count{error_type!=\"\"}[5m]))", "legendFormat": "{{error_type}}" } ] },

    { "id": 6, "type": "timeseries", "title": "Tool calls / sec (by tool, status)", "gridPos": { "x": 12, "y": 16, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [ { "expr": "sum by (assistant_tool_name, status) (rate(assistant_tool_calls_total[5m]))", "legendFormat": "{{assistant_tool_name}} / {{status}}" } ] },

    { "id": 7, "type": "stat", "title": "Human decisions on risky actions (1h)", "gridPos": { "x": 0, "y": 24, "w": 8, "h": 6 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [ { "expr": "sum by (decision) (increase(assistant_confirmations_total[1h]))", "legendFormat": "{{decision}}" } ] },

    { "id": 8, "type": "piechart", "title": "Tickets by category (1h)", "gridPos": { "x": 8, "y": 24, "w": 8, "h": 6 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [ { "expr": "sum by (category) (increase(assistant_classifications_total[1h]))", "legendFormat": "{{category}}" } ] },

    { "id": 9, "type": "stat", "title": "Active tickets in memory", "gridPos": { "x": 16, "y": 24, "w": 8, "h": 6 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [ { "expr": "max(assistant_active_tickets)" } ] },

    { "id": 10, "type": "logs", "title": "Application logs (Loki)", "gridPos": { "x": 0, "y": 30, "w": 24, "h": 10 },
      "datasource": { "type": "loki", "uid": "loki" },
      "targets": [ { "expr": "{service_name=\"assistant-app\"}" } ] }
  ]
}
'''

FILES["Dockerfile"] = r'''FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY data ./data
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
'''

FILES["requirements.txt"] = r'''fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.7
httpx>=0.27
python-dotenv>=1.0

# --- OpenTelemetry ---
opentelemetry-api>=1.27
opentelemetry-sdk>=1.27
opentelemetry-exporter-otlp-proto-http>=1.27
opentelemetry-instrumentation-fastapi>=0.48b0
opentelemetry-instrumentation-httpx>=0.48b0

# --- dev ---
pytest>=8.0
pytest-asyncio>=0.23
'''

FILES["pyproject.toml"] = r'''[project]
name = "assistant-app"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["backend/tests"]
asyncio_mode = "auto"
'''

FILES[".gitignore"] = r'''.env
.venv/
__pycache__/
*.pyc
data/assistant.db
.pytest_cache/
'''

FILES["Makefile"] = r'''.PHONY: run test obs-up obs-down console

run:            ## run the API with OpenTelemetry -> collector
	uvicorn backend.main:app --reload --port 8000

console:        ## run the API printing telemetry to the terminal (no docker needed)
	TELEMETRY_EXPORTER=console LOG_FORMAT=text uvicorn backend.main:app --port 8000

test:
	pytest -v

obs-up:         ## start collector, jaeger, prometheus, loki, grafana
	docker compose -f deploy/docker-compose.yml up -d

obs-down:
	docker compose -f deploy/docker-compose.yml down
'''

FILES[".env.example"] = r'''# ---------- LLM ----------
# provider: openai | groq | gemini | anthropic | ollama | openrouter | deepseek
LLM_PROVIDER=groq
LLM_MODEL=                      # empty = provider default (see backend/llm/registry.py)
LLM_API_KEY=                    # or use the provider-specific variable below
OPENAI_API_KEY=
GROQ_API_KEY=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
OPENROUTER_API_KEY=
DEEPSEEK_API_KEY=
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=2

# ---------- Assistant ----------
MEMORY_WINDOW_TURNS=10
DB_PATH=data/assistant.db
TICKETS_PATH=data/sample_tickets.json

# ---------- Telemetry ----------
TELEMETRY_ENABLED=true
TELEMETRY_EXPORTER=otlp         # otlp | console | none
OTEL_SERVICE_NAME=assistant-app
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
SERVICE_VERSION=0.1.0
DEPLOYMENT_ENV=development
TELEMETRY_SAMPLE_RATIO=1.0
TELEMETRY_CAPTURE_CONTENT=false  # keep false in production (prompts may contain personal data)
LOG_LEVEL=INFO
LOG_FORMAT=json                 # json | text
'''

# =============================================================================
# LLM LAYER  - one internal format, one adapter per wire protocol
#   OpenAI-compatible adapter  -> OpenAI, Groq, Gemini, Ollama, OpenRouter, DeepSeek (just a base_url)
#   Anthropic adapter          -> Claude (different request/response shape)
#   InstrumentedProvider       -> telemetry decorator (COMPLETE)     RetryingProvider -> retries (COMPLETE)
# =============================================================================
FILES["backend/__init__.py"] = ""
FILES["backend/llm/__init__.py"] = r'''from .base import LLMProvider
from .registry import create_provider
from .types import LLMError, LLMResponse, Message, Usage

__all__ = ["LLMError", "LLMProvider", "LLMResponse", "Message", "Usage", "create_provider"]
'''

FILES["backend/llm/types.py"] = r'''"""The ONE internal message format the whole app speaks. Adapters translate to/from provider formats."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None


class LLMError(Exception):
    """Every provider failure is converted to this, so the rest of the app handles ONE error type."""

    def __init__(
        self, message: str, *, retryable: bool = False, status_code: int | None = None, error_type: str = "llm_error"
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.error_type = error_type  # low-cardinality label used in metrics, e.g. "rate_limited"
'''

FILES["backend/llm/base.py"] = r'''"""Provider interface + shared HTTP error mapping."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from .types import LLMError, LLMResponse, Message


class LLMProvider(ABC):
    provider_name: str
    model: str

    @abstractmethod
    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send a conversation, return the assistant text + token usage. Raise LLMError on failure."""

    async def aclose(self) -> None:  # release HTTP connections
        return None


def map_http_error(status_code: int, body: str) -> LLMError:
    """Turn an HTTP status into a typed, metric-friendly LLMError (same rules for every provider)."""
    snippet = body[:300]
    if status_code == 429:
        return LLMError(f"rate limited: {snippet}", retryable=True, status_code=429, error_type="rate_limited")
    if status_code in (401, 403):
        return LLMError(f"authentication failed: {snippet}", status_code=status_code, error_type="auth_error")
    if status_code == 400:
        return LLMError(f"bad request: {snippet}", status_code=400, error_type="bad_request")
    if status_code >= 500:
        return LLMError(f"provider error: {snippet}", retryable=True, status_code=status_code, error_type="provider_error")
    return LLMError(f"unexpected status {status_code}: {snippet}", status_code=status_code, error_type="http_error")
'''

FILES["backend/llm/openai_compat.py"] = r'''"""Adapter for every OpenAI-style API:  POST {base_url}/chat/completions

Works for OpenAI, Groq, Gemini (OpenAI-compat endpoint), Ollama, OpenRouter, DeepSeek...
Only `base_url`, `api_key` and `model` differ. Pure httpx - no vendor SDK.

Wire format you need
    request : {"model": "...", "messages": [{"role": "...", "content": "..."}],
               "temperature": 0.2, "max_tokens": 1024,
               "response_format": {"type": "json_object"}}          <- only when json_mode=True
    response: {"choices": [{"message": {"content": "..."}, "finish_reason": "stop"}],
               "model": "...",
               "usage": {"prompt_tokens": 12, "completion_tokens": 34}}

YOUR TASKS
  1. Implement _build_payload.
       hint: OpenAI's newest models reject `max_tokens` -> send `max_completion_tokens` when provider_name == "openai".
  2. Implement _parse_response (use .get() defensively; Ollama sometimes omits `usage`).
  3. Implement chat(): POST, then
       - httpx.TimeoutException / httpx.TransportError -> raise LLMError(..., retryable=True, error_type="timeout"/"network_error")
       - status >= 400 -> raise map_http_error(status, text)   (already written in base.py)
       - otherwise return self._parse_response(response.json())
"""
from __future__ import annotations

from typing import Sequence

import httpx

from .base import LLMProvider, map_http_error  # noqa: F401  (you will use map_http_error in chat())
from .types import LLMError, LLMResponse, Message, Usage  # noqa: F401


class OpenAICompatProvider(LLMProvider):
    def __init__(self, *, provider_name: str, base_url: str, api_key: str | None, model: str, timeout: float = 30.0) -> None:
        self.provider_name = provider_name
        self.model = model
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def _build_payload(self, messages: Sequence[Message], temperature: float, max_tokens: int, json_mode: bool) -> dict:
        # TODO (task 1)
        raise NotImplementedError("Task 1: build the request body")

    def _parse_response(self, data: dict) -> LLMResponse:
        # TODO (task 2)
        raise NotImplementedError("Task 2: convert the JSON into LLMResponse")

    async def chat(
        self, messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 1024, json_mode: bool = False
    ) -> LLMResponse:
        payload = self._build_payload(messages, temperature, max_tokens, json_mode)
        # TODO (task 3): POST to "/chat/completions", map errors, parse the response
        raise NotImplementedError("Task 3: send the request")

    async def aclose(self) -> None:
        await self._client.aclose()
'''

FILES["backend/llm/anthropic_provider.py"] = r'''"""Adapter for Anthropic Claude:  POST {base_url}/v1/messages

Differences from OpenAI that you must handle
    * auth headers : "x-api-key": <key>   and   "anthropic-version": "2023-06-01"
    * the system prompt is a TOP-LEVEL field `system`, NOT a message with role "system"
    * `max_tokens` is required
    * no native JSON mode -> when json_mode=True append "Respond with a single valid JSON object only." to `system`

Wire format
    request : {"model": "...", "max_tokens": 1024, "temperature": 0.2,
               "system": "...", "messages": [{"role": "user", "content": "..."}]}
    response: {"content": [{"type": "text", "text": "..."}], "model": "...", "stop_reason": "end_turn",
               "usage": {"input_tokens": 12, "output_tokens": 34}}

YOUR TASKS
  1. _split_system(messages) -> (system_text, non_system_messages)
  2. _build_payload
  3. _parse_response  (join ALL text blocks, ignore other block types)
  4. chat() - same error handling as OpenAICompatProvider (timeouts/network -> retryable LLMError, status>=400 -> map_http_error)
"""
from __future__ import annotations

from typing import Sequence

import httpx

from .base import LLMProvider, map_http_error  # noqa: F401
from .types import LLMError, LLMResponse, Message, Usage  # noqa: F401

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    def __init__(self, *, provider_name: str, base_url: str, api_key: str | None, model: str, timeout: float = 30.0) -> None:
        self.provider_name = provider_name
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key or "", "anthropic-version": ANTHROPIC_VERSION, "Content-Type": "application/json"},
            timeout=timeout,
        )

    @staticmethod
    def _split_system(messages: Sequence[Message]) -> tuple[str, list[Message]]:
        # TODO (task 1)
        raise NotImplementedError

    def _build_payload(self, messages: Sequence[Message], temperature: float, max_tokens: int, json_mode: bool) -> dict:
        # TODO (task 2)
        raise NotImplementedError

    def _parse_response(self, data: dict) -> LLMResponse:
        # TODO (task 3)
        raise NotImplementedError

    async def chat(
        self, messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 1024, json_mode: bool = False
    ) -> LLMResponse:
        # TODO (task 4): POST "/v1/messages"
        raise NotImplementedError

    async def aclose(self) -> None:
        await self._client.aclose()
'''

FILES["backend/llm/instrumented.py"] = r'''"""Telemetry decorator for ANY provider (Decorator pattern).

Adapters stay free of monitoring code; this wrapper adds, for every LLM call:
    * a CLIENT span  "chat <model>"  with gen_ai.* attributes   (visible in Jaeger)
    * latency histogram + token histograms + error type          (visible in Grafana)
"""
from __future__ import annotations

import time
from typing import Sequence

from opentelemetry.trace import SpanKind

from backend.telemetry import get_metrics, start_span

from .base import LLMProvider
from .types import LLMResponse, Message


def _truncate(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


class InstrumentedProvider(LLMProvider):
    def __init__(self, inner: LLMProvider, *, capture_content: bool = False) -> None:
        self._inner = inner
        self._capture_content = capture_content
        self.provider_name = inner.provider_name
        self.model = inner.model

    async def chat(
        self, messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 1024, json_mode: bool = False
    ) -> LLMResponse:
        attributes = {
            "gen_ai.operation.name": "chat",
            "gen_ai.system": self.provider_name,
            "gen_ai.request.model": self.model,
            "gen_ai.request.temperature": temperature,
            "gen_ai.request.max_tokens": max_tokens,
            "assistant.llm.json_mode": json_mode,
        }
        started = time.perf_counter()
        with start_span(f"chat {self.model}", attributes, kind=SpanKind.CLIENT) as span:
            if self._capture_content:  # OFF by default: prompts may contain personal data
                span.add_event("gen_ai.prompt", {"content": _truncate(str(messages[-1].content))})
            try:
                response = await self._inner.chat(
                    messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
                )
            except Exception as exc:
                error_type = getattr(exc, "error_type", type(exc).__name__)
                span.set_attribute("error.type", error_type)
                get_metrics().record_llm_call(
                    provider=self.provider_name, model=self.model,
                    duration_s=time.perf_counter() - started, error_type=error_type,
                )
                raise  # start_span records the exception on the span and re-raises

            span.set_attribute("gen_ai.response.model", response.model)
            span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
            if response.finish_reason:
                span.set_attribute("gen_ai.response.finish_reasons", [response.finish_reason])
            if self._capture_content:
                span.add_event("gen_ai.completion", {"content": _truncate(response.text)})

            get_metrics().record_llm_call(
                provider=self.provider_name, model=self.model, duration_s=time.perf_counter() - started,
                input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
            )
            return response

    async def aclose(self) -> None:
        await self._inner.aclose()
'''

FILES["backend/llm/retry.py"] = r'''"""Retry transient LLM failures (429 / 5xx / timeouts) with exponential backoff + jitter.

Wrapping order matters:  Retrying( Instrumented( adapter ) )
-> every ATTEMPT gets its own span and latency sample, and retries are counted separately.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Sequence

from backend.telemetry import get_metrics

from .base import LLMProvider
from .types import LLMError, LLMResponse, Message

logger = logging.getLogger(__name__)


class RetryingProvider(LLMProvider):
    def __init__(self, inner: LLMProvider, *, max_retries: int = 2, base_delay_s: float = 0.5) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._base_delay_s = base_delay_s
        self.provider_name = inner.provider_name
        self.model = inner.model

    async def chat(
        self, messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 1024, json_mode: bool = False
    ) -> LLMResponse:
        attempt = 0
        while True:
            try:
                return await self._inner.chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode)
            except LLMError as exc:
                if not exc.retryable or attempt >= self._max_retries:
                    raise
                attempt += 1
                delay = self._base_delay_s * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                get_metrics().record_llm_retry(provider=self.provider_name, model=self.model, reason=exc.error_type)
                logger.warning(
                    "llm call failed, retrying",
                    extra={"attempt": attempt, "delay_s": round(delay, 2), "error_type": exc.error_type},
                )
                await asyncio.sleep(delay)

    async def aclose(self) -> None:
        await self._inner.aclose()
'''

FILES["backend/llm/registry.py"] = r'''"""Provider table + factory. Adding a new OpenAI-compatible provider = ONE new line in PROVIDERS."""
from __future__ import annotations

import os
from dataclasses import dataclass

from backend.telemetry import TelemetryConfig

from .anthropic_provider import AnthropicProvider
from .base import LLMProvider
from .instrumented import InstrumentedProvider
from .openai_compat import OpenAICompatProvider
from .retry import RetryingProvider


@dataclass(frozen=True)
class ProviderSpec:
    kind: str            # "openai_compat" | "anthropic"
    base_url: str
    default_model: str
    key_env: str | None  # None = no key needed (local Ollama)


PROVIDERS: dict[str, ProviderSpec] = {
    "openai":     ProviderSpec("openai_compat", "https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    "groq":       ProviderSpec("openai_compat", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "gemini":     ProviderSpec("openai_compat", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash", "GEMINI_API_KEY"),
    "anthropic":  ProviderSpec("anthropic", "https://api.anthropic.com", "claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY"),
    "ollama":     ProviderSpec("openai_compat", "http://localhost:11434/v1", "llama3.1", None),
    "openrouter": ProviderSpec("openai_compat", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini", "OPENROUTER_API_KEY"),
    "deepseek":   ProviderSpec("openai_compat", "https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
}


def create_provider(
    provider: str,
    model: str | None = None,
    api_key: str | None = None,
    *,
    timeout: float = 30.0,
    max_retries: int = 2,
    telemetry: TelemetryConfig | None = None,
) -> LLMProvider:
    """Build the full stack:  Retrying( Instrumented( adapter ) )."""
    spec = PROVIDERS.get(provider.lower())
    if spec is None:
        raise ValueError(f"Unknown LLM provider '{provider}'. Choose one of: {', '.join(PROVIDERS)}")

    key = api_key or (os.getenv(spec.key_env) if spec.key_env else None)
    if spec.key_env and not key:
        raise ValueError(f"Missing API key for '{provider}': set LLM_API_KEY or {spec.key_env}")

    adapter_cls = AnthropicProvider if spec.kind == "anthropic" else OpenAICompatProvider
    adapter = adapter_cls(
        provider_name=provider.lower(), base_url=spec.base_url, api_key=key,
        model=model or spec.default_model, timeout=timeout,
    )
    capture = telemetry.capture_content if telemetry else False
    return RetryingProvider(InstrumentedProvider(adapter, capture_content=capture), max_retries=max_retries)
'''

# =============================================================================
# APP CORE  (~30% skeleton + numbered tasks; guards and telemetry lines are complete)
# =============================================================================
FILES["backend/schemas.py"] = r'''"""Canonical request/response models (Pydantic v2). Boilerplate is given; extend as you need."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Category(str, Enum):
    NETWORK = "network_issue"
    SOFTWARE = "software_issue"
    HARDWARE = "hardware_issue"
    UNKNOWN = "unknown"


class Classification(BaseModel):
    category: Category = Category.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_action: bool = False
    keywords: list[str] = Field(default_factory=list)


class ToolFinding(BaseModel):
    tool: str
    status: Literal["ok", "error"]
    details: str


class PendingAction(BaseModel):
    action_id: str
    ticket_id: str
    tool: str
    description: str


class ChatRequest(BaseModel):
    ticket_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(min_length=1, max_length=4000)


class AssessmentResponse(BaseModel):
    ticket_id: str
    category: Category
    confidence: float
    summary: str
    recommendations: list[str]
    findings: list[ToolFinding] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    answer: str
    trace_id: str | None = None  # quote this to support: it finds the exact trace in Jaeger


class ConfirmRequest(BaseModel):
    approved: bool


class ActionResultResponse(BaseModel):
    action_id: str
    status: Literal["executed", "denied", "failed"]
    message: str
    trace_id: str | None = None
'''

FILES["backend/utils/__init__.py"] = ""

FILES["backend/utils/config.py"] = r'''"""The ONE place that reads application environment variables (telemetry has its own config).

YOUR TASKS
  1. Implement Settings.from_env(): read each variable named in .env.example.
       - call `load_dotenv()` first (python-dotenv)
       - ints/floats must be converted; empty strings (e.g. LLM_MODEL=) must become None
  2. Add a `get_settings()` function cached with functools.lru_cache so the env is read once.
"""
from __future__ import annotations

from dataclasses import dataclass

from dotenv import load_dotenv  # noqa: F401


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "groq"
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    memory_window_turns: int = 10
    db_path: str = "data/assistant.db"
    tickets_path: str = "data/sample_tickets.json"

    @classmethod
    def from_env(cls) -> "Settings":
        # TODO (task 1)
        raise NotImplementedError("Task 1: read environment variables")


def get_settings() -> Settings:
    # TODO (task 2)
    raise NotImplementedError("Task 2: cached accessor")
'''

FILES["backend/utils/json_utils.py"] = r'''"""LLMs often wrap JSON in ```json fences or add chatter. Never trust raw model output.

YOUR TASKS
  1. parse_json_object(text) -> dict
       - strip whitespace and markdown code fences
       - try json.loads; if it fails, extract the first {...} block with a regex and retry
       - raise ValueError if nothing valid is found (callers decide on a fallback)
"""
from __future__ import annotations


def parse_json_object(text: str) -> dict:
    # TODO (task 1)
    raise NotImplementedError("Task 1: robust JSON extraction")
'''

# ------------------------------------------------------------------ services (internal inference steps)
FILES["backend/services/__init__.py"] = ""

FILES["backend/services/classifier.py"] = r'''"""Classifier - labels the issue (network / software / hardware / unknown).

YOUR TASKS
  1. Build messages: system prompt + (optional) recent history + the user message.
  2. Call self._llm.chat(..., temperature=0.0, json_mode=True).
  3. Parse with parse_json_object and validate with Classification(**data).
  4. NEVER crash on bad model output: on ValueError/ValidationError return Classification() (UNKNOWN, 0.0)
     and log a warning. A confused model must degrade to "ask for more details", not a 500 error.
  The telemetry line at the end is already written - keep it.
"""
from __future__ import annotations

import logging

from backend.llm import LLMProvider, Message  # noqa: F401
from backend.schemas import Classification
from backend.telemetry import get_metrics, traced
from backend.utils.json_utils import parse_json_object  # noqa: F401

logger = logging.getLogger(__name__)

CLASSIFIER_SYSTEM_PROMPT = """You are an IT support triage classifier.
Return ONE JSON object and nothing else:
{"category": "network_issue" | "software_issue" | "hardware_issue" | "unknown",
 "confidence": <0.0-1.0>,
 "needs_action": <true if running a diagnostic or fix could help>,
 "keywords": [<up to 5 short phrases>]}
If the evidence is weak, use "unknown" and a low confidence. Do not guess."""


class ClassifierService:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    @traced("service.classify")
    async def classify(self, user_message: str, history: list[Message] | None = None) -> Classification:
        # TODO (tasks 1-4): produce `result: Classification`
        raise NotImplementedError("Implement classification")
        get_metrics().record_classification(result.category.value)  # noqa: F821  (keep this line)
        return result  # noqa: F821
'''

FILES["backend/services/summarizer.py"] = r'''"""Summarizer - compact state summary of the ticket so far (cheap context for later turns).

YOUR TASKS
  1. Build a prompt from the recent conversation (list[Message]) plus tool findings.
  2. Ask for <= 3 sentences: symptoms, what was already tried, current blocker. Use temperature=0.2.
  3. Return plain text (strip it). On LLMError return a safe fallback such as the last user message.
"""
from __future__ import annotations

from backend.llm import LLMProvider, Message  # noqa: F401
from backend.schemas import ToolFinding  # noqa: F401
from backend.telemetry import traced

SUMMARIZER_SYSTEM_PROMPT = """You summarise IT support tickets for the next engineer.
In at most 3 sentences state: the symptoms, what has already been tried, and the current blocker.
Use only facts present in the conversation. Do not invent details."""


class SummarizerService:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    @traced("service.summarize")
    async def summarize(self, history: list[Message], findings: list[ToolFinding]) -> str:
        # TODO (tasks 1-3)
        raise NotImplementedError("Implement summarisation")
'''

FILES["backend/services/advisor.py"] = r'''"""Advisor - turns classification + summary + tool findings into concrete, GROUNDED next steps.

YOUR TASKS
  1. Build the prompt with: category, summary, findings (tool name + details). The guardrail text below is
     already written - keep it in the system prompt.
  2. Ask for JSON {"recommendations": ["...", "..."], "answer": "friendly reply to the user"} (json_mode=True).
  3. Post-check (book ch.9, hallucination mitigation): if there ARE findings but the answer mentions none of the
     tool names, log a warning (or ask the model to revise). If there are NO findings, recommendations must not
     claim a diagnostic was run.
  4. Return (recommendations: list[str], answer: str); on any failure return a safe generic fallback.
"""
from __future__ import annotations

from backend.llm import LLMProvider  # noqa: F401
from backend.schemas import Category, ToolFinding  # noqa: F401
from backend.telemetry import traced

ADVISOR_SYSTEM_PROMPT = """You are an IT support advisor. Rules:
- If evidence is insufficient, say "I don't know based on current data" and ask a clarifying question.
- Never fabricate tool results. Only cite findings you were given.
- For actions that change system state, say they REQUIRE the user's confirmation first.
- When possible, name the tool that supports each recommendation."""


class AdvisorService:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    @traced("service.advise")
    async def advise(
        self, category: Category, summary: str, findings: list[ToolFinding]
    ) -> tuple[list[str], str]:
        # TODO (tasks 1-4)
        raise NotImplementedError("Implement advice generation")
'''

# ------------------------------------------------------------------ tools
FILES["backend/tools/__init__.py"] = ""

FILES["backend/tools/base.py"] = r'''"""Tool contract (book ch.9): name, purpose, inputs, output, errors, side effects, RISK LEVEL."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Literal


class RiskLevel(str, Enum):
    INFO = "info"              # read-only lookups
    DIAGNOSTIC = "diagnostic"  # inspects state, changes nothing
    ACTION = "action"          # changes the world -> ALWAYS needs human confirmation


@dataclass
class ToolResult:
    status: Literal["ok", "error"]
    details: str
    category: str | None = None   # error category: transient_timeout | tool_error | confirmation_required ...
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class Tool(ABC):
    name: str
    description: str
    risk: RiskLevel

    @property
    def requires_confirmation(self) -> bool:
        return self.risk is RiskLevel.ACTION

    @abstractmethod
    async def run(self, **kwargs) -> ToolResult:
        """Do the work. Raise nothing for expected failures - return ToolResult(status="error", ...)."""
'''

FILES["backend/tools/registry.py"] = r'''"""ToolRegistry = the governance layer. The model PROPOSES, this code DECIDES.  (COMPLETE)

Enforced in code, not in prompts:
    * confirmation gate  - ACTION tools never run unless confirmed=True
    * tiered timeouts    - fast lookups fail fast, diagnostics get more time, actions the most
    * uniform errors     - callers always get a ToolResult, never an exception
    * full telemetry     - a span + metrics per execution, including blocked attempts
"""
from __future__ import annotations

import asyncio
import logging
import time

from backend.telemetry import get_metrics, start_span

from .base import RiskLevel, Tool, ToolResult

logger = logging.getLogger(__name__)

TIMEOUTS_S: dict[RiskLevel, float] = {RiskLevel.INFO: 5.0, RiskLevel.DIAGNOSTIC: 15.0, RiskLevel.ACTION: 30.0}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"unknown tool '{name}'") from None

    async def execute(self, name: str, *, confirmed: bool = False, timeout_s: float | None = None, **kwargs) -> ToolResult:
        tool = self.get(name)
        timeout = timeout_s if timeout_s is not None else TIMEOUTS_S[tool.risk]
        started = time.perf_counter()

        with start_span(f"tool.{name}", {"assistant.tool.name": name, "assistant.tool.risk": tool.risk.value}) as span:
            if tool.requires_confirmation and not confirmed:
                result = ToolResult(
                    status="error", category="confirmation_required", retryable=False,
                    details=f"'{name}' changes system state and needs the user's explicit confirmation.",
                )
                metric_status = "blocked"
            else:
                try:
                    result = await asyncio.wait_for(tool.run(**kwargs), timeout=timeout)
                    metric_status = result.status
                except asyncio.TimeoutError:
                    result = ToolResult(status="error", category="transient_timeout", retryable=True,
                                        details=f"'{name}' timed out after {timeout:.0f}s.")
                    metric_status = "error"
                except Exception as exc:  # a tool bug must never crash the conversation
                    logger.exception("tool crashed", extra={"tool": name})
                    result = ToolResult(status="error", category="tool_error", retryable=False,
                                        details=f"'{name}' failed unexpectedly.")
                    span.record_exception(exc)
                    metric_status = "error"

            span.set_attribute("assistant.tool.status", metric_status)
            if result.category:
                span.set_attribute("assistant.tool.error_category", result.category)

        get_metrics().record_tool_call(
            tool=name, risk=tool.risk.value, status=metric_status, duration_s=time.perf_counter() - started
        )
        return result
'''

FILES["backend/tools/issue_lookup.py"] = r'''"""issue_lookup (INFO) - reads a known ticket from data/sample_tickets.json (stand-in for a real ITSM API).

YOUR TASKS
  1. __init__(self, tickets_path): store the path.
  2. run(self, ticket_id: str) -> ToolResult
       - load the JSON file WITHOUT blocking the event loop:  await asyncio.to_thread(self._load)
       - found     -> ToolResult("ok", details=<the ticket's "details" text>)
       - not found -> ToolResult("error", category="not_found", details="No ticket <id>")
"""
from __future__ import annotations

from .base import RiskLevel, Tool, ToolResult


class IssueLookupTool(Tool):
    name = "issue_lookup"
    description = "Look up the stored details of a ticket by id (read-only)."
    risk = RiskLevel.INFO

    def __init__(self, tickets_path: str) -> None:
        # TODO (task 1)
        raise NotImplementedError

    async def run(self, ticket_id: str = "", **_) -> ToolResult:
        # TODO (task 2)
        raise NotImplementedError
'''

FILES["backend/tools/network_check.py"] = r'''"""network_check (DIAGNOSTIC) - simulated diagnostics. Changes nothing.

YOUR TASKS
  1. run(): `await asyncio.sleep(0.3)` to mimic work, then return ToolResult("ok", details=...)
     with fake-but-plausible readings, e.g. "packet loss 7%, latency 180ms, DNS ok".
  2. (extension) add DiskCheckTool / HardwareCheckTool in the same style - the registry needs no changes.
"""
from __future__ import annotations

from .base import RiskLevel, Tool, ToolResult


class NetworkCheckTool(Tool):
    name = "network_check"
    description = "Run a network diagnostic (latency, packet loss, DNS)."
    risk = RiskLevel.DIAGNOSTIC

    async def run(self, **_) -> ToolResult:
        # TODO (task 1)
        raise NotImplementedError
'''

FILES["backend/tools/network_action.py"] = r'''"""network_action (ACTION) - simulated 'restart network service'. Needs confirmation (enforced by ToolRegistry).

YOUR TASKS
  1. Keep a module-level set `_RESTARTED: set[str]` of ticket ids already fixed.
  2. run(self, ticket_id: str = "", **_):
       - IDEMPOTENT (book ch.9): if ticket_id already in _RESTARTED return ok with
         "Network service already restarted for this ticket - nothing to do." (no second restart!)
       - else `await asyncio.sleep(0.5)`, add it to the set, return ok "Network service restarted."
"""
from __future__ import annotations

from .base import RiskLevel, Tool, ToolResult

_RESTARTED: set[str] = set()


class NetworkActionTool(Tool):
    name = "network_action"
    description = "Restart the network service (changes system state)."
    risk = RiskLevel.ACTION

    async def run(self, ticket_id: str = "", **_) -> ToolResult:
        # TODO (task 2)
        raise NotImplementedError
'''

# ------------------------------------------------------------------ memory
FILES["backend/memory/__init__.py"] = ""

FILES["backend/memory/short_term.py"] = r'''"""Short-term memory: the active conversation of EACH ticket, bounded (book ch.9).

Design: keep the last N turns in full + a rolling summary + structured state (pending actions).
Scoped per ticket_id so tickets can never leak into each other.

YOUR TASKS
  1. add(ticket_id, message): append, then trim to the last `window_turns * 2` messages.
  2. history(ticket_id) -> list[Message] (a COPY, never the internal list)
  3. set_summary / get_summary(ticket_id)
  4. set_pending(ticket_id, PendingAction) / pop_pending(action_id) -> PendingAction | None
  5. clear(ticket_id)
The method `active_tickets` below feeds the `assistant.active_tickets` gauge - keep it.
"""
from __future__ import annotations

from collections import defaultdict

from backend.llm import Message
from backend.schemas import PendingAction


class ShortTermMemory:
    def __init__(self, window_turns: int = 10) -> None:
        self._window = window_turns * 2
        self._messages: dict[str, list[Message]] = defaultdict(list)
        self._summaries: dict[str, str] = {}
        self._pending: dict[str, PendingAction] = {}  # action_id -> action

    def add(self, ticket_id: str, message: Message) -> None:
        raise NotImplementedError("Task 1")

    def history(self, ticket_id: str) -> list[Message]:
        raise NotImplementedError("Task 2")

    def set_summary(self, ticket_id: str, summary: str) -> None:
        raise NotImplementedError("Task 3")

    def get_summary(self, ticket_id: str) -> str:
        raise NotImplementedError("Task 3")

    def set_pending(self, action: PendingAction) -> None:
        raise NotImplementedError("Task 4")

    def pop_pending(self, action_id: str) -> PendingAction | None:
        raise NotImplementedError("Task 4")

    def clear(self, ticket_id: str) -> None:
        raise NotImplementedError("Task 5")

    def active_tickets(self) -> int:
        return len(self._messages)
'''

FILES["backend/memory/long_term.py"] = r'''"""Long-term memory: persisted ticket history in SQLite (stdlib only), for future retrieval.

Retrieval policy (book ch.9): metadata filter first (ticket_id / category), recent first.

YOUR TASKS
  1. __init__: create the parent folder, then run SCHEMA (use a short-lived sqlite3 connection).
  2. save_turn(ticket_id, role, content, category=None)
  3. recent(ticket_id, limit=20) -> list[dict]   (oldest first)
  4. delete_ticket(ticket_id)
  IMPORTANT: sqlite3 is blocking. Call these from async code with `await asyncio.to_thread(...)`.
"""
from __future__ import annotations

import sqlite3  # noqa: F401
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    category   TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_turns_ticket ON turns (ticket_id, id);
"""


class LongTermMemory:
    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        # TODO (task 1)
        raise NotImplementedError

    def save_turn(self, ticket_id: str, role: str, content: str, category: str | None = None) -> None:
        raise NotImplementedError("Task 2")

    def recent(self, ticket_id: str, limit: int = 20) -> list[dict]:
        raise NotImplementedError("Task 3")

    def delete_ticket(self, ticket_id: str) -> None:
        raise NotImplementedError("Task 4")
'''

# ------------------------------------------------------------------ orchestrator
FILES["backend/assistant.py"] = r'''"""AssistantService - the policy of the app. main.py stays thin; THIS file decides what happens.

Assessment flow (read-only, safe):
    classify -> (tools by category: decision tree) -> summarize -> advise
Resolution flow (changes state):
    needs_action -> store PendingAction -> return it -> user confirms -> confirm_action() -> run tool

YOUR TASKS in handle_message (follow the numbered comments):
  1. Save the user message to short-term memory; read the history.
  2. classification = await self._classifier.classify(...)
  3. findings = await self._run_tool_path(...)
  4. summary = await self._summarizer.summarize(...) ; store it
  5. recommendations, answer = await self._advisor.advise(...)
  6. If classification.needs_action AND category is NETWORK: create a PendingAction (uuid4 hex id), store it.
     Do NOT execute anything here. Execution only happens in confirm_action().
  7. Save the assistant answer (short + long term) and build the AssessmentResponse.
YOUR TASKS in _run_tool_path (decision tree, book ch.9): NETWORK -> network_check; others -> [] for now.
YOUR TASKS in confirm_action: pop the pending action; unknown id -> raise KeyError (main.py turns it into 404);
  approved -> registry.execute("network_action", confirmed=True, ticket_id=...); denied -> no execution.

Telemetry already wired for you: a root span, ticket id as span ATTRIBUTE (fine for traces, forbidden for
metrics), request metrics with outcome, and a confirmation counter.
"""
from __future__ import annotations

import logging
import time
import uuid  # noqa: F401

from backend.llm import Message  # noqa: F401
from backend.memory.long_term import LongTermMemory
from backend.memory.short_term import ShortTermMemory
from backend.schemas import (  # noqa: F401
    ActionResultResponse, AssessmentResponse, Category, ChatRequest, PendingAction, ToolFinding,
)
from backend.services.advisor import AdvisorService
from backend.services.classifier import ClassifierService
from backend.services.summarizer import SummarizerService
from backend.telemetry import current_trace_id, get_metrics, start_span
from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AssistantService:
    def __init__(
        self,
        *,
        classifier: ClassifierService,
        summarizer: SummarizerService,
        advisor: AdvisorService,
        tools: ToolRegistry,
        short_term: ShortTermMemory,
        long_term: LongTermMemory,
    ) -> None:
        self._classifier = classifier
        self._summarizer = summarizer
        self._advisor = advisor
        self._tools = tools
        self._short = short_term
        self._long = long_term

    async def handle_message(self, req: ChatRequest) -> AssessmentResponse:
        started = time.perf_counter()
        outcome = "ok"
        with start_span("assistant.handle_message", {"ticket.id": req.ticket_id}) as span:
            try:
                # TODO steps 1-7 (see module docstring)
                response: AssessmentResponse = ...  # type: ignore[assignment]
                raise NotImplementedError("Implement handle_message")
                span.set_attribute("assistant.category", response.category.value)
                return response
            except Exception:
                outcome = "error"
                raise
            finally:
                get_metrics().record_request(duration_s=time.perf_counter() - started, outcome=outcome)

    async def _run_tool_path(self, category: Category, ticket_id: str) -> list[ToolFinding]:
        # TODO: decision tree. Convert each ToolResult into a ToolFinding(tool=..., status=..., details=...)
        raise NotImplementedError

    async def confirm_action(self, action_id: str, approved: bool) -> ActionResultResponse:
        with start_span("assistant.confirm_action", {"assistant.approved": approved}):
            # TODO: pop pending (KeyError if missing) -> record_confirmation -> execute or deny
            # keep: get_metrics().record_confirmation(tool=action.tool, approved=approved)
            raise NotImplementedError("Implement confirm_action")
'''

# ------------------------------------------------------------------ API
FILES["backend/main.py"] = r'''"""FastAPI entry point: transport + wiring ONLY. Keep it thin.

Run:  uvicorn backend.main:app --reload --port 8000        (or `make run`)

Already done for you: telemetry start/stop, FastAPI + httpx auto-instrumentation, /health,
X-Trace-Id response header, and the application wiring (dependency graph).

YOUR TASKS
  1. POST   /api/chat                       -> await assistant.handle_message(req)
  2. POST   /api/actions/{action_id}/confirm -> await assistant.confirm_action(...); KeyError -> HTTP 404
  3. GET    /api/tickets/{ticket_id}/history -> long-term memory rows  (await asyncio.to_thread(...))
  4. DELETE /api/tickets/{ticket_id}          -> clear short-term + long-term memory
  5. Add a global exception handler for `LLMError` -> HTTP 502 with a friendly JSON body (no stack traces!)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from backend.assistant import AssistantService
from backend.llm import create_provider
from backend.memory.long_term import LongTermMemory
from backend.memory.short_term import ShortTermMemory
from backend.schemas import ActionResultResponse, AssessmentResponse, ChatRequest, ConfirmRequest  # noqa: F401
from backend.services.advisor import AdvisorService
from backend.services.classifier import ClassifierService
from backend.services.summarizer import SummarizerService
from backend.telemetry import (
    TelemetryConfig, current_trace_id, get_metrics, init_telemetry, instrument_fastapi, instrument_httpx,
    shutdown_telemetry,
)
from backend.tools.issue_lookup import IssueLookupTool
from backend.tools.network_action import NetworkActionTool
from backend.tools.network_check import NetworkCheckTool
from backend.tools.registry import ToolRegistry
from backend.utils.config import get_settings

logger = logging.getLogger(__name__)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def build_assistant(settings, telemetry_config: TelemetryConfig) -> tuple[AssistantService, ShortTermMemory, object]:
    llm = create_provider(
        settings.llm_provider, settings.llm_model, settings.llm_api_key,
        timeout=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries, telemetry=telemetry_config,
    )
    tools = ToolRegistry()
    tools.register(IssueLookupTool(settings.tickets_path))
    tools.register(NetworkCheckTool())
    tools.register(NetworkActionTool())

    short_term = ShortTermMemory(settings.memory_window_turns)
    assistant = AssistantService(
        classifier=ClassifierService(llm), summarizer=SummarizerService(llm), advisor=AdvisorService(llm),
        tools=tools, short_term=short_term, long_term=LongTermMemory(settings.db_path),
    )
    return assistant, short_term, llm


@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry_config = TelemetryConfig.from_env()
    init_telemetry(telemetry_config)          # 1) telemetry FIRST so startup is observable
    instrument_httpx()                         # 2) before any httpx client is created

    settings = get_settings()
    assistant, short_term, llm = build_assistant(settings, telemetry_config)
    app.state.assistant = assistant
    get_metrics().register_gauge(
        "assistant.active_tickets", short_term.active_tickets, "Tickets currently held in short-term memory"
    )
    logger.info("assistant ready", extra={"provider": settings.llm_provider, "model": llm.model})
    yield
    await llm.aclose()
    shutdown_telemetry()                       # flush buffered spans/metrics/logs


app = FastAPI(title="IT Support Assistant", version="0.1.0", lifespan=lifespan)
instrument_fastapi(app)


@app.middleware("http")
async def add_trace_id_header(request: Request, call_next):
    response = await call_next(request)
    if trace_id := current_trace_id():
        response.headers["X-Trace-Id"] = trace_id
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# TODO (tasks 1-5): add your routes here, ABOVE the static mount below.


if FRONTEND_DIR.exists():  # must stay LAST: it catches every remaining path
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
'''

# ------------------------------------------------------------------ data + frontend
FILES["data/sample_tickets.json"] = r'''[
  {"id": "T-1001", "details": "User reports Zoom drops every few minutes since morning; office Wi-Fi unstable. Router reboot already tried."},
  {"id": "T-1002", "details": "Laptop shows 'low disk space' warning; C: drive 97% full."},
  {"id": "T-1003", "details": "Docking station not detected after Windows update; external monitors stay black."}
]
'''

FILES["frontend/index.html"] = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>IT Support Assistant</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <main class="app">
    <header>
      <h1>IT Support Assistant</h1>
      <label>Ticket <input id="ticket-id" value="T-1001" /></label>
    </header>

    <section id="chat" class="chat" aria-live="polite"></section>
    <div id="status" class="status" hidden></div>

    <form id="chat-form" class="composer">
      <input id="message" placeholder="Describe your issue..." autocomplete="off" required />
      <button type="submit">Send</button>
    </form>
    <footer><small id="trace">trace id: -</small></footer>
  </main>
  <script src="app.js"></script>
</body>
</html>
'''

FILES["frontend/styles.css"] = r'''* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, sans-serif; background: #f3f4f6; color: #111827; }
.app { max-width: 760px; margin: 0 auto; min-height: 100vh; display: flex; flex-direction: column; padding: 16px; }
header { display: flex; justify-content: space-between; align-items: center; }
header input { width: 110px; padding: 4px 8px; }
.chat { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; padding: 12px 0; }
.msg { max-width: 85%; padding: 10px 14px; border-radius: 12px; white-space: pre-wrap; line-height: 1.4; }
.msg.user { align-self: flex-end; background: #2563eb; color: #fff; }
.msg.assistant { align-self: flex-start; background: #fff; border: 1px solid #e5e7eb; }
.msg.system { align-self: center; background: #fef3c7; font-size: 0.9rem; }
.confirm { display: flex; gap: 8px; margin-top: 8px; }
.confirm button { padding: 6px 12px; cursor: pointer; }
.status { color: #6b7280; font-size: 0.9rem; padding: 4px 0; }
.composer { display: flex; gap: 8px; }
.composer input { flex: 1; padding: 10px; border: 1px solid #d1d5db; border-radius: 8px; }
.composer button { padding: 10px 18px; border: 0; border-radius: 8px; background: #111827; color: #fff; cursor: pointer; }
footer { text-align: center; color: #9ca3af; padding-top: 8px; }
'''

FILES["frontend/app.js"] = r'''// Plain JS client. The HTML/CSS are done; this file is YOURS.
//
// YOUR TASKS
//  1. addMessage(role, text, extraNode?)  - append a div.msg.<role>, scroll to the bottom.
//  2. sendMessage(text): show "Classifying issue..." in #status (UX for latency, book ch.9), POST /api/chat
//     {ticket_id, message}, hide the status, render data.answer + data.recommendations (+ findings).
//  3. If data.pending_action exists: render Confirm / Cancel buttons (class "confirm") that call confirmAction().
//  4. confirmAction(actionId, approved): POST /api/actions/<id>/confirm {approved}, show result.message.
//  5. Show the X-Trace-Id response header in #trace (copy it when reporting a bug -> find it in Jaeger).
//  6. Handle errors: network failure or non-2xx -> a friendly "system" message, never a blank screen.

const chat = document.getElementById("chat");
const form = document.getElementById("chat-form");
const input = document.getElementById("message");
const statusEl = document.getElementById("status");
const traceEl = document.getElementById("trace");
const ticketEl = document.getElementById("ticket-id");

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const traceId = res.headers.get("X-Trace-Id");
  if (traceId) traceEl.textContent = `trace id: ${traceId}`;
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json();
}

function addMessage(role, text, extraNode) {
  // TODO (task 1)
}

async function sendMessage(text) {
  // TODO (tasks 2, 3, 6)
}

async function confirmAction(actionId, approved) {
  // TODO (task 4)
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMessage("user", text);
  sendMessage(text);
});
'''

# =============================================================================
# TESTS + README
# =============================================================================
FILES["backend/tests/__init__.py"] = ""

FILES["backend/tests/conftest.py"] = r'''"""Shared fixtures. `telemetry_capture` records REAL spans/metrics in memory so tests can assert on them."""
from __future__ import annotations

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_span_exporter = InMemorySpanExporter()
_metric_reader = InMemoryMetricReader()
# OpenTelemetry providers can only be set once per process, so we set them at import time.
_tracer_provider = TracerProvider()
_tracer_provider.add_span_processor(SimpleSpanProcessor(_span_exporter))
trace.set_tracer_provider(_tracer_provider)
metrics.set_meter_provider(MeterProvider(metric_readers=[_metric_reader]))


class Capture:
    def spans(self):
        return _span_exporter.get_finished_spans()

    def metric_names(self) -> set[str]:
        data = _metric_reader.get_metrics_data()
        if data is None:
            return set()
        return {m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}

    def metric_points(self, name: str):
        data = _metric_reader.get_metrics_data()
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == name:
                        return list(m.data.data_points)
        return []


@pytest.fixture
def telemetry_capture() -> Capture:
    _span_exporter.clear()
    return Capture()
'''

FILES["backend/tests/test_telemetry.py"] = r'''"""Telemetry is tested like any other feature: spans, attributes, errors and metrics."""
import pytest

from backend.llm.instrumented import InstrumentedProvider
from backend.llm.base import LLMProvider
from backend.llm.retry import RetryingProvider
from backend.llm.types import LLMError, LLMResponse, Message, Usage
from backend.telemetry import start_span, traced


class FakeProvider(LLMProvider):
    provider_name = "fake"
    model = "fake-1"

    def __init__(self, fail_times: int = 0) -> None:
        self.calls = 0
        self._fail_times = fail_times

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise LLMError("busy", retryable=True, status_code=429, error_type="rate_limited")
        return LLMResponse(text="hi", model="fake-1", provider="fake", usage=Usage(10, 5), finish_reason="stop")


def test_start_span_records_error(telemetry_capture):
    with pytest.raises(ValueError):
        with start_span("boom"):
            raise ValueError("bad")
    span = telemetry_capture.spans()[-1]
    assert span.name == "boom"
    assert span.status.status_code.name == "ERROR"
    assert span.attributes["error.type"] == "ValueError"


async def test_traced_decorator_async(telemetry_capture):
    @traced("demo.work")
    async def work():
        return 42

    assert await work() == 42
    assert telemetry_capture.spans()[-1].name == "demo.work"


async def test_llm_call_creates_span_and_metrics(telemetry_capture):
    provider = InstrumentedProvider(FakeProvider())
    await provider.chat([Message("user", "hello")])

    span = telemetry_capture.spans()[-1]
    assert span.name == "chat fake-1"
    assert span.attributes["gen_ai.usage.input_tokens"] == 10
    assert span.attributes["gen_ai.usage.output_tokens"] == 5
    assert "gen_ai.client.operation.duration" in telemetry_capture.metric_names()
    assert "gen_ai.client.token.usage" in telemetry_capture.metric_names()


async def test_prompt_content_not_captured_by_default(telemetry_capture):
    await InstrumentedProvider(FakeProvider()).chat([Message("user", "my password is hunter2")])
    events = [e for s in telemetry_capture.spans() for e in s.events]
    assert not any("hunter2" in str(e.attributes) for e in events)


async def test_retry_on_transient_error(telemetry_capture):
    inner = FakeProvider(fail_times=2)
    provider = RetryingProvider(InstrumentedProvider(inner), max_retries=2, base_delay_s=0.01)
    result = await provider.chat([Message("user", "x")])
    assert result.text == "hi" and inner.calls == 3
    assert "assistant.llm.retries" in telemetry_capture.metric_names()


async def test_non_retryable_error_is_raised_immediately(telemetry_capture):
    class AuthFail(FakeProvider):
        async def chat(self, messages, **kwargs):
            self.calls += 1
            raise LLMError("nope", status_code=401, error_type="auth_error")

    inner = AuthFail()
    with pytest.raises(LLMError):
        await RetryingProvider(InstrumentedProvider(inner), max_retries=3, base_delay_s=0.01).chat([Message("user", "x")])
    assert inner.calls == 1
'''

FILES["backend/tests/test_tools.py"] = r'''"""The governance layer is complete, so these tests pass as soon as you create the project."""
import asyncio

from backend.tools.base import RiskLevel, Tool, ToolResult
from backend.tools.registry import ToolRegistry


class DummyAction(Tool):
    name, description, risk = "dummy_action", "changes state", RiskLevel.ACTION

    def __init__(self) -> None:
        self.ran = 0

    async def run(self, **_) -> ToolResult:
        self.ran += 1
        return ToolResult("ok", "done")


class SlowTool(Tool):
    name, description, risk = "slow", "never finishes in time", RiskLevel.INFO

    async def run(self, **_) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult("ok", "late")


class CrashTool(Tool):
    name, description, risk = "crash", "raises", RiskLevel.INFO

    async def run(self, **_) -> ToolResult:
        raise RuntimeError("boom")


async def test_action_is_blocked_without_confirmation(telemetry_capture):
    registry, tool = ToolRegistry(), DummyAction()
    registry.register(tool)
    result = await registry.execute("dummy_action")
    assert result.status == "error" and result.category == "confirmation_required"
    assert tool.ran == 0  # the tool body NEVER ran


async def test_action_runs_when_confirmed(telemetry_capture):
    registry, tool = ToolRegistry(), DummyAction()
    registry.register(tool)
    assert (await registry.execute("dummy_action", confirmed=True)).ok
    assert tool.ran == 1


async def test_timeout_returns_retryable_error(telemetry_capture):
    registry = ToolRegistry()
    registry.register(SlowTool())
    result = await registry.execute("slow", timeout_s=0.05)
    assert result.category == "transient_timeout" and result.retryable


async def test_crash_is_contained(telemetry_capture):
    registry = ToolRegistry()
    registry.register(CrashTool())
    result = await registry.execute("crash")
    assert result.category == "tool_error"
    assert "assistant.tool.calls" in telemetry_capture.metric_names()
'''

FILES["backend/tests/test_services.py"] = r'''"""YOUR TASK: test classifier/summarizer/advisor with a FakeProvider (see test_telemetry.py for one).

Ideas
  - classifier returns UNKNOWN (not an exception) when the model replies with garbage
  - classifier handles JSON wrapped in ```json fences
  - advisor never claims a diagnostic ran when there were no findings
"""
import pytest


@pytest.mark.skip(reason="TODO: write after implementing ClassifierService")
def test_classifier_fallback_on_garbage():
    ...
'''

FILES["backend/tests/test_assistant_flow.py"] = r'''"""YOUR TASK: end-to-end flow with a FakeProvider + a real ToolRegistry.

Must prove
  - a NETWORK ticket produces a PendingAction and the network_action tool has NOT run yet
  - confirm(approved=False) -> nothing executes
  - confirm(approved=True) twice -> the second call reports 'already restarted' (idempotency)
"""
import pytest


@pytest.mark.skip(reason="TODO: write after implementing AssistantService")
def test_confirmation_gate():
    ...
'''

FILES["README.md"] = r'''# IT Support Assistant (production-style)

Pure-Python AI agent for IT support - from Chapter 9 of *Building Production AI Agents for the Web*.
Any LLM (OpenAI, Groq, Gemini, Anthropic, Ollama, OpenRouter, DeepSeek) + full OpenTelemetry observability.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                      # set LLM_PROVIDER + the matching API key
pytest                                                    # telemetry + tool-governance tests pass immediately
make console                                              # run, telemetry printed to terminal (no docker)
# or the full stack:
make obs-up && make run                                   # Grafana :3000  Jaeger :16686  Prometheus :9090
```
Open http://localhost:8000 . Complete the TODOs first (see "Build order").

## Build order (each file has numbered YOUR TASKS)
1. `utils/config.py`, `utils/json_utils.py`
2. `llm/openai_compat.py`, then `llm/anthropic_provider.py`   (smoke test with one provider)
3. `memory/short_term.py`, `memory/long_term.py`
4. `tools/issue_lookup.py`, `network_check.py`, `network_action.py`
5. `services/classifier.py` -> `summarizer.py` -> `advisor.py`
6. `assistant.py`, then routes in `main.py`
7. `frontend/app.js`, then write `tests/test_services.py` and `tests/test_assistant_flow.py`

## Architecture
```
frontend -> FastAPI (main.py) -> AssistantService (assistant.py)
                                   |- ClassifierService -> SummarizerService -> AdvisorService   (LLM steps)
                                   |- ToolRegistry: issue_lookup | network_check | network_action(confirm!)
                                   |- ShortTermMemory (per ticket) + LongTermMemory (SQLite)
                                   '- LLM stack: Retrying( Instrumented( OpenAICompat | Anthropic ) )
```
Safety rules enforced in CODE: ACTION tools never run without confirmation; tools time out; tool crashes become
structured errors; the classifier degrades to UNKNOWN instead of failing; actions are idempotent.

## Observability in 5 minutes
| Signal  | Question it answers                         | Where to look                      |
|---------|---------------------------------------------|------------------------------------|
| Trace   | "Why was THIS request slow / failing?"      | Jaeger  http://localhost:16686     |
| Metric  | "Is the system healthy over time?"          | Grafana http://localhost:3000      |
| Log     | "What did the code say?" (trace_id linked)  | Grafana -> Explore -> Loki         |

One request produces this trace tree:
```
POST /api/chat                          <- FastAPI auto-instrumentation
 '- assistant.handle_message
     |- service.classify -> chat <model> -> POST https://api...   <- LLM span -> httpx span
     |- tool.network_check
     |- service.summarize -> chat <model>
     '- service.advise   -> chat <model>
```
Debug workflow: user reports a problem -> they copy the **trace id** shown in the UI -> paste it in Jaeger.

Metrics emitted: `gen_ai.client.operation.duration`, `gen_ai.client.token.usage`, `assistant.llm.retries`,
`assistant.requests`, `assistant.request.duration`, `assistant.classifications`, `assistant.tool.calls`,
`assistant.tool.duration`, `assistant.confirmations`, `assistant.active_tickets`.
Alerts live in `deploy/alerts.yml` (LLM error rate, p95 latency, tool failures).

### Reuse telemetry in another app
1. Copy `backend/telemetry/` (it imports nothing from this project) and `pip install` the 5 `opentelemetry-*` packages.
2. Call `init_telemetry()` at startup, `shutdown_telemetry()` at exit, `instrument_fastapi(app)`.
3. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at any OTLP backend (this Collector, Grafana Cloud, Datadog, Honeycomb...).

### Production checklist
- [ ] `TELEMETRY_CAPTURE_CONTENT=false` (prompts can contain personal data) - already the default
- [ ] Lower `TELEMETRY_SAMPLE_RATIO` (e.g. 0.1) under heavy traffic; keep error traces via Collector tail-sampling
- [ ] Never use ticket/user ids as metric attributes (cardinality explosion) - they belong on spans/logs only
- [ ] Put Prometheus/Grafana/Collector behind auth and a private network; change the Grafana admin password
- [ ] Add alert routing (Alertmanager -> Slack/PagerDuty) for `deploy/alerts.yml`
- [ ] Run the app with several workers behind a reverse proxy; move short-term memory to Redis when you scale out
- [ ] Keep API keys in a secret manager, not in `.env` files on servers
'''

# =============================================================================
# MAIN
# =============================================================================
def main(argv: list[str]) -> int:
    force = "--force" in argv
    args = [a for a in argv if not a.startswith("--")]
    cwd = Path.cwd()
    if args:
        root = Path(args[0]).resolve()
    elif cwd.name == "assistant-app":
        root = cwd
    else:
        root = (cwd / "assistant-app").resolve()

    created, skipped = 0, 0
    for rel_path, content in FILES.items():
        target = root / rel_path
        if target.exists() and not force:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created += 1

    (root / "data").mkdir(exist_ok=True)
    print(f"Project folder : {root}")
    print(f"Files created  : {created}   skipped (already exist): {skipped}")
    print("\nNext steps:")
    print("  cd", root)
    print("  python -m venv .venv && source .venv/bin/activate   # Windows: .venv\\Scripts\\activate")
    print("  pip install -r requirements.txt && cp .env.example .env")
    print("  pytest        # telemetry + tool tests should already pass")
    print("  then follow 'Build order' in README.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
