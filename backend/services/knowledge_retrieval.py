"""Turn a user's question into a bounded set of validated manual excerpts."""

from __future__ import annotations

from typing import Protocol

from backend.memory.semantic import SemanticMatch


class EmbedderPort(Protocol):
    """Define the small embedding interface used for question search."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector for each supplied text."""


class SemanticSearchPort(Protocol):
    """Define semantic lookup without exposing Qdrant to assistant policy."""

    async def search(self, vector: list[float], *, limit: int = 4) -> list[SemanticMatch]:
        """Return a bounded list of validated reference matches."""


class KnowledgeRetrieverPort(Protocol):
    """Expose only the retrieval operation required by assistant policy."""

    async def retrieve(self, query: str, *, limit: int = 4) -> list[str]:
        """Return bounded reference excerpts for a query."""


class KnowledgeRetrievalService:
    """Compose question embedding with semantic lookup for relevant manual text."""

    def __init__(self, embedder: EmbedderPort, semantic_memory: SemanticSearchPort) -> None:
        """Inject the embedding and semantic-search adapters for this service."""
        self._embedder = embedder
        self._memory = semantic_memory

    async def retrieve(self, query: str, *, limit: int = 4) -> list[str]:
        """Return relevant excerpts, or an empty list when the query has no text."""
        if not query.strip() or limit <= 0:
            return []
        vectors = await self._embedder.embed([query])
        if len(vectors) != 1:
            raise ValueError("Query embedding provider returned an unexpected number of vectors.")
        matches = await self._memory.search(vectors[0], limit=min(limit, 4))
        return [_format_match(match) for match in matches]


def _format_match(match: SemanticMatch) -> str:
    """Keep source location visible while marking indexed text as untrusted data."""
    return f"[Source: {match.source}, page {match.page}]\n{match.text}"
