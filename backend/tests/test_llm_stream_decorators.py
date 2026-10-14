"""Verify bounded retries and telemetry lifecycle for async LLM streams."""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from backend.llm import LLMError, LLMResponse, LLMProvider, Message, Usage
from backend.llm.instrumented import InstrumentedProvider
from backend.llm.retry import RetryingProvider
from backend.tests.conftest import Capture


class _StreamProvider(LLMProvider):
    provider_name = "stream-test"
    model = "stream-model"

    def __init__(self, outcomes: Sequence[Sequence[str | LLMError]]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        return LLMResponse("fallback", self.model, self.provider_name, Usage())

    async def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> AsyncIterator[str]:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        for item in outcome:
            if isinstance(item, LLMError):
                raise item
            yield item


async def _no_sleep(_: float) -> None:
    return None


def _metric_count(capture: Capture, metric_name: str) -> int:
    return sum(point.count for point in capture.metric_points(metric_name))


async def test_retry_restarts_when_failure_precedes_first_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.llm.retry.asyncio.sleep", _no_sleep)
    provider = _StreamProvider(
        [[LLMError("temporary", retryable=True)], ["first", "second"]],
    )
    retrying = RetryingProvider(provider, max_retries=1, base_delay_s=0)

    chunks = [chunk async for chunk in retrying.chat_stream([Message("user", "hello")])]

    assert chunks == ["first", "second"]
    assert provider.calls == 2


async def test_retry_never_restarts_after_a_chunk_is_yielded() -> None:
    provider = _StreamProvider([["partial", LLMError("late", retryable=True)], ["duplicate"]])
    retrying = RetryingProvider(provider, max_retries=2, base_delay_s=0)
    chunks: list[str] = []

    with pytest.raises(LLMError, match="late"):
        async for chunk in retrying.chat_stream([Message("user", "hello")]):
            chunks.append(chunk)

    assert chunks == ["partial"]
    assert provider.calls == 1


async def test_instrumentation_records_one_span_and_metrics_after_normal_end(
    telemetry_capture: Capture,
) -> None:
    provider = InstrumentedProvider(_StreamProvider([["one", "two"]]))
    before = _metric_count(telemetry_capture, "gen_ai.client.operation.duration")

    assert [chunk async for chunk in provider.chat_stream([Message("user", "hello")])] == ["one", "two"]

    spans = telemetry_capture.spans()
    assert len(spans) == 1
    assert spans[0].name == "chat stream-model"
    assert _metric_count(telemetry_capture, "gen_ai.client.operation.duration") == before + 1


async def test_instrumentation_finishes_on_early_close(telemetry_capture: Capture) -> None:
    provider = InstrumentedProvider(_StreamProvider([["first", "second"]]))
    before = _metric_count(telemetry_capture, "gen_ai.client.operation.duration")
    stream = provider.chat_stream([Message("user", "hello")])

    assert await anext(stream) == "first"
    await stream.aclose()

    spans = telemetry_capture.spans()
    assert len(spans) == 1
    assert spans[0].name == "chat stream-model"
    assert _metric_count(telemetry_capture, "gen_ai.client.operation.duration") == before + 1
