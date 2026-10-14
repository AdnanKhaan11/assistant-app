"""FastAPI entry point: transport + wiring ONLY. Keep it thin.

Run:  uvicorn backend.main:app --reload --port 8000        (or `make run`)

Already done for you: telemetry start/stop, FastAPI + httpx auto-instrumentation, /health,
X-Trace-Id response header, and the application wiring (dependency graph).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Path as APIPath, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from qdrant_client import AsyncQdrantClient

from backend.assistant import AssistantOptions, AssistantService, AssistantStreamEvent
from backend.auth_api import router as auth_router
from backend.conversation_api import router as conversation_router
from backend.runtime_ai_api import router as runtime_ai_router
from backend.user_settings_api import router as user_settings_router
from backend.llm import LLMError, create_provider
from backend.memory.attachment_store import AttachmentStore
from backend.memory.auth_store import AuthStore
from backend.memory.conversation_store import ConversationStore
from backend.memory.long_term import LongTermMemory
from backend.memory.semantic import QdrantSemanticMemory
from backend.memory.short_term import ShortTermMemory
from backend.services.huggingface_embeddings import HuggingFaceEmbeddingClient
from backend.services.knowledge_ingestion import KnowledgeIngestionService
from backend.services.knowledge_retrieval import KnowledgeRetrievalService, KnowledgeRetrieverPort
from backend.schemas import (
    ActionResultResponse,
    AssessmentResponse,
    AttachmentResponse,
    ChatRequest,
    ConfirmRequest,
)
from backend.services.advisor import AdvisorService
from backend.services.authentication import AuthenticationService
from backend.services.classifier import ClassifierService
from backend.services.conversations import ConversationService
from backend.services.summarizer import SummarizerService
from backend.telemetry import (
    TelemetryConfig, current_trace_id, get_metrics, init_telemetry, instrument_fastapi, instrument_httpx,
    shutdown_telemetry,
)
from backend.tools.issue_lookup import IssueLookupTool
from backend.tools.network_action import NetworkActionTool
from backend.tools.network_check import NetworkCheckTool
from backend.tools.registry import ToolRegistry
from backend.utils.config import Settings, get_settings
from backend.utils.attachments import (
    AttachmentNotFound,
    AttachmentTooLarge,
    UnsupportedAttachment,
    read_upload,
    sanitize_filename,
)

logger = logging.getLogger(__name__)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def build_assistant(
    settings: Settings,
    telemetry_config: TelemetryConfig,
    knowledge_retriever: KnowledgeRetrieverPort | None = None,
) -> tuple[AssistantService, ShortTermMemory, object, AttachmentStore]:
    llm = create_provider(
        settings.llm_provider, settings.llm_model, settings.llm_api_key,
        timeout=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries, telemetry=telemetry_config,
    )
    tools = ToolRegistry()
    tools.register(IssueLookupTool(settings.tickets_path))
    tools.register(NetworkCheckTool())
    tools.register(NetworkActionTool())

    short_term = ShortTermMemory(settings.memory_window_turns)
    attachment_store = AttachmentStore(settings.db_path, settings.upload_dir)
    assistant = AssistantService(
        classifier=ClassifierService(llm), summarizer=SummarizerService(llm), advisor=AdvisorService(llm),
        tools=tools, short_term=short_term, long_term=LongTermMemory(settings.db_path),
        attachment_store=attachment_store,
        knowledge_retriever=knowledge_retriever,
        options=AssistantOptions(
            max_attachments_per_message=settings.max_attachments_per_message,
            vision_enabled=settings.llm_supports_vision,
            stream_max_seconds=settings.stream_max_seconds,
        ),
    )
    return assistant, short_term, llm, attachment_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry_config = TelemetryConfig.from_env()
    init_telemetry(telemetry_config)          # 1) telemetry FIRST so startup is observable
    instrument_httpx()                         # 2) before any httpx client is created

    settings = get_settings()
    knowledge_retriever, semantic_memory = await _initialize_knowledge(settings)
    assistant, short_term, llm, attachment_store = build_assistant(
        settings, telemetry_config, knowledge_retriever
    )
    app.state.authentication = AuthenticationService(AuthStore(settings.db_path))
    app.state.conversations = ConversationService(ConversationStore(settings.db_path))
    app.state.assistant = assistant
    app.state.attachment_store = attachment_store
    app.state.settings = settings
    get_metrics().register_gauge(
        "assistant.active_tickets", short_term.active_tickets, "Tickets currently held in short-term memory"
    )
    logger.info("assistant ready", extra={"provider": settings.llm_provider, "model": llm.model})
    yield
    if semantic_memory is not None:
        await semantic_memory.close()
    await llm.aclose()
    shutdown_telemetry()                       # flush buffered spans/metrics/logs


async def _initialize_knowledge(
    settings: Settings,
) -> tuple[KnowledgeRetrievalService | None, QdrantSemanticMemory | None]:
    """Index the configured manual at startup when both hosted services are configured."""
    if not settings.hf_token or not settings.qdrant_url:
        return None, None
    embedder = HuggingFaceEmbeddingClient(
        endpoint=settings.hf_embedding_url,
        model=settings.hf_embedding_model,
        token=settings.hf_token,
    )
    client: AsyncQdrantClient | None = None
    semantic_memory: QdrantSemanticMemory | None = None
    try:
        client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=10,
        )
        async with asyncio.timeout(60):
            probe = await embedder.embed(["IT troubleshooting manual dimension check"])
            if len(probe) != 1:
                raise ValueError("Embedding provider returned an invalid dimension probe.")
            semantic_memory = QdrantSemanticMemory(
                client,
                collection=settings.qdrant_collection,
                vector_size=len(probe[0]),
            )
            chunk_count = await KnowledgeIngestionService(embedder, semantic_memory).index_pdf(
                settings.knowledge_pdf_path
            )
        logger.info("knowledge manual indexed", extra={"chunk_count": chunk_count})
        return KnowledgeRetrievalService(embedder, semantic_memory), semantic_memory
    except Exception as error:
        logger.warning(
            "knowledge indexing unavailable; continuing without semantic retrieval",
            extra={"error_type": type(error).__name__},
        )
        if client is not None:
            await client.close()
        return None, None


app = FastAPI(title="IT Support Assistant", version="0.1.0", lifespan=lifespan)
instrument_fastapi(app)
app.include_router(auth_router)
app.include_router(conversation_router)
app.include_router(runtime_ai_router)
app.include_router(user_settings_router)


@app.middleware("http")
async def add_trace_id_header(request: Request, call_next):
    response = await call_next(request)
    if trace_id := current_trace_id():
        response.headers["X-Trace-Id"] = trace_id
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(LLMError)
async def handle_llm_error(_: Request, __: LLMError) -> JSONResponse:
    """Hide provider internals behind a stable, user-friendly gateway error."""
    return JSONResponse(
        status_code=502,
        content={"detail": "The AI provider is unavailable. Try again shortly."},
    )


@app.exception_handler(AttachmentTooLarge)
async def handle_attachment_too_large(_: Request, error: AttachmentTooLarge) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": str(error)})


@app.exception_handler(UnsupportedAttachment)
async def handle_unsupported_attachment(_: Request, error: UnsupportedAttachment) -> JSONResponse:
    return JSONResponse(status_code=415, content={"detail": str(error)})


@app.exception_handler(AttachmentNotFound)
async def handle_attachment_not_found(_: Request, __: AttachmentNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Attachment not found."})


@app.post("/api/chat", response_model=AssessmentResponse)
async def chat(req: ChatRequest, request: Request) -> AssessmentResponse:
    """Validate and forward one chat request to the assistant policy layer."""
    _validate_attachment_count(req, request)
    return await request.app.state.assistant.handle_message(req)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """Stream assistant progress and answer events as server-sent events."""
    _validate_attachment_count(req, request)
    await request.app.state.assistant.validate_attachments(req.ticket_id, req.attachment_ids)

    async def event_stream() -> AsyncIterator[str]:
        stream = request.app.state.assistant.handle_message_stream(req)
        try:
            async for event in stream:
                yield _encode_sse_event(event)
        finally:
            await stream.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/tickets/{ticket_id}/attachments", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    ticket_id: Annotated[str, APIPath(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")],
    request: Request,
    file: UploadFile = File(...),
) -> AttachmentResponse:
    """Validate and store one bounded ticket attachment."""
    settings = request.app.state.settings
    try:
        content, kind, content_type = await read_upload(
            file,
            max_bytes=settings.max_upload_bytes,
            vision_enabled=settings.llm_supports_vision,
        )
    finally:
        await file.close()
    attachment_id = uuid4().hex
    filename = sanitize_filename(file.filename)
    await asyncio.to_thread(
        request.app.state.attachment_store.save,
        ticket_id=ticket_id,
        attachment_id=attachment_id,
        filename=filename,
        content_type=content_type,
        kind=kind,
        content=content,
    )
    return AttachmentResponse(
        attachment_id=attachment_id,
        filename=filename,
        content_type=content_type,
        kind=kind,
        size_bytes=len(content),
    )


@app.post("/api/actions/{action_id}/confirm", response_model=ActionResultResponse)
async def confirm_action(action_id: str, req: ConfirmRequest, request: Request) -> ActionResultResponse:
    """Forward one approval decision and map missing pending actions to 404."""
    try:
        return await request.app.state.assistant.confirm_action(action_id, req.approved)
    except KeyError:
        raise HTTPException(status_code=404, detail="Pending action not found.") from None


@app.get("/api/tickets/{ticket_id}/history")
async def ticket_history(
    ticket_id: Annotated[str, APIPath(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")],
    request: Request,
) -> list[dict[str, str | None]]:
    """Return the assistant's durable ticket history."""
    return await request.app.state.assistant.get_ticket_history(ticket_id)


@app.delete("/api/tickets/{ticket_id}", status_code=204)
async def delete_ticket(
    ticket_id: Annotated[str, APIPath(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")],
    request: Request,
) -> Response:
    """Clear both persistent and in-memory state for a ticket."""
    await request.app.state.assistant.clear_ticket(ticket_id)
    return Response(status_code=204)


def _validate_attachment_count(req: ChatRequest, request: Request) -> None:
    limit = request.app.state.settings.max_attachments_per_message
    if len(req.attachment_ids) > limit:
        raise HTTPException(status_code=422, detail="Too many attachments for one message.")


def _encode_sse_event(event: AssistantStreamEvent) -> str:
    payload = json.dumps(event.data, separators=(",", ":"))
    return f"event: {event.name}\ndata: {payload}\n\n"


if FRONTEND_DIR.exists():  # must stay LAST: it catches every remaining path
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
