"""Canonical request/response models (Pydantic v2). Boilerplate is given; extend as you need."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Category(str, Enum):
    NETWORK = "network_issue"
    SOFTWARE = "software_issue"
    HARDWARE = "hardware_issue"
    UNKNOWN = "unknown"


class Classification(BaseModel):
    category: Category = Category.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_action: bool = False
    keywords: list[str] = Field(default_factory=list)


class ToolFinding(BaseModel):
    tool: str
    status: Literal["ok", "error"]
    details: str


class PendingAction(BaseModel):
    action_id: str
    ticket_id: str
    tool: str
    description: str


class ChatRequest(BaseModel):
    ticket_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(min_length=1, max_length=4000)
    attachment_ids: list[Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]] = Field(default_factory=list)


class AttachmentResponse(BaseModel):
    attachment_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    filename: str
    content_type: str
    kind: Literal["image", "text"]
    size_bytes: int = Field(ge=0)


class AssessmentResponse(BaseModel):
    ticket_id: str
    category: Category
    confidence: float
    summary: str
    recommendations: list[str]
    findings: list[ToolFinding] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    answer: str
    trace_id: str | None = None  # quote this to support: it finds the exact trace in Jaeger


class ConfirmRequest(BaseModel):
    approved: bool


class ActionResultResponse(BaseModel):
    action_id: str
    status: Literal["executed", "denied", "failed"]
    message: str
    trace_id: str | None = None
