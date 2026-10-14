"""Create text vectors through Hugging Face's hosted feature-extraction endpoint."""

from __future__ import annotations

import math
from urllib.parse import quote

import httpx


class HuggingFaceEmbeddingClient:
    """Send bounded batches of text to a configured Hugging Face model."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        token: str | None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Store endpoint settings and an optional testable HTTP client."""
        self._url = f"{endpoint.rstrip('/')}/{quote(model, safe='/')}"
        self._token = token
        self._timeout = timeout_seconds
        self._client = client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one finite, non-empty vector per input text."""
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding input must contain non-empty text.")
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        if self._client is not None:
            response = await self._client.post(
                self._url,
                json={"inputs": texts, "options": {"wait_for_model": True}},
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    json={"inputs": texts, "options": {"wait_for_model": True}},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
        return _parse_vectors(payload, len(texts))


def _parse_vectors(payload: object, expected_count: int) -> list[list[float]]:
    """Validate the provider response shape and convert values to floats."""
    if not isinstance(payload, list) or len(payload) != expected_count:
        raise ValueError("Embedding provider returned an unexpected number of vectors.")
    vectors: list[list[float]] = []
    for row in payload:
        if not isinstance(row, list) or not row:
            raise ValueError("Embedding provider returned an invalid vector.")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in row):
            raise ValueError("Embedding provider returned a non-numeric vector value.")
        vector = [float(value) for value in row]
        if any(not math.isfinite(value) for value in vector):
            raise ValueError("Embedding provider returned a non-finite vector value.")
        vectors.append(vector)
    if len({len(vector) for vector in vectors}) != 1:
        raise ValueError("Embedding provider returned vectors with different dimensions.")
    return vectors
