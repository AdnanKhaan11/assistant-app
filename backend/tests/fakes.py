"""Small deterministic test doubles shared by LLM-backed service tests."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import AsyncIterator, Sequence

from backend.llm import LLMError, LLMProvider, LLMResponse, Message, Usage


@dataclass(frozen=True)
class FakeCall:
    """Capture the exact provider request options for assertions."""

    messages: tuple[Message, ...]
    temperature: float
    max_tokens: int
    json_mode: bool


class FakeProvider(LLMProvider):
    """Return queued text or errors while recording calls without network access."""

    provider_name = "fake"
    model = "fake-model"

    def __init__(
        self,
        responses: Sequence[str | LLMError],
        *,
        stream_chunks: Sequence[Sequence[str | LLMError]] | None = None,
    ) -> None:
        self._responses: deque[str | LLMError] = deque(responses)
        self._stream_chunks = deque(stream_chunks or [])
        self.calls: list[FakeCall] = []

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Record the request and return its next configured result."""
        self.calls.append(FakeCall(tuple(messages), temperature, max_tokens, json_mode))
        if not self._responses:
            raise AssertionError("FakeProvider has no queued response")
        result = self._responses.popleft()
        if isinstance(result, LLMError):
            raise result
        return LLMResponse(text=result, model=self.model, provider=self.provider_name, usage=Usage())

    async def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Yield scripted chunks or use the provider's one-response stream fallback."""
        if not self._stream_chunks:
            async for chunk in super().chat_stream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            ):
                yield chunk
            return
        self.calls.append(FakeCall(tuple(messages), temperature, max_tokens, json_mode))
        for item in self._stream_chunks.popleft():
            if isinstance(item, LLMError):
                raise item
            yield item
