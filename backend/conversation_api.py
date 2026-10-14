"""Define private conversation routes; identity comes from auth cookies and data from SQLite."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path as APIPath, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.auth_api import current_user, validate_csrf
from backend.llm import Message
from backend.memory.auth_store import StoredAuthUser
from backend.memory.conversation_store import ConversationMessageRecord, ConversationRecord
from backend.schemas import ActionResultResponse, AssessmentResponse, ChatRequest, ConfirmRequest
from backend.services.conversations import (
    ConversationNotFound,
    ConversationService,
    InvalidConversationInput,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])
_RESOURCE_ID_PATTERN = r"^[a-f0-9]{32}$"


class ConversationCreateRequest(BaseModel):
    """Validate the optional title for a new private conversation."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="New conversation", min_length=1, max_length=120)


class ConversationRenameRequest(BaseModel):
    """Validate the replacement title for an owned conversation."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)


class MessageEditRequest(BaseModel):
    """Validate replacement text for an owned user message."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=20_000)


class ConversationChatRequest(BaseModel):
    """Validate bounded `message` text sent to one private conversation."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4_000)


class ConversationResponse(BaseModel):
    """Expose conversation metadata without internal ownership or database fields."""

    conversation_id: str
    title: str
    created_at: str
    updated_at: str

    @classmethod
    def from_record(cls, record: ConversationRecord) -> ConversationResponse:
        """Convert an internal `record` to the public conversation representation."""
        return cls(
            conversation_id=record.conversation_id,
            title=record.title,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class ConversationMessageResponse(BaseModel):
    """Expose message text and timestamps without its database row ordering value."""

    message_id: str
    role: str
    content: str
    created_at: str
    updated_at: str

    @classmethod
    def from_record(cls, record: ConversationMessageRecord) -> ConversationMessageResponse:
        """Convert an internal `record` into a public message representation."""
        return cls(
            message_id=record.message_id,
            role=record.role,
            content=record.content,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreateRequest,
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> ConversationResponse:
    """Create a conversation owned by the authenticated user after CSRF validation."""
    validate_csrf(request)
    try:
        record = await _service(request).create(user.user_id, body.title)
    except InvalidConversationInput:
        raise _invalid_input() from None
    return ConversationResponse.from_record(record)


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConversationResponse]:
    """Return one bounded page of the authenticated user's conversations."""
    records = await _service(request).list_for_owner(user.user_id, limit, offset)
    return [ConversationResponse.from_record(record) for record in records]


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> ConversationResponse:
    """Return one conversation only when it belongs to the authenticated user."""
    try:
        record = await _service(request).get(user.user_id, conversation_id)
    except ConversationNotFound:
        raise _not_found() from None
    return ConversationResponse.from_record(record)


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def rename_conversation(
    conversation_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    body: ConversationRenameRequest,
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> ConversationResponse:
    """Rename an owned conversation after checking the request's CSRF proof."""
    validate_csrf(request)
    try:
        record = await _service(request).rename(user.user_id, conversation_id, body.title)
    except ConversationNotFound:
        raise _not_found() from None
    except InvalidConversationInput:
        raise _invalid_input() from None
    return ConversationResponse.from_record(record)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> Response:
    """Delete an owned conversation and its messages after CSRF validation."""
    validate_csrf(request)
    try:
        await _service(request).delete(user.user_id, conversation_id)
    except ConversationNotFound:
        raise _not_found() from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[ConversationMessageResponse])
async def list_messages(
    conversation_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConversationMessageResponse]:
    """Return an oldest-first bounded page of messages from an owned conversation."""
    try:
        records = await _service(request).messages(user.user_id, conversation_id, limit, offset)
    except ConversationNotFound:
        raise _not_found() from None
    return [ConversationMessageResponse.from_record(record) for record in records]


@router.patch("/{conversation_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def edit_user_message(
    conversation_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    message_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    body: MessageEditRequest,
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> Response:
    """Edit an owned user message and remove all later messages to prevent stale replies."""
    validate_csrf(request)
    try:
        await _service(request).edit_user_message(
            user.user_id, conversation_id, message_id, body.content
        )
    except ConversationNotFound:
        raise _not_found() from None
    except InvalidConversationInput:
        raise _invalid_input() from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{conversation_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation_message(
    conversation_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    message_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> Response:
    """Delete `message_id` and following replies for `user` after validating CSRF on `request`."""
    validate_csrf(request)
    try:
        await _service(request).delete_message(user.user_id, conversation_id, message_id)
    except ConversationNotFound:
        raise _not_found() from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/chat", response_model=AssessmentResponse)
async def chat_in_conversation(
    conversation_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    body: ConversationChatRequest,
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> AssessmentResponse:
    """Use `conversation_id`, `body`, `request`, and authenticated `user` to append and answer a private turn."""
    validate_csrf(request)
    service = _service(request)
    try:
        records = await service.messages(user.user_id, conversation_id, limit=50)
        await service.append_message(user.user_id, conversation_id, "user", body.message)
    except ConversationNotFound:
        raise _not_found() from None
    prior_messages = [Message(record.role, record.content) for record in records]
    assessment = await request.app.state.assistant.handle_conversation_message(
        ChatRequest(ticket_id=conversation_id, message=body.message),
        prior_messages,
    )
    try:
        await service.append_message(user.user_id, conversation_id, "assistant", assessment.answer)
    except ConversationNotFound:
        raise _not_found() from None
    return assessment


@router.post(
    "/{conversation_id}/actions/{action_id}/confirm",
    response_model=ActionResultResponse,
)
async def confirm_conversation_action(
    conversation_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    action_id: Annotated[str, APIPath(pattern=_RESOURCE_ID_PATTERN)],
    body: ConfirmRequest,
    request: Request,
    user: Annotated[StoredAuthUser, Depends(current_user)],
) -> ActionResultResponse:
    """Use `conversation_id`, `action_id`, `body`, `request`, and `user` to confirm an owned pending action."""
    validate_csrf(request)
    try:
        await _service(request).get(user.user_id, conversation_id)
        return await request.app.state.assistant.confirm_action(
            action_id,
            body.approved,
            expected_ticket_id=conversation_id,
        )
    except (ConversationNotFound, KeyError):
        raise HTTPException(status_code=404, detail="Pending action not found.") from None


def _service(request: Request) -> ConversationService:
    """Read the startup-created conversation service from this FastAPI app."""
    return request.app.state.conversations


def _not_found() -> HTTPException:
    """Create one non-disclosing 404 for missing and foreign-owned records."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation or message not found.")


def _invalid_input() -> HTTPException:
    """Create the shared 422 response for service-level conversation validation failures."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid conversation input.")
