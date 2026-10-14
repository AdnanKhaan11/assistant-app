"""Test private conversation chat, history isolation, and action ownership boundaries."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.auth_api import CSRF_COOKIE
from backend.main import app
from backend.memory.auth_store import AuthStore
from backend.memory.conversation_store import ConversationStore
from backend.memory.long_term import LongTermMemory
from backend.schemas import Category
from backend.services.authentication import AuthenticationService
from backend.services.conversations import ConversationService
from backend.tests.fakes import FakeProvider
from backend.tests.test_assistant_flow import _make_assistant, _patch_tool_delays, _write_tickets


def _login(client: TestClient, email: str) -> tuple[str, str]:
    """Register `email` through `client`, then return the owner's ID and CSRF token."""
    password = "a sufficiently strong password"
    assert client.post("/api/auth/register", json={"email": email, "password": password}).status_code == 201
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    return response.json()["user"]["user_id"], client.cookies[CSRF_COOKIE]


def _create_conversation(client: TestClient, csrf_token: str) -> str:
    """Use `client` and `csrf_token` to create an owned conversation and return its ID."""
    response = client.post("/api/conversations", json={}, headers={"X-CSRF-Token": csrf_token})
    assert response.status_code == 201
    return response.json()["conversation_id"]


def test_chat_uses_private_history_without_writing_legacy_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    telemetry_capture: object,
) -> None:
    """Use `tmp_path`, `monkeypatch`, and telemetry capture to check private history and persistence."""
    database_path = tmp_path / "assistant.db"
    conversations = ConversationService(ConversationStore(database_path))
    monkeypatch.setattr(app.state, "authentication", AuthenticationService(AuthStore(database_path)), raising=False)
    monkeypatch.setattr(app.state, "conversations", conversations, raising=False)
    client = TestClient(app, base_url="https://testserver")
    owner_id, csrf_token = _login(client, "private@example.test")
    conversation_id = _create_conversation(client, csrf_token)
    asyncio.run(conversations.append_message(owner_id, conversation_id, "user", "The app opened slowly"))
    asyncio.run(conversations.append_message(owner_id, conversation_id, "assistant", "When did this start?"))
    tickets_path = tmp_path / "tickets.json"
    _write_tickets(tickets_path)
    provider = FakeProvider(
        [
            json.dumps({"category": "software_issue", "confidence": 0.8}),
            "The app opened slowly; no repair was attempted; startup remains slow.",
            json.dumps(
                {
                    "recommendations": ["Note which app is affected.", "Record when the delay occurs."],
                    "answer": "When did the slow startup begin?",
                }
            ),
        ]
    )
    assistant, _, _, _ = _make_assistant(
        database_path,
        tickets_path,
        category=Category.SOFTWARE,
        needs_action=False,
        provider=provider,
    )
    monkeypatch.setattr(app.state, "assistant", assistant, raising=False)

    response = client.post(
        f"/api/conversations/{conversation_id}/chat",
        json={"message": "It still takes a long time"},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    assert [message.content for message in provider.calls[0].messages[-3:]] == [
        "The app opened slowly",
        "When did this start?",
        "It still takes a long time",
    ]
    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    assert [message["role"] for message in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[-1]["content"] == "When did the slow startup begin?"
    assert LongTermMemory(str(database_path)).recent(conversation_id) == []


def test_action_confirmation_is_scoped_to_owner_and_conversation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    telemetry_capture: object,
) -> None:
    """Use `tmp_path` and `monkeypatch` to check owner scope and one-time action confirmation."""
    _patch_tool_delays(monkeypatch)
    database_path = tmp_path / "assistant.db"
    conversations = ConversationService(ConversationStore(database_path))
    monkeypatch.setattr(app.state, "authentication", AuthenticationService(AuthStore(database_path)), raising=False)
    monkeypatch.setattr(app.state, "conversations", conversations, raising=False)
    owner = TestClient(app, base_url="https://testserver")
    other = TestClient(app, base_url="https://testserver")
    _, owner_csrf = _login(owner, "owner@example.test")
    _, other_csrf = _login(other, "other@example.test")
    conversation_id = _create_conversation(owner, owner_csrf)
    other_conversation_id = _create_conversation(other, other_csrf)
    tickets_path = tmp_path / "tickets.json"
    _write_tickets(tickets_path)
    assistant, _, _, _ = _make_assistant(database_path, tickets_path)
    monkeypatch.setattr(app.state, "assistant", assistant, raising=False)
    chat_response = owner.post(
        f"/api/conversations/{conversation_id}/chat",
        json={"message": "Wi-Fi keeps dropping"},
        headers={"X-CSRF-Token": owner_csrf},
    )
    action_id = chat_response.json()["pending_action"]["action_id"]

    foreign_confirmation = other.post(
        f"/api/conversations/{other_conversation_id}/actions/{action_id}/confirm",
        json={"approved": True},
        headers={"X-CSRF-Token": other_csrf},
    )
    allowed_confirmation = owner.post(
        f"/api/conversations/{conversation_id}/actions/{action_id}/confirm",
        json={"approved": True},
        headers={"X-CSRF-Token": owner_csrf},
    )
    replay = owner.post(
        f"/api/conversations/{conversation_id}/actions/{action_id}/confirm",
        json={"approved": True},
        headers={"X-CSRF-Token": owner_csrf},
    )

    assert chat_response.status_code == 200
    assert foreign_confirmation.status_code == 404
    assert allowed_confirmation.status_code == 200
    assert allowed_confirmation.json()["status"] == "executed"
    assert replay.status_code == 404
