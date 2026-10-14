"""Shared fixtures. `telemetry_capture` records REAL spans/metrics in memory so tests can assert on them."""
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
