"""Exercise HTTP routes without starting provider or telemetry services."""
from __future__ import annotations

from pathlib import Path
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest

from backend.llm import LLMError, Message
from backend.main import app
from backend.assistant import AssistantStreamEvent
from backend.memory.attachment_store import AttachmentStore
from backend.utils.attachments import AttachmentNotFound
from backend.memory.long_term import LongTermMemory
from backend.memory.short_term import ShortTermMemory


class _FakeAssistant:
    async def handle_message(self, request: Any) -> dict[str, Any]:
        if request.message == "provider failure":
            raise LLMError("provider secret detail")
        return {
            "ticket_id": request.ticket_id,
            "category": "unknown",
            "confidence": 0.0,
            "summary": request.message,
            "recommendations": [],
            "findings": [],
            "pending_action": None,
            "answer": "Please share more details.",
            "trace_id": None,
        }

    async def handle_message_stream(self, request: Any) -> AsyncIterator[AssistantStreamEvent]:
        yield AssistantStreamEvent("stage", {"name": "classifying"})
        yield AssistantStreamEvent("token", {"text": "Answer"})
        yield AssistantStreamEvent("final", {"ticket_id": request.ticket_id, "answer": "Answer"})

    async def validate_attachments(self, ticket_id: str, attachment_ids: list[str]) -> None:
        if attachment_ids:
            raise AttachmentNotFound("not owned by this ticket")

    async def confirm_action(self, action_id: str, approved: bool) -> dict[str, Any]:
        if action_id == "missing":
            raise KeyError(action_id)
        return {"action_id": action_id, "status": "denied", "message": "Not run. Nothing was changed."}

    async def get_ticket_history(self, ticket_id: str) -> list[dict[str, str | None]]:
        return [{"role": "user", "content": ticket_id, "category": None, "created_at": "now"}]

    async def clear_ticket(self, ticket_id: str) -> None:
        self.cleared_ticket = ticket_id


def _client(monkeypatch: Any, assistant: _FakeAssistant | None = None) -> TestClient:
    monkeypatch.setattr(app.state, "assistant", assistant or _FakeAssistant(), raising=False)
    app.state.settings = SimpleNamespace(
        max_attachments_per_message=3,
        max_upload_bytes=5_242_880,
        llm_supports_vision=False,
    )
    return TestClient(app)


def test_chat_validates_and_returns_assessment(monkeypatch: Any) -> None:
    client = _client(monkeypatch)

    response = client.post("/api/chat", json={"ticket_id": "T-1", "message": "Wi-Fi issue"})

    assert response.status_code == 200
    assert response.json()["ticket_id"] == "T-1"
    assert response.json()["answer"] == "Please share more details."
    invalid = client.post("/api/chat", json={"ticket_id": "bad/id", "message": "Help"})
    assert invalid.status_code == 422


def test_confirm_action_maps_missing_pending_action_to_404(monkeypatch: Any) -> None:
    client = _client(monkeypatch)

    assert client.post("/api/actions/action-1/confirm", json={"approved": False}).status_code == 200
    missing = client.post("/api/actions/missing/confirm", json={"approved": True})

    assert missing.status_code == 404
    assert missing.json() == {"detail": "Pending action not found."}


def test_history_and_delete_routes(monkeypatch: Any) -> None:
    assistant = _FakeAssistant()
    client = _client(monkeypatch, assistant)

    history = client.get("/api/tickets/T-1/history")
    deleted = client.delete("/api/tickets/T-1")

    assert history.status_code == 200
    assert history.json()[0]["content"] == "T-1"
    assert deleted.status_code == 204
    assert assistant.cleared_ticket == "T-1"
    assert client.get("/api/tickets/bad$id/history").status_code == 422


