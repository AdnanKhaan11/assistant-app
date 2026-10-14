"""Check the backward-compatible streaming default on existing providers."""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from backend.llm import LLMResponse, Message, Usage
from backend.tests.fakes import FakeProvider


async def test_default_chat_stream_yields_full_chat_response_once() -> None:
    provider = FakeProvider(["whole answer"])
    chunks: list[str] = [chunk async for chunk in provider.chat_stream([Message("user", "hello")])]

    assert chunks == ["whole answer"]
    assert len(provider.calls) == 1


class _CustomChatProvider(FakeProvider):
    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Supply a fixed result to demonstrate the interface's default stream."""
        return LLMResponse("single chunk", self.model, self.provider_name, Usage())


async def test_default_stream_is_an_async_iterator() -> None:
    stream: AsyncIterator[str] = _CustomChatProvider([]).chat_stream([Message("user", "hello")])

    assert [chunk async for chunk in stream] == ["single chunk"]
