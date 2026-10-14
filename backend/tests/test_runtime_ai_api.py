"""Test authorization, validation, and safe persistence for admin AI configuration routes."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.auth_api import CSRF_COOKIE
from backend.llm.registry import PROVIDERS
from backend.main import app
from backend.memory.auth_store import AuthStore
from backend.memory.runtime_config_store import RuntimeAIConfigStore
from backend.services.authentication import AuthenticationService
from backend.services.runtime_configuration import RuntimeConfigurationService
from backend.tests.fakes import FakeProvider
from backend.utils.auth_security import hash_password
from backend.utils.config import Settings


@pytest.fixture
def admin_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide an admin client and isolated runtime settings storage."""
    database_path = tmp_path / "runtime-config.db"
    store = AuthStore(database_path)
    store.create_first_admin("runtime-admin@example.test", hash_password("administrator password"))
    monkeypatch.setattr(app.state, "authentication", AuthenticationService(store), raising=False)
    settings = Settings(llm_provider="groq", llm_model="active-groq-model", llm_api_key="test-key")
    service = RuntimeConfigurationService(
        settings,
        RuntimeAIConfigStore(database_path),
        lambda provider, model, key: FakeProvider([]),
    )
    monkeypatch.setattr(app.state, "runtime_configuration", service, raising=False)
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/api/auth/login",
        json={"email": "runtime-admin@example.test", "password": "administrator password"},
    )
    assert response.status_code == 200
    return client


def _csrf(client: TestClient) -> dict[str, str]:
    """Build the required same-origin CSRF header from the test session cookie."""
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE]}


def test_runtime_ai_configuration_requires_admin_authentication(admin_client: TestClient) -> None:
    """Reject anonymous and regular-user reads, validation, and saves."""
    admin = admin_client
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get("/api/auth/admin/ai-config").status_code == 401
    assert anonymous.post(
        "/api/auth/admin/ai-config/validate",
        json={"provider": "groq", "model": PROVIDERS["groq"].default_model},
    ).status_code == 401

    regular = TestClient(app, base_url="https://testserver")
    regular.post(
        "/api/auth/register",
        json={"email": "regular@example.test", "password": "regular account password"},
    )
    regular.post(
        "/api/auth/login",
        json={"email": "regular@example.test", "password": "regular account password"},
    )
    assert regular.get("/api/auth/admin/ai-config").status_code == 403
    assert regular.put(
        "/api/auth/admin/ai-config",
        json={"provider": "groq", "model": PROVIDERS["groq"].default_model},
        headers=_csrf(regular),
    ).status_code == 403
    assert admin.get("/api/auth/admin/ai-config").status_code == 200


def test_runtime_ai_configuration_validates_and_saves_without_exposing_secrets(
    admin_client: TestClient,
) -> None:
    """Expose active/saved state, reject unsupported models, and audit safe saves."""
    client = admin_client
    initial = client.get("/api/auth/admin/ai-config")
    assert initial.status_code == 200
    assert initial.json()["active_model"] == "active-groq-model"
    assert initial.json()["saved"] is None
    assert "test-key" not in initial.text

    invalid = client.post(
        "/api/auth/admin/ai-config/validate",
        json={"provider": "groq", "model": "unlisted-model"},
        headers=_csrf(client),
    )
    assert invalid.status_code == 200
    assert invalid.json()["is_valid"] is False

    request = {"provider": "groq", "model": PROVIDERS["groq"].default_model}
    saved = client.put("/api/auth/admin/ai-config", json=request, headers=_csrf(client))
    assert saved.status_code == 200
    assert saved.json()["provider"] == "groq"
    assert "test-key" not in saved.text
    assert client.get("/api/auth/admin/ai-config").json()["saved"]["model"] == request["model"]
    audit = client.get("/api/auth/admin/audit")
    assert audit.status_code == 200
    assert any(event["action"] == "runtime_ai_config.saved" for event in audit.json())


def test_runtime_ai_configuration_requires_csrf_and_rejects_extra_fields(admin_client: TestClient) -> None:
    """Protect both mutating operations with CSRF and reject secret-bearing request fields."""
    client = admin_client
    selection = {"provider": "groq", "model": PROVIDERS["groq"].default_model}
    assert client.post("/api/auth/admin/ai-config/validate", json=selection).status_code == 403
    assert client.put("/api/auth/admin/ai-config", json=selection).status_code == 403
    assert client.put(
        "/api/auth/admin/ai-config",
        json={**selection, "api_key": "must-not-be-accepted"},
        headers=_csrf(client),
    ).status_code == 422


