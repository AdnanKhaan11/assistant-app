"""Store and retrieve validated text vectors in Qdrant as a rebuildable search index."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models


@dataclass(frozen=True)
class SemanticMatch:
    """Represent a bounded, validated semantic-search result."""

    text: str
    source: str
    page: int
    score: float


class QdrantSemanticMemory:
    """Keep Qdrant operations behind a small asynchronous memory interface."""

    def __init__(
        self,
        client: AsyncQdrantClient,
        *,
        collection: str,
        vector_size: int,
    ) -> None:
        """Bind a Qdrant client to one named collection and vector dimension."""
        if vector_size <= 0:
            raise ValueError("Vector size must be positive.")
        self._client = client
        self._collection = collection
        self._vector_size = vector_size

    async def index_chunks(
        self,
        chunks: list[tuple[str, str, int]],
        vectors: list[list[float]],
    ) -> int:
        """Upsert `(text, source, page)` chunks with stable IDs for safe re-indexing."""
        if len(chunks) != len(vectors):
            raise ValueError("Each chunk must have exactly one vector.")
        if not chunks:
            return 0
        self._validate_vectors(vectors)
        if not await self._client.collection_exists(self._collection):
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=models.VectorParams(size=self._vector_size, distance=models.Distance.COSINE),
            )
        points = [self._point(chunk, vector) for chunk, vector in zip(chunks, vectors, strict=True)]
        await self._client.upsert(collection_name=self._collection, points=points, wait=True)
        return len(points)

    async def search(self, vector: list[float], *, limit: int = 4) -> list[SemanticMatch]:
        """Return up to `limit` results, dropping malformed stored payloads."""
        if limit <= 0:
            return []
        self._validate_vectors([vector])
        if not await self._client.collection_exists(self._collection):
            return []
        response = await self._client.query_points(
            collection_name=self._collection,
            query=vector,
            limit=min(limit, 10),
            with_payload=True,
        )
        return [match for point in response.points if (match := _validated_match(point)) is not None]

    async def close(self) -> None:
        """Close the Qdrant client when its owning application shuts down."""
        await self._client.close()

    def _validate_vectors(self, vectors: list[list[float]]) -> None:
        """Reject empty or wrong-sized vectors before making a Qdrant request."""
        if any(len(vector) != self._vector_size for vector in vectors):
            raise ValueError(f"Vectors must contain exactly {self._vector_size} values.")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for vector in vectors
            for value in vector
        ):
            raise ValueError("Vectors must contain only finite numeric values.")

    @staticmethod
    def _point(chunk: tuple[str, str, int], vector: list[float]) -> models.PointStruct:
        """Build a stable Qdrant point from source, page, and text content."""
        text, source, page = chunk
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:{page}:{text}")
        return models.PointStruct(
            id=str(identity),
            vector=vector,
            payload={"text": text, "source": source, "page": page},
        )


def _validated_match(point: Any) -> SemanticMatch | None:
    """Build a result only when its payload has the expected safe fields."""
    payload = getattr(point, "payload", None)
    text = payload.get("text") if isinstance(payload, dict) else None
    source = payload.get("source") if isinstance(payload, dict) else None
    page = payload.get("page") if isinstance(payload, dict) else None
    score = getattr(point, "score", None)
    if not isinstance(text, str) or not text.strip() or not isinstance(source, str):
        return None
    if not isinstance(page, int) or isinstance(page, bool) or not isinstance(score, (int, float)):
        return None
    return SemanticMatch(text=text, source=source, page=page, score=float(score))
