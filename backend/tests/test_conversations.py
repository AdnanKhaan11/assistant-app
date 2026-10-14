"""Test private conversation persistence, ownership isolation, and bounded message history."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.memory.auth_store import AuthStore
from backend.memory.conversation_store import ConversationStore
from backend.services.conversations import (
    ConversationNotFound,
    ConversationService,
    InvalidConversationInput,
)


@pytest.fixture
def conversation_service(tmp_path: Path) -> tuple[ConversationService, str, str]:
    """Provide an isolated conversation service with two registered owner identities."""
    store = ConversationStore(tmp_path / "assistant.db")
    users = AuthStore(tmp_path / "assistant.db")
    first_user = users.create_user("first@example.test", "hash")
    second_user = users.create_user("second@example.test", "hash")
    return ConversationService(store), first_user.user_id, second_user.user_id


@pytest.mark.asyncio
async def test_conversation_crud_is_private_to_its_owner(
    conversation_service: tuple[ConversationService, str, str],
) -> None:
    """Check create, list, rename, and delete never reveal a foreign conversation."""
    service, owner_a, owner_b = conversation_service
    first = await service.create(owner_a, "  Network help  ")

    assert first.title == "Network help"
    assert await service.list_for_owner(owner_a) == [first]
    assert await service.list_for_owner(owner_b) == []
    with pytest.raises(ConversationNotFound):
        await service.get(owner_b, first.conversation_id)
    renamed = await service.rename(owner_a, first.conversation_id, "Updated")
    assert renamed.title == "Updated"
    with pytest.raises(ConversationNotFound):
        await service.delete(owner_b, first.conversation_id)
    await service.delete(owner_a, first.conversation_id)
    assert await service.list_for_owner(owner_a) == []


@pytest.mark.asyncio
async def test_messages_are_owner_scoped_paginated_and_edit_truncates_following(
    conversation_service: tuple[ConversationService, str, str],
) -> None:
    """Check message pages are private and editing a user turn removes all later turns."""
    service, owner_a, owner_b = conversation_service
    conversation = await service.create(owner_a)
    first = await service.append_message(
        owner_a, conversation.conversation_id, "user", "first question"
    )
    await service.append_message(owner_a, conversation.conversation_id, "assistant", "first answer")
    second = await service.append_message(
        owner_a, conversation.conversation_id, "user", "second question"
    )
    await service.append_message(owner_a, conversation.conversation_id, "assistant", "second answer")

    assert [message.content for message in await service.messages(
        owner_a, conversation.conversation_id, limit=2
    )] == ["first question", "first answer"]
    with pytest.raises(ConversationNotFound):
        await service.messages(owner_b, conversation.conversation_id)
    remaining = await service.edit_user_message(
        owner_a, conversation.conversation_id, second.message_id, "edited question"
    )
    assert [message.content for message in remaining] == [
        "first question",
        "first answer",
        "edited question",
    ]
    assert first.message_id == remaining[0].message_id


@pytest.mark.asyncio
async def test_conversation_validation_and_delete_cascades_messages(
    conversation_service: tuple[ConversationService, str, str],
) -> None:
    """Check invalid titles, roles, pagination, and message cleanup on conversation deletion."""
    service, owner_a, _ = conversation_service
    with pytest.raises(InvalidConversationInput):
        await service.create(owner_a, "  ")
    conversation = await service.create(owner_a)
    with pytest.raises(InvalidConversationInput):
        await service.append_message(owner_a, conversation.conversation_id, "system", "no")
    with pytest.raises(InvalidConversationInput):
        await service.list_for_owner(owner_a, limit=51)
    await service.append_message(owner_a, conversation.conversation_id, "user", "hello")

    await service.delete(owner_a, conversation.conversation_id)

    with pytest.raises(ConversationNotFound):
        await service.messages(owner_a, conversation.conversation_id)


@pytest.mark.asyncio
async def test_delete_message_removes_selected_turn_and_all_later_turns(
    conversation_service: tuple[ConversationService, str, str],
) -> None:
    """Delete `message_id` and verify preceding history remains while later replies disappear."""
    service, owner_a, owner_b = conversation_service
    conversation = await service.create(owner_a)
    first = await service.append_message(owner_a, conversation.conversation_id, "user", "keep this")
    selected = await service.append_message(owner_a, conversation.conversation_id, "assistant", "remove this")
    await service.append_message(owner_a, conversation.conversation_id, "user", "also remove this")

    await service.delete_message(owner_a, conversation.conversation_id, selected.message_id)

    remaining = await service.messages(owner_a, conversation.conversation_id)
    assert [message.content for message in remaining] == ["keep this"]
    assert remaining[0].message_id == first.message_id
    with pytest.raises(ConversationNotFound):
        await service.delete_message(owner_b, conversation.conversation_id, selected.message_id)