def test_embedding_status_requires_admin_and_masks_secrets_and_paths(
    admin_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose embedding readiness to admins without disclosing credentials or local paths."""
    admin = admin_client
    assert TestClient(app, base_url="https://testserver").get(
        "/api/auth/admin/ai-config/embeddings"
    ).status_code == 401

    regular = TestClient(app, base_url="https://testserver")
    regular.post(
        "/api/auth/register",
        json={"email": "embedding-user@example.test", "password": "regular account password"},
    )
    regular.post(
        "/api/auth/login",
        json={"email": "embedding-user@example.test", "password": "regular account password"},
    )
    assert regular.get("/api/auth/admin/ai-config/embeddings").status_code == 403

    manual = tmp_path / "manual.pdf"
    manual.write_bytes(b"test manual")
    settings = Settings(
        llm_provider="groq",
        hf_token="private-hf-token",
        hf_embedding_model="sample/embed-model",
        qdrant_url="https://private-qdrant.example.test",
        qdrant_api_key="private-qdrant-key",
        knowledge_pdf_path=str(manual),
    )
    monkeypatch.setattr(app.state, "settings", settings, raising=False)

    response = admin.get("/api/auth/admin/ai-config/embeddings")
    assert response.status_code == 200
    assert response.json() == {
        "provider": "huggingface",
        "model": "sample/embed-model",
        "is_provider_configured": True,
        "vector_store": "qdrant",
        "is_vector_store_configured": True,
        "is_manual_available": True,
        "vector_dimension": None,
        "note": "This reports local configuration only; service connectivity and indexed vector dimensions are not tracked.",
    }
    assert "private-hf-token" not in response.text
    assert "private-qdrant-key" not in response.text
    assert str(manual) not in response.text


def test_embedding_status_reports_missing_configuration(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Represent absent credentials, vector store, and manual as unavailable rather than ready."""
    monkeypatch.setattr(
        app.state,
        "settings",
        Settings(knowledge_pdf_path="missing-manual.pdf"),
        raising=False,
    )
    response = admin_client.get("/api/auth/admin/ai-config/embeddings")
    assert response.status_code == 200
    assert response.json()["is_provider_configured"] is False
    assert response.json()["is_vector_store_configured"] is False
    assert response.json()["is_manual_available"] is False
    assert response.json()["vector_dimension"] is None


def test_system_status_requires_admin_authentication(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep runtime system information private to administrators."""
    monkeypatch.setattr(app.state, "settings", Settings(), raising=False)
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get("/api/auth/admin/ai-config/system-status").status_code == 401

    regular = TestClient(app, base_url="https://testserver")
    regular.post(
        "/api/auth/register",
        json={"email": "system-user@example.test", "password": "regular account password"},
    )
    regular.post(
        "/api/auth/login",
        json={"email": "system-user@example.test", "password": "regular account password"},
    )
    assert regular.get("/api/auth/admin/ai-config/system-status").status_code == 403
    assert admin_client.get("/api/auth/admin/ai-config/system-status").status_code == 200


def test_system_status_reports_safe_readiness_and_does_not_create_database(
    admin_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report configured features while masking secrets and probing SQLite read-only."""
    database_path = tmp_path / "existing.db"
    database_path.write_bytes(b"not a sqlite database")
    settings = Settings(
        llm_provider="groq",
        llm_model="safe-model-name",
        llm_api_key="private-chat-secret",
        db_path=str(database_path),
        hf_token="private-hf-secret",
        qdrant_url="https://private-vector-host.example.test",
        qdrant_api_key="private-vector-secret",
    )
    monkeypatch.setattr(app.state, "settings", settings, raising=False)

    response = admin_client.get("/api/auth/admin/ai-config/system-status")
    assert response.status_code == 200
    body = response.json()
    assert body["database_status"] == "unavailable"
    assert body["active_chat_provider"] == "groq"
    assert body["active_chat_model"] == "safe-model-name"
    assert body["is_chat_provider_configured"] is True
    assert body["semantic_retrieval_configured"] is True
    assert body["max_upload_bytes"] == settings.max_upload_bytes
    assert body["max_attachments_per_message"] == settings.max_attachments_per_message
    assert body["stream_max_seconds"] == settings.stream_max_seconds
    assert body["memory_window_turns"] == settings.memory_window_turns
    assert body["vision_enabled"] is settings.llm_supports_vision
    assert "connectivity is not checked" in body["note"]
    assert "private-chat-secret" not in response.text
    assert "private-hf-secret" not in response.text
    assert "private-vector-secret" not in response.text
    assert str(database_path) not in response.text

    healthy_path = tmp_path / "healthy.db"
    with sqlite3.connect(healthy_path) as connection:
        connection.execute("CREATE TABLE health_probe (id INTEGER)")
    monkeypatch.setattr(app.state, "settings", Settings(db_path=str(healthy_path)), raising=False)
    healthy_response = admin_client.get("/api/auth/admin/ai-config/system-status")
    assert healthy_response.status_code == 200
    assert healthy_response.json()["database_status"] == "available"

    missing_path = tmp_path / "must-not-be-created.db"
    monkeypatch.setattr(app.state, "settings", Settings(db_path=str(missing_path)), raising=False)
    missing_response = admin_client.get("/api/auth/admin/ai-config/system-status")
    assert missing_response.status_code == 200
    assert missing_response.json()["database_status"] == "unavailable"
    assert not missing_path.exists()
