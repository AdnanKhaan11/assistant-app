"""Telemetry decorator for ANY provider (Decorator pattern).

Adapters stay free of monitoring code; this wrapper adds, for every LLM call:
    * a CLIENT span  "chat <model>"  with gen_ai.* attributes   (visible in Jaeger)
    * latency histogram + token histograms + error type          (visible in Grafana)
"""
from __future__ import annotations

import time
from typing import AsyncIterator, Sequence

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

    async def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Trace and measure one complete stream, including early consumer closure."""
        attributes = {
            "gen_ai.operation.name": "chat",
            "gen_ai.system": self.provider_name,
            "gen_ai.request.model": self.model,
            "gen_ai.request.temperature": temperature,
            "gen_ai.request.max_tokens": max_tokens,
            "assistant.llm.json_mode": json_mode,
        }
        started = time.perf_counter()
        error_type: str | None = None
        with start_span(f"chat {self.model}", attributes, kind=SpanKind.CLIENT) as span:
            try:
                async for chunk in self._inner.chat_stream(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                ):
                    yield chunk
            except Exception as exc:
                error_type = getattr(exc, "error_type", type(exc).__name__)
                span.set_attribute("error.type", error_type)
                raise
            finally:
                get_metrics().record_llm_call(
                    provider=self.provider_name,
                    model=self.model,
                    duration_s=time.perf_counter() - started,
                    input_tokens=0,
                    output_tokens=0,
                    error_type=error_type,
                )

    async def aclose(self) -> None:
        await self._inner.aclose()
