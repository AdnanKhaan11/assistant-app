from __future__ import annotations

import json

import httpx
import pytest

from backend.llm.anthropic_provider import ANTHROPIC_VERSION, AnthropicProvider
from backend.llm.types import ImagePart, LLMError, Message


async def _use_transport(provider: AnthropicProvider, transport: httpx.AsyncBaseTransport) -> None:
    headers = provider._client.headers
    await provider.aclose()
    provider._client = httpx.AsyncClient(
        base_url="https://provider.test",
        headers=headers,
        transport=transport,
    )


def _provider() -> AnthropicProvider:
    return AnthropicProvider(
        provider_name="anthropic",
        base_url="https://provider.test",
        api_key="test-key",
        model="claude-test",
    )


def test_split_system_preserves_non_system_messages() -> None:
    messages = [
        Message("system", "first rule"),
        Message("user", "question"),
        Message("system", "second rule"),
    ]

    system, conversation = AnthropicProvider._split_system(messages)

    assert system == "first rule\nsecond rule"
    assert conversation == [Message("user", "question")]


def test_payload_moves_system_and_adds_json_instruction() -> None:
    provider = _provider()
    payload = provider._build_payload(
        [Message("system", "rules"), Message("user", "hello")], 0.1, 500, True
    )

    assert payload["system"] == "rules\nRespond with a single valid JSON object only."
    assert payload["max_tokens"] == 500
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert "response_format" not in payload


def test_payload_encodes_image_parts_as_anthropic_source_blocks() -> None:
    payload = _provider()._build_payload(
        [Message("user", "inspect", (ImagePart("image/jpeg", "aGVsbG8="),))], 0.2, 100, False
    )

    assert payload["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "aGVsbG8="}},
            ],
        }
    ]


def test_parse_response_joins_text_blocks_and_ignores_other_blocks() -> None:
    response = _provider()._parse_response(
        {
            "content": [
                {"type": "text", "text": "hello "},
                {"type": "tool_use", "id": "ignored"},
                {"type": "text", "text": "world"},
            ],
            "usage": {"input_tokens": 8, "output_tokens": 3},
            "stop_reason": "end_turn",
        }
    )

    assert response.text == "hello world"
    assert response.model == "claude-test"
    assert response.usage.input_tokens == 8
    assert response.usage.output_tokens == 3
    assert response.finish_reason == "end_turn"


async def test_chat_posts_anthropic_wire_format() -> None:
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["key"] = request.headers["x-api-key"]
        seen["version"] = request.headers["anthropic-version"]
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "answer"}],
                "usage": {"input_tokens": 4, "output_tokens": 6},
            },
        )

    provider = _provider()
    await _use_transport(provider, httpx.MockTransport(handle))
    try:
        result = await provider.chat(
            [Message("system", "policy"), Message("user", "question")], json_mode=True
        )
    finally:
        await provider.aclose()

    assert seen["path"] == "/v1/messages"
    assert seen["key"] == "test-key"
    assert seen["version"] == ANTHROPIC_VERSION
    assert seen["payload"]["system"] == "policy\nRespond with a single valid JSON object only."
    assert result.text == "answer"
    assert result.usage.input_tokens == 4
    assert result.usage.output_tokens == 6


@pytest.mark.parametrize(
    ("status", "error_type", "retryable"),
    [(403, "auth_error", False), (500, "provider_error", True)],
)
async def test_chat_maps_http_errors(status: int, error_type: str, retryable: bool) -> None:
    provider = _provider()
    await _use_transport(provider, httpx.MockTransport(lambda _: httpx.Response(status, text="failure")))
    try:
        with pytest.raises(LLMError) as captured:
            await provider.chat([Message("user", "question")])
    finally:
        await provider.aclose()

    assert captured.value.error_type == error_type
    assert captured.value.retryable is retryable
    assert captured.value.status_code == status


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [(httpx.ReadTimeout("late"), "timeout"), (httpx.ConnectError("offline"), "network_error")],
)
async def test_chat_maps_transport_errors(failure: Exception, error_type: str) -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        raise failure

    provider = _provider()
    await _use_transport(provider, httpx.MockTransport(handle))
    try:
        with pytest.raises(LLMError) as captured:
            await provider.chat([Message("user", "question")])
    finally:
        await provider.aclose()

    assert captured.value.error_type == error_type
    assert captured.value.retryable


async def test_chat_stream_yields_text_deltas_and_ignores_ping_events() -> None:
    body = (
        b"event: ping\ndata: {\"type\":\"ping\"}\n\n"
        b"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"hel\"}}\n\n"
        b"event: message_delta\ndata: {\"type\":\"message_delta\",\"usage\":{\"output_tokens\":3}}\n\n"
        b"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"lo\"}}\n\n"
    )
    provider = _provider()
    await _use_transport(provider, httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
    try:
        chunks = [chunk async for chunk in provider.chat_stream([Message("user", "question")])]
    finally:
        await provider.aclose()

    assert chunks == ["hel", "lo"]


async def test_chat_stream_maps_provider_error_event() -> None:
    body = b"event: error\ndata: {\"type\":\"error\",\"error\":{\"message\":\"failed\"}}\n\n"
    provider = _provider()
    await _use_transport(provider, httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
    try:
        with pytest.raises(LLMError, match="failed") as captured:
            _ = [chunk async for chunk in provider.chat_stream([Message("user", "question")])]
    finally:
        await provider.aclose()

    assert captured.value.error_type == "provider_error"

