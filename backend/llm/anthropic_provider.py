"""Adapter for Anthropic Claude: POST {base_url}/v1/messages.

Differences from OpenAI that you must handle
    * auth headers : "x-api-key": <key>   and   "anthropic-version": "2023-06-01"
    * the system prompt is a TOP-LEVEL field `system`, NOT a message with role "system"
    * `max_tokens` is required
    * no native JSON mode -> when json_mode=True append "Respond with a single valid JSON object only." to `system`

Wire format
    request : {"model": "...", "max_tokens": 1024, "temperature": 0.2,
               "system": "...", "messages": [{"role": "user", "content": "..."}]}
    response: {"content": [{"type": "text", "text": "..."}], "model": "...", "stop_reason": "end_turn",
               "usage": {"input_tokens": 12, "output_tokens": 34}}

"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Sequence

import httpx

from .base import LLMProvider, map_http_error  # noqa: F401
from .types import LLMError, LLMResponse, Message, Usage  # noqa: F401

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    """Translate the shared message format into Anthropic's messages API."""

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
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "x-api-key": api_key or "",
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    @staticmethod
    def _split_system(messages: Sequence[Message]) -> tuple[str, list[Message]]:
        """Move system messages to Anthropic's top-level system prompt."""
        system_parts = [message.content for message in messages if message.role == "system"]
        other_messages = [message for message in messages if message.role != "system"]
        return "\n".join(system_parts), other_messages

    def _build_payload(self, messages: Sequence[Message], temperature: float, max_tokens: int, json_mode: bool) -> dict:
        """Build an Anthropic request body, keeping system instructions separate."""
        system_text, conversation = self._split_system(messages)
        if json_mode:
            json_instruction = "Respond with a single valid JSON object only."
            system_text = f"{system_text}\n{json_instruction}" if system_text else json_instruction
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_text,
            "messages": [self._message_payload(message) for message in conversation],
        }

    @staticmethod
    def _message_payload(message: Message) -> dict[str, Any]:
        """Encode optional image parts as Anthropic base64 source blocks."""
        if not message.images:
            return message.to_dict()
        content: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        content.extend(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": image.media_type, "data": image.data_base64},
            }
            for image in message.images
        )
        return {"role": message.role, "content": content}

    def _parse_response(self, data: dict) -> LLMResponse:
        """Join text blocks and normalize Anthropic usage and stop details."""
        blocks = data.get("content")
        if not isinstance(blocks, list):
            blocks = []
        text = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )
        usage = data.get("usage")
        usage_data = usage if isinstance(usage, dict) else {}
        return LLMResponse(
            text=text,
            model=data.get("model") or self.model,
            provider=self.provider_name,
            usage=Usage(
                input_tokens=usage_data.get("input_tokens", 0) or 0,
                output_tokens=usage_data.get("output_tokens", 0) or 0,
            ),
            finish_reason=data.get("stop_reason"),
        )

    async def chat(
        self, messages: Sequence[Message], *, temperature: float = 0.2, max_tokens: int = 1024, json_mode: bool = False
    ) -> LLMResponse:
        """Send one messages request and normalize provider failures."""
        payload = self._build_payload(messages, temperature, max_tokens, json_mode)
        try:
            response = await self._client.post("/v1/messages", json=payload)
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
        """Yield text deltas from Anthropic's event stream."""
        payload = self._build_payload(messages, temperature, max_tokens, json_mode)
        payload["stream"] = True
        event_name = ""
        try:
            async with self._client.stream("POST", "/v1/messages", json=payload) as response:
                if not 200 <= response.status_code < 300:
                    await response.aread()
                    raise map_http_error(response.status_code, response.text)
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data = line[5:].strip()
                        if data:
                            chunk = self._parse_stream_event(event_name, data)
                            if chunk is not None:
                                yield chunk
                    elif not line:
                        event_name = ""
        except httpx.TimeoutException as error:
            raise LLMError("LLM request timed out", retryable=True, error_type="timeout") from error
        except httpx.TransportError as error:
            raise LLMError(
                "LLM request failed due to a network error",
                retryable=True,
                error_type="network_error",
            ) from error

    @staticmethod
    def _parse_stream_event(event_name: str, data: str) -> str | None:
        """Extract text from content events and raise for provider error events."""
        try:
            event = json.loads(data)
        except json.JSONDecodeError as error:
            raise LLMError("LLM returned an invalid stream event", error_type="invalid_response") from error
        if not isinstance(event, dict):
            return None
        if event_name == "error" or event.get("type") == "error":
            error_data = event.get("error")
            message = error_data.get("message", "provider stream failed") if isinstance(error_data, dict) else "provider stream failed"
            raise LLMError(str(message), error_type="provider_error")
        if event_name == "ping":
            return None
        delta = event.get("delta")
        if event_name == "content_block_delta" and isinstance(delta, dict):
            text = delta.get("text")
            if delta.get("type") == "text_delta" and isinstance(text, str):
                return text
        return None

    async def aclose(self) -> None:
        await self._client.aclose()
