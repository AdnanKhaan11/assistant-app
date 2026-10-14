"""Test conversation HTTP routes with separate authenticated users and a temporary database."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

from fastapi.testclient import TestClient
import pytest

from backend.auth_api import CSRF_COOKIE
from backend.main import app
from backend.memory.auth_store import AuthStore
from backend.memory.conversation_store import ConversationStore
from backend.services.authentication import AuthenticationService
from backend.services.conversations import ConversationService

_Result = TypeVar("_Result")


@pytest.fixture
def auth_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, TestClient, ConversationService]:
    """Provide two isolated browser sessions backed by one temporary account database."""
    database_path = tmp_path / "assistant.db"
    store = AuthStore(database_path)
    conversation_service = ConversationService(ConversationStore(database_path))
    monkeypatch.setattr(app.state, "authentication", AuthenticationService(store), raising=False)
    monkeypatch.setattr(app.state, "conversations", conversation_service, raising=False)
    return (
        TestClient(app, base_url="https://testserver"),
        TestClient(app, base_url="https://testserver"),
        conversation_service,
    )


def _register_and_login(client: TestClient, email: str) -> tuple[str, str]:
    """Create `email`, start its session, and return its user ID and CSRF token."""
    password = "a strong password"
    assert client.post("/api/auth/register", json={"email": email, "password": password}).status_code == 201
    login_response = client.post("/api/auth/login", json={"email": email, "password": password})
    return login_response.json()["user"]["user_id"], client.cookies[CSRF_COOKIE]


def test_conversation_routes_enforce_owner_and_csrf(
    auth_clients: tuple[TestClient, TestClient, ConversationService],
) -> None:
    """Check list, read, rename, and delete cannot cross user ownership boundaries."""
    owner_client, other_client, _ = auth_clients
    _register_and_login(owner_client, "owner@example.test")
    _register_and_login(other_client, "other@example.test")
    csrf_token = owner_client.cookies[CSRF_COOKIE]

    created = owner_client.post(
        "/api/conversations",
        json={"title": "Wi-Fi support"},
        headers={"X-CSRF-Token": csrf_token},
    )
    conversation_id = created.json()["conversation_id"]
    assert created.status_code == 201
    assert owner_client.get("/api/conversations").json()[0]["title"] == "Wi-Fi support"
    assert other_client.get("/api/conversations").json() == []
    assert other_client.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert other_client.patch(
        f"/api/conversations/{conversation_id}",
        json={"title": "Stolen"},
        headers={"X-CSRF-Token": other_client.cookies[CSRF_COOKIE]},
    ).status_code == 404
    assert owner_client.patch(
        f"/api/conversations/{conversation_id}", json={"title": "Renamed"}
    ).status_code == 403
    deleted_by_other = other_client.delete(
        f"/api/conversations/{conversation_id}",
        headers={"X-CSRF-Token": other_client.cookies[CSRF_COOKIE]},
    )
    assert deleted_by_other.status_code == 404
    assert owner_client.delete(
        f"/api/conversations/{conversation_id}",
        headers={"X-CSRF-Token": csrf_token},
    ).status_code == 204


def test_message_edit_truncates_later_replies_and_requires_login(
    auth_clients: tuple[TestClient, TestClient, ConversationService],
) -> None:
    """Check editing a user message removes later replies and private routes require login."""
    client, _, service = auth_clients
    assert client.get("/api/conversations").status_code == 401
    user_id, csrf_token = _register_and_login(client, "owner@example.test")
    conversation = client.post(
        "/api/conversations",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    ).json()
    conversation_id = conversation["conversation_id"]
    first = _run(service.append_message(user_id, conversation_id, "user", "first question"))
    _run(service.append_message(user_id, conversation_id, "assistant", "first answer"))
    second = _run(service.append_message(user_id, conversation_id, "user", "old question"))
    _run(service.append_message(user_id, conversation_id, "assistant", "stale answer"))

    edited = client.patch(
        f"/api/conversations/{conversation_id}/messages/{second.message_id}",
        json={"content": "updated question"},
        headers={"X-CSRF-Token": csrf_token},
    )
    history = client.get(f"/api/conversations/{conversation_id}/messages").json()

    assert edited.status_code == 204
    assert [message["content"] for message in history] == [
        "first question",
        "first answer",
        "updated question",
    ]
    assert history[0]["message_id"] == first.message_id


def test_message_delete_requires_owner_and_csrf_and_truncates_following_turns(
    auth_clients: tuple[TestClient, TestClient, ConversationService],
) -> None:
    """Check deletion is owner-scoped, CSRF-protected, and removes stale later turns."""
    owner_client, other_client, service = auth_clients
    owner_id, csrf_token = _register_and_login(owner_client, "owner@example.test")
    _register_and_login(other_client, "other@example.test")
    conversation = owner_client.post(
        "/api/conversations", json={}, headers={"X-CSRF-Token": csrf_token}
    ).json()
    conversation_id = conversation["conversation_id"]
    _run(service.append_message(owner_id, conversation_id, "user", "first"))
    selected = _run(service.append_message(owner_id, conversation_id, "assistant", "selected"))
    _run(service.append_message(owner_id, conversation_id, "user", "stale follow-up"))
    url = f"/api/conversations/{conversation_id}/messages/{selected.message_id}"

    assert owner_client.delete(url).status_code == 403
    assert other_client.delete(
        url,
        headers={"X-CSRF-Token": other_client.cookies[CSRF_COOKIE]},
    ).status_code == 404
    assert owner_client.delete(
        url,
        headers={"X-CSRF-Token": csrf_token},
    ).status_code == 204
    assert [message["content"] for message in owner_client.get(
        f"/api/conversations/{conversation_id}/messages"
    ).json()] == ["first"]
    missing_url = f"/api/conversations/{conversation_id}/messages/{'0' * 32}"
    assert owner_client.delete(
        missing_url,
        headers={"X-CSRF-Token": csrf_token},
    ).status_code == 404


def _run(awaitable: Coroutine[Any, Any, _Result]) -> _Result:
    """Resolve one service coroutine for synchronous route-test setup."""
    return asyncio.run(awaitable)


def test_conversation_routes_validate_pagination_and_ids(
    auth_clients: tuple[TestClient, TestClient, ConversationService],
) -> None:
    """Check malformed resource IDs and out-of-range pages return 422 responses."""
    client, _, _ = auth_clients
    _, csrf_token = _register_and_login(client, "owner@example.test")

    assert client.get("/api/conversations?limit=51").status_code == 422
    assert client.get("/api/conversations/not-an-id").status_code == 422
    assert client.post(
        "/api/conversations",
        json={"title": " "},
        headers={"X-CSRF-Token": csrf_token},
    ).status_code == 422
