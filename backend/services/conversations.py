"""Apply owner checks, validation, and bounded pagination to private conversation storage."""
from __future__ import annotations

import asyncio
from uuid import uuid4

from backend.memory.conversation_store import (
    ConversationMessageRecord,
    ConversationRecord,
    ConversationStore,
)

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20
MAX_TITLE_LENGTH = 120
MAX_MESSAGE_LENGTH = 20_000


class ConversationNotFound(Exception):
    """Signal a missing resource or one that belongs to another user."""


class InvalidConversationInput(Exception):
    """Signal a title, message, or page request that fails service-level validation."""


class ConversationService:
    """Expose private conversation operations without leaking SQLite to HTTP handlers."""

    def __init__(self, store: ConversationStore) -> None:
        """Use `store` as the durable persistence boundary for conversation data."""
        self._store = store

    async def create(self, owner_id: str, title: str = "New conversation") -> ConversationRecord:
        """Create a conversation for `owner_id` and validate its user-visible `title`."""
        normalized_title = self._validate_title(title)
        return await asyncio.to_thread(self._store.create, owner_id, normalized_title, uuid4().hex)

    async def list_for_owner(
        self,
        owner_id: str,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[ConversationRecord]:
        """Return an owner-scoped page using the requested `limit` and `offset`."""
        self._validate_page(limit, offset)
        return await asyncio.to_thread(self._store.list_for_owner, owner_id, limit, offset)

    async def get(self, owner_id: str, conversation_id: str) -> ConversationRecord:
        """Return an owned conversation or raise the same error for missing/foreign IDs."""
        conversation = await asyncio.to_thread(self._store.get_for_owner, owner_id, conversation_id)
        if conversation is None:
            raise ConversationNotFound
        return conversation

    async def rename(self, owner_id: str, conversation_id: str, title: str) -> ConversationRecord:
        """Rename one owned conversation and return its updated record."""
        normalized_title = self._validate_title(title)
        changed = await asyncio.to_thread(self._store.rename, owner_id, conversation_id, normalized_title)
        if not changed:
            raise ConversationNotFound
        return await self.get(owner_id, conversation_id)

    async def delete(self, owner_id: str, conversation_id: str) -> None:
        """Delete an owned conversation and its messages, or raise `ConversationNotFound`."""
        deleted = await asyncio.to_thread(self._store.delete, owner_id, conversation_id)
        if not deleted:
            raise ConversationNotFound

    async def messages(
        self,
        owner_id: str,
        conversation_id: str,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[ConversationMessageRecord]:
        """Return a bounded oldest-first message page for an owned conversation."""
        self._validate_page(limit, offset)
        messages = await asyncio.to_thread(
            self._store.messages_for_owner, owner_id, conversation_id, limit, offset
        )
        if messages is None:
            raise ConversationNotFound
        return messages

    async def append_message(
        self,
        owner_id: str,
        conversation_id: str,
        role: str,
        content: str,
    ) -> ConversationMessageRecord:
        """Append a bounded `user` or `assistant` message to an owned conversation."""
        self._validate_message(role, content)
        message_id = uuid4().hex
        appended = await asyncio.to_thread(
            self._store.append_message,
            owner_id,
            conversation_id,
            message_id,
            role,
            content,
        )
        if not appended:
            raise ConversationNotFound
        message = await asyncio.to_thread(
            self._store.get_message_for_owner,
            owner_id,
            conversation_id,
            message_id,
        )
        if message is None:
            raise ConversationNotFound
        return message

    async def edit_user_message(
        self,
        owner_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> list[ConversationMessageRecord]:
        """Edit a user message and return history after removing every later response."""
        self._validate_message("user", content)
        edited = await asyncio.to_thread(
            self._store.edit_user_message, owner_id, conversation_id, message_id, content
        )
        if not edited:
            raise ConversationNotFound
        return await self.messages(owner_id, conversation_id)

    async def delete_message(self, owner_id: str, conversation_id: str, message_id: str) -> None:
        """Delete `message_id` and later turns for its owner, or raise `ConversationNotFound`."""
        deleted = await asyncio.to_thread(
            self._store.delete_message,
            owner_id,
            conversation_id,
            message_id,
        )
        if not deleted:
            raise ConversationNotFound

    @staticmethod
    def _validate_title(title: str) -> str:
        """Trim `title` and require between one and the configured maximum characters."""
        normalized_title = title.strip()
        if not normalized_title or len(normalized_title) > MAX_TITLE_LENGTH:
            raise InvalidConversationInput
        return normalized_title

    @staticmethod
    def _validate_message(role: str, content: str) -> None:
        """Require an allowed `role` and bounded, non-empty `content`."""
        if role not in {"user", "assistant"} or not content.strip() or len(content) > MAX_MESSAGE_LENGTH:
            raise InvalidConversationInput

    @staticmethod
    def _validate_page(limit: int, offset: int) -> None:
        """Require a page `limit` between one and 50 and a non-negative `offset`."""
        if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
            raise InvalidConversationInput
