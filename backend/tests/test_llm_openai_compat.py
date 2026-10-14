from __future__ import annotations

import json

import httpx
import pytest

from backend.llm.openai_compat import OpenAICompatProvider
from backend.llm.types import ImagePart, LLMError, Message


async def _use_transport(provider: OpenAICompatProvider, transport: httpx.AsyncBaseTransport) -> None:
    await provider.aclose()
    provider._client = httpx.AsyncClient(
        base_url="https://provider.test/v1",
        headers=provider._client.headers,
        transport=transport,
    )


def _provider(provider_name: str = "groq") -> OpenAICompatProvider:
    return OpenAICompatProvider(
        provider_name=provider_name,
        base_url="https://provider.test/v1",
        api_key="test-key",
        model="test-model",
    )


def test_payload_uses_provider_token_field_and_json_mode() -> None:
    messages = [Message("system", "rules"), Message("user", "hello")]
    groq_payload = _provider()._build_payload(messages, 0.3, 200, True)
    openai_payload = _provider("openai")._build_payload(messages, 0.3, 200, False)

    assert groq_payload["max_tokens"] == 200
    assert groq_payload["response_format"] == {"type": "json_object"}
    assert "response_format" not in openai_payload
    assert openai_payload["max_completion_tokens"] == 200
    assert "max_tokens" not in openai_payload


def test_payload_encodes_image_parts_as_data_urls() -> None:
    payload = _provider()._build_payload(
        [Message("user", "inspect", (ImagePart("image/png", "aGVsbG8="),))], 0.2, 100, False
    )

    assert payload["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
            ],
        }
    ]


def test_parse_response_handles_missing_optional_data() -> None:
    response = _provider()._parse_response({"choices": [{"message": {"content": "ok"}}]})

    assert response.text == "ok"
    assert response.model == "test-model"
    assert response.provider == "groq"
    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.finish_reason is None


async def test_chat_posts_expected_wire_format() -> None:
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "served-model",
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    provider = _provider()
    await _use_transport(provider, httpx.MockTransport(handle))
    try:
        result = await provider.chat([Message("user", "question")], json_mode=True)
    finally:
        await provider.aclose()

    assert seen["path"] == "/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert result.text == "answer"
    assert result.model == "served-model"
    assert result.usage.input_tokens == 5
    assert result.usage.output_tokens == 2


@pytest.mark.parametrize(
    ("status", "error_type", "retryable"),
    [(401, "auth_error", False), (429, "rate_limited", True)],
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


async def test_chat_stream_parses_sse_chunks_and_ignores_empty_deltas() -> None:
    body = (
        b": keepalive\n\n"
        b"data: {\"choices\":[{\"delta\":{\"content\":\"hel\"}}]}\n\n"
        b"data: {\"choices\":[{\"delta\":{\"content\":null}}]}\n\n"
        b"data: {\"choices\":[{\"delta\":{}}]}\n\n"
        b"data: {\"choices\":[],\"usage\":{\"prompt_tokens\":2}}\n\n"
        b"data: {\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n\n"
        b"data: [DONE]\n\n"
        b"data: {\"choices\":[{\"delta\":{\"content\":\"ignored\"}}]}\n\n"
    )
    provider = _provider()
    await _use_transport(provider, httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
    try:
        chunks = [chunk async for chunk in provider.chat_stream([Message("user", "question")])]
    finally:
        await provider.aclose()

    assert chunks == ["hel", "lo"]


async def test_openai_stream_payload_enables_usage_options() -> None:
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    provider = _provider("openai")
    await _use_transport(provider, httpx.MockTransport(handle))
    try:
        assert [chunk async for chunk in provider.chat_stream([Message("user", "hello")])] == []
    finally:
        await provider.aclose()

    assert seen["payload"]["stream"] is True
    assert seen["payload"]["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize(("status", "error_type"), [(401, "auth_error"), (429, "rate_limited"), (500, "provider_error")])
async def test_chat_stream_maps_http_errors(status: int, error_type: str) -> None:
    provider = _provider()
    await _use_transport(provider, httpx.MockTransport(lambda _: httpx.Response(status, text="failure")))
    try:
        with pytest.raises(LLMError) as captured:
            _ = [chunk async for chunk in provider.chat_stream([Message("user", "question")])]
    finally:
        await provider.aclose()

    assert captured.value.error_type == error_type


async def test_stream_data_split_across_transport_chunks_is_parsed() -> None:
    body = b"data: {\"choices\":[{\"delta\":{\"content\":\"split\"}}]}\n\ndata: [DONE]\n\n"

    class _SplitStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body[:20]
            yield body[20:]

        async def aclose(self) -> None:
            return None

    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_SplitStream())

    provider = _provider()
    await _use_transport(provider, httpx.MockTransport(handle))
    try:
        chunks = [chunk async for chunk in provider.chat_stream([Message("user", "question")])]
    finally:
        await provider.aclose()

    assert chunks == ["split"]

