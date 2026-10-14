"""Expose safe, administrator-only runtime AI configuration operations."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend.auth_api import require_admin, validate_csrf
from backend.llm.registry import PROVIDERS
from backend.memory.auth_store import StoredAuthUser
from backend.memory.runtime_config_store import RuntimeAIConfigRecord, RuntimeAIConfigStore
from backend.services.runtime_configuration import (
    ProviderModelOption,
    RuntimeAIStatus,
    RuntimeAIValidation,
    RuntimeConfigurationRejected,
    RuntimeConfigurationService,
)

router = APIRouter(prefix="/api/auth/admin/ai-config", tags=["administrator configuration"])


class RuntimeAIConfigRequest(BaseModel):
    """Reject unknown fields and constrain administrator-selected provider/model names."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=160)


class ProviderOptionResponse(BaseModel):
    """Expose only supported provider names and model IDs."""

    provider: str
    models: list[str]

    @classmethod
    def from_option(cls, option: ProviderModelOption) -> ProviderOptionResponse:
        """Convert a provider registry entry to its public response fields."""
        return cls(provider=option.provider, models=list(option.models))


class RuntimeAIConfigResponse(BaseModel):
    """Show a saved non-secret provider/model configuration and timestamp."""

    provider: str
    model: str
    updated_at: str

    @classmethod
    def from_record(cls, record: RuntimeAIConfigRecord) -> RuntimeAIConfigResponse:
        """Convert persisted non-secret configuration into a safe response."""
        return cls(provider=record.provider, model=record.model, updated_at=record.updated_at)


class RuntimeAIStatusResponse(BaseModel):
    """Keep active startup settings distinct from administrator-saved settings."""

    active_provider: str
    active_model: str
    saved: RuntimeAIConfigResponse | None
    providers: list[ProviderOptionResponse]

    @classmethod
    def from_status(cls, result: RuntimeAIStatus) -> RuntimeAIStatusResponse:
        """Convert active, saved, and supported settings without exposing credentials."""
        saved = RuntimeAIConfigResponse.from_record(result.saved) if result.saved else None
        return cls(
            active_provider=result.active_provider,
            active_model=result.active_model,
            saved=saved,
            providers=[ProviderOptionResponse.from_option(item) for item in result.providers],
        )


class RuntimeAIValidationResponse(BaseModel):
    """Return safe validation status for a selected provider and model."""

    is_valid: bool
    provider: str
    model: str
    message: str

    @classmethod
    def from_validation(cls, result: RuntimeAIValidation) -> RuntimeAIValidationResponse:
        """Convert validation outcome while excluding internal provider errors."""
        return cls(
            is_valid=result.is_valid,
            provider=result.provider,
            model=result.model,
            message=result.message,
        )


class EmbeddingStatusResponse(BaseModel):
    """Show safe embedding setup details without claiming remote services are healthy."""

    provider: str
    model: str
    is_provider_configured: bool
    vector_store: str
    is_vector_store_configured: bool
    is_manual_available: bool
    vector_dimension: int | None
    note: str


class AdminSystemStatusResponse(BaseModel):
    """Expose safe runtime readiness and configured resource limits to administrators."""

    database_status: Literal["available", "unavailable"]
    active_chat_provider: str
    active_chat_model: str
    is_chat_provider_configured: bool
    max_upload_bytes: int
    max_attachments_per_message: int
    stream_max_seconds: int
    memory_window_turns: int
    vision_enabled: bool
    semantic_retrieval_configured: bool
    note: str


@router.get("", response_model=RuntimeAIStatusResponse)
async def get_runtime_ai_status(
    request: Request,
    user: Annotated[StoredAuthUser, Depends(require_admin)],
) -> RuntimeAIStatusResponse:
    """Return active and saved model configuration to an authenticated administrator."""
    result = await (await _configuration_service(request)).status()
    return RuntimeAIStatusResponse.from_status(result)


