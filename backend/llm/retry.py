"""Retry transient LLM failures (429 / 5xx / timeouts) with exponential backoff + jitter.

Wrapping order matters:  Retrying( Instrumented( adapter ) )
-> every ATTEMPT gets its own span and latency sample, and retries are counted separately.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import AsyncIterator, Sequence

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

    async def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Retry only failures that happen before any stream content reaches the caller."""
        attempt = 0
        while True:
            has_yielded = False
            stream = self._inner.chat_stream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
            try:
                async for chunk in stream:
                    has_yielded = True
                    yield chunk
                return
            except LLMError as exc:
                if has_yielded or not exc.retryable or attempt >= self._max_retries:
                    raise
                attempt += 1
                delay = self._base_delay_s * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                get_metrics().record_llm_retry(provider=self.provider_name, model=self.model, reason=exc.error_type)
                logger.warning(
                    "llm stream failed before first chunk, retrying",
                    extra={"attempt": attempt, "delay_s": round(delay, 2), "error_type": exc.error_type},
                )
                await asyncio.sleep(delay)
            finally:
                close_stream = getattr(stream, "aclose", None)
                if close_stream is not None:
                    await close_stream()

    async def aclose(self) -> None:
        await self._inner.aclose()
