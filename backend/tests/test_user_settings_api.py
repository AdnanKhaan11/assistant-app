"""Test private user settings, CSRF validation, and persisted theme preferences."""
from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.auth_api import CSRF_COOKIE
from backend.main import app
from backend.memory.auth_store import AuthStore
from backend.memory.user_settings_store import UserSettingsStore
from backend.services.authentication import AuthenticationService
from backend.utils.auth_security import hash_password
from backend.utils.config import Settings


@pytest.fixture
def settings_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, TestClient]:
    """Create two isolated signed-in users backed by one migrated test database."""
    database_path = tmp_path / "user-settings.db"
    auth_store = AuthStore(database_path)
    auth_store.create_first_admin("settings-admin@example.test", hash_password("settings admin password"))
    monkeypatch.setattr(app.state, "authentication", AuthenticationService(auth_store), raising=False)
    monkeypatch.setattr(app.state, "settings", Settings(db_path=str(database_path)), raising=False)
    clients = []
    for email in ("settings-one@example.test", "settings-two@example.test"):
        client = TestClient(app, base_url="https://testserver")
        registered = client.post(
            "/api/auth/register",
            json={"email": email, "password": "private settings password"},
        )
        assert registered.status_code == 201
        logged_in = client.post(
            "/api/auth/login",
            json={"email": email, "password": "private settings password"},
        )
        assert logged_in.status_code == 200
        clients.append(client)
    return clients[0], clients[1]


def _csrf(client: TestClient) -> dict[str, str]:
    """Build the CSRF header paired with the client's session cookie."""
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE]}


def test_user_settings_require_authentication_and_csrf(settings_clients: tuple[TestClient, TestClient]) -> None:
    """Protect private settings reads with login and writes with CSRF proof."""
    first, _ = settings_clients
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get("/api/settings").status_code == 401
    assert first.put("/api/settings", json={"theme": "dark"}).status_code == 403
    assert first.put(
        "/api/settings",
        json={"theme": "dark", "role": "admin"},
        headers=_csrf(first),
    ).status_code == 422


def test_user_theme_is_saved_and_private(settings_clients: tuple[TestClient, TestClient]) -> None:
    """Persist theme changes for their owner without changing another user's defaults."""
    first, second = settings_clients
    assert first.get("/api/settings").json() == {"theme": "light", "updated_at": None}
    updated = first.put("/api/settings", json={"theme": "dark"}, headers=_csrf(first))
    assert updated.status_code == 200
    assert updated.json()["theme"] == "dark"
    assert isinstance(updated.json()["updated_at"], str)
    assert first.get("/api/settings").json()["theme"] == "dark"
    assert second.get("/api/settings").json() == {"theme": "light", "updated_at": None}


def test_user_settings_reject_invalid_theme(settings_clients: tuple[TestClient, TestClient]) -> None:
    """Reject unsupported theme names at the HTTP validation boundary."""
    first, _ = settings_clients
    response = first.put(
        "/api/settings",
        json={"theme": "system"},
        headers=_csrf(first),
    )
    assert response.status_code == 422


def test_settings_store_recovers_invalid_values_and_preserves_other_preferences(tmp_path: Path) -> None:
    """Use the default for corrupt theme data while retaining unrelated preference keys on writes."""
    database_path = tmp_path / "stored-settings.db"
    auth_store = AuthStore(database_path)
    user = auth_store.create_first_admin("stored-settings@example.test", hash_password("stored settings password"))
    store = UserSettingsStore(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO user_preferences (user_id, preferences_json) VALUES (?, ?)",
            (user.user_id, '{"theme":[],"locale":"en"}'),
        )

    assert store.get(user.user_id).theme == "light"
    assert store.set_theme(user.user_id, "dark").theme == "dark"
    with sqlite3.connect(database_path) as connection:
        preferences = connection.execute(
            "SELECT preferences_json FROM user_preferences WHERE user_id = ?",
            (user.user_id,),
        ).fetchone()[0]
    assert '"locale":"en"' in preferences
