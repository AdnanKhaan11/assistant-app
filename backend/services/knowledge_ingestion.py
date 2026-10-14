"""Extract a local troubleshooting PDF, split it into bounded chunks, and index it."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol


MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 500
CHUNK_SIZE_CHARS = 900
CHUNK_OVERLAP_CHARS = 120


class EmbeddingPort(Protocol):
    """Describe the embedding operation required by document ingestion."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Convert input texts to vectors in matching order."""


class SemanticMemoryPort(Protocol):
    """Describe the semantic index operation required by document ingestion."""

    async def index_chunks(self, chunks: list[tuple[str, str, int]], vectors: list[list[float]]) -> int:
        """Store each source chunk alongside its vector."""


class KnowledgeIngestionService:
    """Coordinate local PDF extraction, chunking, embedding, and vector indexing."""

    def __init__(self, embedder: EmbeddingPort, semantic_memory: SemanticMemoryPort) -> None:
        """Inject embedding and semantic-memory interfaces without infrastructure globals."""
        self._embedder = embedder
        self._memory = semantic_memory

    async def index_pdf(self, pdf_path: str) -> int:
        """Index the text in `pdf_path`, returning the number of searchable chunks."""
        pages = await asyncio.to_thread(_extract_pages, Path(pdf_path))
        chunks = _chunk_pages(pages, Path(pdf_path).name)
        if not chunks:
            return 0
        vectors = await self._embedder.embed([text for text, _, _ in chunks])
        return await self._memory.index_chunks(chunks, vectors)


def _extract_pages(pdf_path: Path) -> list[tuple[str, int]]:
    """Read bounded page text from a local PDF using pypdf in a worker thread."""
    if not pdf_path.is_file() or pdf_path.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("Knowledge PDF is missing or exceeds the allowed size.")
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path), strict=True)
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError("Knowledge PDF contains too many pages.")
    return [(page.extract_text() or "", index) for index, page in enumerate(reader.pages, start=1)]


def _chunk_pages(pages: list[tuple[str, int]], source: str) -> list[tuple[str, str, int]]:
    """Split useful page text into overlapping chunks while preserving page numbers."""
    chunks: list[tuple[str, str, int]] = []
    step = CHUNK_SIZE_CHARS - CHUNK_OVERLAP_CHARS
    for text, page in pages:
        normalized = " ".join(text.split())
        for start in range(0, len(normalized), step):
            chunk = normalized[start : start + CHUNK_SIZE_CHARS].strip()
            if chunk:
                chunks.append((chunk, source, page))
    return chunks
