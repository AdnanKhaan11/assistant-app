"""Telemetry is tested like any other feature: spans, attributes, errors and metrics."""
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
