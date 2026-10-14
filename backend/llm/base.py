"""Provider interface + shared HTTP error mapping."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Sequence

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

    async def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Provide a compatible one-chunk stream for providers without native streaming."""
        response = await self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        yield response.text


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
