"""Test embedding, semantic-index, and PDF-ingestion behavior without network access."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from backend.memory.semantic import QdrantSemanticMemory, SemanticMatch
from backend.services.huggingface_embeddings import HuggingFaceEmbeddingClient
from backend.services.knowledge_ingestion import KnowledgeIngestionService, _chunk_pages
from backend.services.knowledge_retrieval import KnowledgeRetrievalService
from backend.utils.config import Settings


@pytest.mark.asyncio
async def test_huggingface_embedding_posts_inputs_and_returns_vectors() -> None:
    """Send model input through MockTransport and parse finite float vectors."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Return deterministic vectors while retaining the outgoing request."""
        requests.append(request)
        return httpx.Response(200, json=[[0.1, 0.2], [0.3, 0.4]])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = HuggingFaceEmbeddingClient(
            endpoint="https://example.test/models",
            model="org/model",
            token="test-token",
            client=client,
        )
        vectors = await embedder.embed(["one", "two"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert str(requests[0].url) == "https://example.test/models/org/model"
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[0].read() == b'{"inputs":["one","two"],"options":{"wait_for_model":true}}'


@pytest.mark.asyncio
async def test_huggingface_embedding_rejects_bad_vector_shapes() -> None:
    """Reject malformed or inconsistent vectors before returning them to callers."""
    for payload in ([[1.0], [1.0, 2.0]], [[float("nan")]], {"error": "bad request"}):
        async def handler(request: httpx.Request, result: object = payload) -> httpx.Response:
            """Return the configured malformed response without network access."""
            return httpx.Response(200, json=result)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            embedder = HuggingFaceEmbeddingClient(
                endpoint="https://example.test",
                model="model",
                token=None,
                client=client,
            )
            with pytest.raises(ValueError):
                await embedder.embed(["hello"] * (len(payload) if isinstance(payload, list) else 1))


class FakeQdrant:
    """Capture Qdrant calls while returning deterministic points for tests."""

    def __init__(self) -> None:
        """Initialize empty fake collection state and query results."""
        self.exists = False
        self.created: list[dict[str, object]] = []
        self.points: list[object] = []
        self.upserted: list[object] = []
        self.query_limit = 0

    async def collection_exists(self, name: str) -> bool:
        """Return whether the fake named collection was already created."""
        return self.exists

    async def create_collection(self, **kwargs: object) -> None:
        """Record collection creation and mark the fake collection available."""
        self.created.append(kwargs)
        self.exists = True

    async def upsert(self, **kwargs: object) -> None:
        """Capture points written to the fake collection."""
        self.upserted.extend(kwargs["points"])

    async def query_points(self, **kwargs: object) -> SimpleNamespace:
        """Capture the requested limit and return configured points."""
        self.query_limit = int(kwargs["limit"])
        return SimpleNamespace(points=self.points)

    async def close(self) -> None:
        """Satisfy the client close interface."""


@pytest.mark.asyncio
async def test_qdrant_indexes_stably_and_caps_validated_search_results() -> None:
    """Create the collection once, use stable point IDs, and bound search results."""
    client = FakeQdrant()
    memory = QdrantSemanticMemory(client, collection="knowledge", vector_size=2)  # type: ignore[arg-type]
    chunks = [("router reset", "guide.pdf", 2)]

    assert await memory.index_chunks(chunks, [[0.2, 0.8]]) == 1
    first_id = client.upserted[0].id
    assert await memory.index_chunks(chunks, [[0.2, 0.8]]) == 1
    assert client.upserted[1].id == first_id
    client.points = [
        SimpleNamespace(payload={"text": "router reset", "source": "guide.pdf", "page": 2}, score=0.9),
        SimpleNamespace(payload={"text": "invalid", "source": "guide.pdf"}, score=0.8),
    ]

    matches = await memory.search([0.2, 0.8], limit=100)
    assert client.created[0]["collection_name"] == "knowledge"
    assert client.query_limit == 10
    assert [(match.text, match.page) for match in matches] == [("router reset", 2)]


@pytest.mark.asyncio
async def test_qdrant_rejects_wrong_vector_size_without_writing() -> None:
    """Validate dimensions locally before creating collections or upserting points."""
    client = FakeQdrant()
    memory = QdrantSemanticMemory(client, collection="knowledge", vector_size=2)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="exactly 2"):
        await memory.index_chunks([("text", "guide.pdf", 1)], [[0.1]])
    assert client.created == []
    assert client.upserted == []


class FakeEmbedder:
    """Return predictable vectors and retain the exact texts sent for embedding."""

    def __init__(self) -> None:
        """Create a list for recording embedding inputs."""
        self.inputs: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one two-dimensional vector per requested text."""
        self.inputs = texts
        return [[0.1, 0.2] for _ in texts]


class FakeSemanticMemory:
    """Capture chunk and vector pairs passed to semantic storage."""

    def __init__(self) -> None:
        """Initialize empty captured chunks."""
        self.chunks: list[tuple[str, str, int]] = []

    async def index_chunks(self, chunks: list[tuple[str, str, int]], vectors: list[list[float]]) -> int:
        """Capture chunks and return the number indexed."""
        self.chunks = chunks
        assert len(chunks) == len(vectors)
        return len(chunks)


@pytest.mark.asyncio
async def test_pdf_ingestion_extracts_and_indexes_page_chunks(tmp_path, monkeypatch) -> None:
    """Read PDF pages through the lazy parser import and index overlapping chunks."""
    pdf_path = tmp_path / "guide.pdf"
    pdf_path.write_bytes(b"local pdf fixture")
    fake_reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "Help " * 240)])
    pypdf_module = ModuleType("pypdf")
    pypdf_module.PdfReader = lambda path, strict: fake_reader  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pypdf", pypdf_module)
    embedder = FakeEmbedder()
    memory = FakeSemanticMemory()

    count = await KnowledgeIngestionService(embedder, memory).index_pdf(str(pdf_path))

    assert count == len(memory.chunks) > 1
    assert embedder.inputs == [chunk[0] for chunk in memory.chunks]
    assert {chunk[1:] for chunk in memory.chunks} == {("guide.pdf", 1)}
    assert all(len(chunk[0]) <= 900 for chunk in memory.chunks)


def test_chunker_skips_blank_pages_and_preserves_source_page() -> None:
    """Avoid indexing blank pages and preserve page metadata for useful chunks."""
    chunks = _chunk_pages([("  ", 1), ("Fix the DNS settings.", 4)], "manual.pdf")

    assert chunks == [("Fix the DNS settings.", "manual.pdf", 4)]


class FakeQueryEmbedder:
    """Capture a search query and return one deterministic vector."""

    def __init__(self) -> None:
        """Create storage for the latest query text."""
        self.query = ""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a single vector for the query."""
        self.query = texts[0]
        return [[0.3, 0.7]]


class FakeSearchMemory:
    """Capture semantic search arguments and return configured matches."""

    def __init__(self) -> None:
        """Initialize search-call fields and an empty result set."""
        self.vector: list[float] = []
        self.limit = 0
        self.matches: list[SemanticMatch] = []

    async def search(self, vector: list[float], *, limit: int = 4) -> list[SemanticMatch]:
        """Return configured matches while recording the bounded search request."""
        self.vector = vector
        self.limit = limit
        return self.matches


@pytest.mark.asyncio
async def test_knowledge_retrieval_embeds_query_and_formats_bounded_source_matches() -> None:
    """Connect query embedding to semantic search and include page attribution."""
    embedder = FakeQueryEmbedder()
    memory = FakeSearchMemory()
    memory.matches = [SemanticMatch("Flush DNS cache", "playbook.pdf", 8, 0.91)]
    retrieval = KnowledgeRetrievalService(embedder, memory)  # type: ignore[arg-type]

    excerpts = await retrieval.retrieve("DNS fails", limit=20)

    assert embedder.query == "DNS fails"
    assert memory.vector == [0.3, 0.7]
    assert memory.limit == 4
    assert excerpts == ["[Source: playbook.pdf, page 8]\nFlush DNS cache"]


@pytest.mark.asyncio
async def test_knowledge_initialization_stays_disabled_without_both_credentials() -> None:
    """Skip optional semantic infrastructure without making any external connection."""
    from backend.main import _initialize_knowledge

    retriever, memory = await _initialize_knowledge(Settings(hf_token=None, qdrant_url=None))

    assert retriever is None
    assert memory is None
