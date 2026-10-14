"""All metric instruments in ONE place (so dashboards and code never drift apart).

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
