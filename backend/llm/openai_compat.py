"""Adapter for every OpenAI-style API: POST {base_url}/chat/completions.

Works for OpenAI, Groq, Gemini (OpenAI-compat endpoint), Ollama, OpenRouter, DeepSeek...
Only `base_url`, `api_key` and `model` differ. Pure httpx - no vendor SDK.

Wire format you need
    request : {"model": "...", "messages": [{"role": "...", "content": "..."}],
               "temperature": 0.2, "max_tokens": 1024,
               "response_format": {"type": "json_object"}}          <- only when json_mode=True
    response: {"choices": [{"message": {"content": "..."}, "finish_reason": "stop"}],
               "model": "...",
               "usage": {"prompt_tokens": 12, "completion_tokens": 34}}

"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Sequence

import httpx

from .base import LLMProvider, map_http_error  # noqa: F401  (you will use map_http_error in chat())
from .types import LLMError, LLMResponse, Message, Usage  # noqa: F401


class OpenAICompatProvider(LLMProvider):
    """Translate the shared message format into an OpenAI-compatible HTTP API."""

    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 30.0,
    ) -> None:
        self.provider_name = provider_name
        self.model = model
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def _build_payload(self, messages: Sequence[Message], temperature: float, max_tokens: int, json_mode: bool) -> dict:
        """Build the provider request body without mutating the input messages."""
        token_field = "max_completion_tokens" if self.provider_name == "openai" else "max_tokens"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_payload(message) for message in messages],
            "temperature": temperature,
            token_field: max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _message_payload(message: Message) -> dict[str, Any]:
        """Encode optional image parts using the OpenAI-compatible content format."""
        if not message.images:
            return message.to_dict()
        content: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image.media_type};base64,{image.data_base64}"},
            }
            for image in message.images
        )
        return {"role": message.role, "content": content}

    def _parse_response(self, data: dict) -> LLMResponse:
        """Convert an OpenAI-compatible response, tolerating absent optional fields."""
        choices = data.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        message_data = choice.get("message")
        message = message_data if isinstance(message_data, dict) else {}
        usage = data.get("usage")
        usage_data = usage if isinstance(usage, dict) else {}
        text = message.get("content")
        return LLMResponse(
            text=text if isinstance(text, str) else "",
            model=data.get("model") or self.model,
            provider=self.provider_name,
            usage=Usage(
                input_tokens=usage_data.get("prompt_tokens", 0) or 0,
                output_tokens=usage_data.get("completion_tokens", 0) or 0,
            ),
            finish_reason=choice.get("finish_reason"),
        )

    async def chat(
        self, messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 1024, json_mode: bool = False
    ) -> LLMResponse:
        """Send one completion request and normalize provider failures."""
        payload = self._build_payload(messages, temperature, max_tokens, json_mode)
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as error:
            raise LLMError(
                "LLM request timed out",
                retryable=True,
                error_type="timeout",
            ) from error
        except httpx.TransportError as error:
            raise LLMError(
                "LLM request failed due to a network error",
                retryable=True,
                error_type="network_error",
            ) from error

        if response.status_code >= 400:
            raise map_http_error(response.status_code, response.text)
        try:
            response_data = response.json()
        except json.JSONDecodeError as error:
            raise LLMError("LLM returned an invalid response", error_type="invalid_response") from error
        if not isinstance(response_data, dict):
            raise LLMError("LLM returned an invalid response", error_type="invalid_response")
        return self._parse_response(response_data)

    async def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Yield text deltas from an OpenAI-compatible SSE response."""
        payload = self._build_payload(messages, temperature, max_tokens, json_mode)
        payload["stream"] = True
        if self.provider_name == "openai":
            payload["stream_options"] = {"include_usage": True}
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if not 200 <= response.status_code < 300:
                    await response.aread()
                    raise map_http_error(response.status_code, response.text)
                async for line in response.aiter_lines():
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    for chunk in self._parse_stream_delta(data):
                        yield chunk
        except httpx.TimeoutException as error:
            raise LLMError("LLM request timed out", retryable=True, error_type="timeout") from error
        except httpx.TransportError as error:
            raise LLMError(
                "LLM request failed due to a network error",
                retryable=True,
                error_type="network_error",
            ) from error

    @staticmethod
    def _parse_stream_delta(data: str) -> tuple[str, ...]:
        """Return any text delta from one SSE data line, tolerating usage-only events."""
        try:
            event = json.loads(data)
        except json.JSONDecodeError as error:
            raise LLMError("LLM returned an invalid stream event", error_type="invalid_response") from error
        if not isinstance(event, dict):
            return ()
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return ()
        delta = choices[0].get("delta")
        if not isinstance(delta, dict) or not isinstance(delta.get("content"), str):
            return ()
        return (delta["content"],)

    async def aclose(self) -> None:
        await self._client.aclose()
