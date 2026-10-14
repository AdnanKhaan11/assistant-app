"""Telemetry configuration - everything comes from environment variables (12-factor).

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