@router.get("/embeddings", response_model=EmbeddingStatusResponse)
async def get_embedding_status(
    request: Request,
    user: Annotated[StoredAuthUser, Depends(require_admin)],
) -> EmbeddingStatusResponse:
    """Report configured embedding components without exposing secrets or probing services."""
    settings = request.app.state.settings
    manual_available = await asyncio.to_thread(Path(settings.knowledge_pdf_path).is_file)
    return EmbeddingStatusResponse(
        provider="huggingface",
        model=settings.hf_embedding_model,
        is_provider_configured=bool(settings.hf_token),
        vector_store="qdrant",
        is_vector_store_configured=bool(settings.qdrant_url),
        is_manual_available=manual_available,
        vector_dimension=None,
        note="This reports local configuration only; service connectivity and indexed vector dimensions are not tracked.",
    )


@router.get("/system-status", response_model=AdminSystemStatusResponse)
async def get_admin_system_status(
    request: Request,
    user: Annotated[StoredAuthUser, Depends(require_admin)],
) -> AdminSystemStatusResponse:
    """Return safe application readiness details without exposing paths or credentials."""
    settings = request.app.state.settings
    provider = settings.llm_provider.lower()
    provider_spec = PROVIDERS.get(provider)
    provider_configured = provider_spec is not None and (
        provider_spec.key_env is None or bool(settings.llm_api_key)
    )
    database_available = await asyncio.to_thread(_database_available, settings.db_path)
    model = settings.llm_model or (provider_spec.default_model if provider_spec else "unknown")
    return AdminSystemStatusResponse(
        database_status="available" if database_available else "unavailable",
        active_chat_provider=provider,
        active_chat_model=model,
        is_chat_provider_configured=provider_configured,
        max_upload_bytes=settings.max_upload_bytes,
        max_attachments_per_message=settings.max_attachments_per_message,
        stream_max_seconds=settings.stream_max_seconds,
        memory_window_turns=settings.memory_window_turns,
        vision_enabled=settings.llm_supports_vision,
        semantic_retrieval_configured=bool(settings.hf_token and settings.qdrant_url),
        note="Provider status shows local configuration only; external provider connectivity is not checked.",
    )


@router.post("/validate", response_model=RuntimeAIValidationResponse)
async def validate_runtime_ai_config(
    body: RuntimeAIConfigRequest,
    request: Request,
    user: Annotated[StoredAuthUser, Depends(require_admin)],
) -> RuntimeAIValidationResponse:
    """Validate a supported provider/model choice without saving or activating it."""
    validate_csrf(request)
    result = await (await _configuration_service(request)).validate(body.provider, body.model)
    return RuntimeAIValidationResponse.from_validation(result)


@router.put("", response_model=RuntimeAIConfigResponse)
async def save_runtime_ai_config(
    body: RuntimeAIConfigRequest,
    request: Request,
    user: Annotated[StoredAuthUser, Depends(require_admin)],
) -> RuntimeAIConfigResponse:
    """Validate and persist settings without switching the active provider mid-request."""
    validate_csrf(request)
    try:
        record = await (await _configuration_service(request)).save(
            body.provider,
            body.model,
            user.user_id,
        )
    except RuntimeConfigurationRejected as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from None
    return RuntimeAIConfigResponse.from_record(record)


async def _configuration_service(request: Request) -> RuntimeConfigurationService:
    """Return an injected service or lazily build one from application settings and SQLite."""
    service = getattr(request.app.state, "runtime_configuration", None)
    if service is not None:
        return service
    settings = request.app.state.settings
    store = await asyncio.to_thread(RuntimeAIConfigStore, settings.db_path)
    service = RuntimeConfigurationService(settings, store)
    request.app.state.runtime_configuration = service
    return service


def _database_available(database_path: str) -> bool:
    """Check an existing SQLite database with a read-only query without creating files."""
    path = Path(database_path)
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1.0) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
            return result == ("ok",)
    except (OSError, sqlite3.Error):
        return False