def test_llm_failure_returns_friendly_gateway_error(monkeypatch: Any) -> None:
    response = _client(monkeypatch).post(
        "/api/chat",
        json={"ticket_id": "T-1", "message": "provider failure"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "The AI provider is unavailable. Try again shortly."}
    assert "provider secret detail" not in response.text


def test_chat_stream_serializes_events_in_order(monkeypatch: Any) -> None:
    response = _client(monkeypatch).post(
        "/api/chat/stream",
        json={"ticket_id": "T-1", "message": "Help"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.index("event: stage") < response.text.index("event: token") < response.text.index("event: final")


def test_chat_stream_checks_attachment_ownership_before_start(monkeypatch: Any) -> None:
    response = _client(monkeypatch).post(
        "/api/chat/stream",
        json={"ticket_id": "T-1", "message": "Help", "attachment_ids": ["a" * 32]},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Attachment not found."}


def test_chat_rejects_too_many_or_malformed_attachment_ids(monkeypatch: Any) -> None:
    client = _client(monkeypatch)
    app.state.settings = SimpleNamespace(max_attachments_per_message=1)

    too_many = client.post(
        "/api/chat/stream",
        json={"ticket_id": "T-1", "message": "Help", "attachment_ids": ["a" * 32, "b" * 32]},
    )
    malformed = client.post(
        "/api/chat",
        json={"ticket_id": "T-1", "message": "Help", "attachment_ids": ["not-an-id"]},
    )

    assert too_many.status_code == 422
    assert malformed.status_code == 422


def test_upload_stores_detected_text_attachment(tmp_path: Path, monkeypatch: Any) -> None:
    store = AttachmentStore(str(tmp_path / "assistant.db"), str(tmp_path / "uploads"))
    app.state.attachment_store = store
    client = _client(monkeypatch)
    app.state.settings = SimpleNamespace(max_upload_bytes=100, llm_supports_vision=False, max_attachments_per_message=3)
    response = client.post(
        "/api/tickets/T-1/attachments",
        files={"file": ("../../notes.log", b"diagnostic log", "application/octet-stream")},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["attachment_id"] and len(data["attachment_id"]) == 32
    assert data["filename"] == "notes.log"
    assert data["content_type"] == "text/plain"
    assert data["kind"] == "text"
    assert data["size_bytes"] == len(b"diagnostic log")
    assert store.get("T-1", data["attachment_id"]).content == b"diagnostic log"


def test_upload_maps_oversize_unsupported_and_invalid_ticket(tmp_path: Path, monkeypatch: Any) -> None:
    app.state.attachment_store = AttachmentStore(str(tmp_path / "assistant.db"), str(tmp_path / "uploads"))
    client = _client(monkeypatch)
    app.state.settings = SimpleNamespace(max_upload_bytes=32, llm_supports_vision=False, max_attachments_per_message=3)

    too_large = client.post("/api/tickets/T-1/attachments", files={"file": ("x.txt", b"x" * 40)})
    unsupported = client.post("/api/tickets/T-1/attachments", files={"file": ("x.svg", b"<svg></svg>")})
    invalid_ticket = client.post("/api/tickets/bad$id/attachments", files={"file": ("x.txt", b"x")})

    assert too_large.status_code == 413
    assert unsupported.status_code == 415
    assert invalid_ticket.status_code == 422


def test_upload_rejects_images_when_vision_is_disabled(tmp_path: Path, monkeypatch: Any) -> None:
    app.state.attachment_store = AttachmentStore(str(tmp_path / "assistant.db"), str(tmp_path / "uploads"))
    client = _client(monkeypatch)
    app.state.settings = SimpleNamespace(max_upload_bytes=100, llm_supports_vision=False, max_attachments_per_message=3)

    response = client.post(
        "/api/tickets/T-1/attachments",
        files={"file": ("capture.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Image analysis is not enabled for the current model."


async def test_assistant_memory_routes_use_public_memory_methods(tmp_path: Path) -> None:
    from backend.tests.test_assistant_flow import _make_assistant, _write_tickets

    tickets = tmp_path / "tickets.json"
    _write_tickets(tickets)
    attachments = AttachmentStore(str(tmp_path / "memory.db"), str(tmp_path / "uploads"))
    attachment_id = "e" * 32
    attachments.save(
        ticket_id="T-1",
        attachment_id=attachment_id,
        filename="log.txt",
        content_type="text/plain",
        kind="text",
        content=b"private log data",
    )
    assistant, _, short_term, _ = _make_assistant(
        tmp_path / "memory.db", tickets, attachment_store=attachments
    )
    short_term.add("T-1", Message("user", "temporary"))
    long_term = LongTermMemory(str(tmp_path / "memory.db"))
    long_term.save_turn("T-1", "user", "durable", "network_issue")

    assert (await assistant.get_ticket_history("T-1"))[0]["content"] == "durable"
    await assistant.clear_ticket("T-1")
    assert await assistant.get_ticket_history("T-1") == []
    assert short_term.history("T-1") == []
    with pytest.raises(AttachmentNotFound):
        attachments.get("T-1", attachment_id)
