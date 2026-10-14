"""Test administrator user listing, safe account changes, and audit persistence."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.auth_api import CSRF_COOKIE
from backend.main import app
from backend.memory.auth_store import AuthStore, LastActiveAdminError
from backend.services.authentication import AuthenticationService
from backend.utils.auth_security import hash_password


@pytest.fixture
def admin_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, AuthStore]:
    """Provide an authenticated administrator and temporary user database."""
    store = AuthStore(tmp_path / "assistant.db")
    store.create_first_admin("admin@example.test", hash_password("administrator password"))
    monkeypatch.setattr(app.state, "authentication", AuthenticationService(store), raising=False)
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@example.test", "password": "administrator password"},
    )
    assert response.status_code == 200
    return client, store


def test_admin_user_routes_require_admin_authentication_and_csrf(
    admin_client: tuple[TestClient, AuthStore],
) -> None:
    """Check regular and anonymous sessions cannot list or update administrator accounts."""
    admin, store = admin_client
    regular = TestClient(app, base_url="https://testserver")
    regular.post(
        "/api/auth/register",
        json={"email": "user@example.test", "password": "regular user password"},
    )
    regular.post(
        "/api/auth/login",
        json={"email": "user@example.test", "password": "regular user password"},
    )
    user_id = store.find_user_by_email("user@example.test").user_id

    assert regular.get("/api/auth/admin/users").status_code == 403
    assert regular.get("/api/auth/admin/audit").status_code == 403
    assert TestClient(app, base_url="https://testserver").get("/api/auth/admin/users").status_code == 401
    assert TestClient(app, base_url="https://testserver").get("/api/auth/admin/audit").status_code == 401
    assert admin.patch(
        f"/api/auth/admin/users/{user_id}",
        json={"role": "admin", "is_active": True},
    ).status_code == 403


def test_admin_can_page_users_update_status_and_revoke_sessions(
    admin_client: tuple[TestClient, AuthStore],
) -> None:
    """Check safe paginated profiles, account updates, audit details, and session revocation."""
    admin, store = admin_client
    user = store.create_user("user@example.test", hash_password("regular user password"))
    regular = TestClient(app, base_url="https://testserver")
    assert regular.post(
        "/api/auth/login",
        json={"email": "user@example.test", "password": "regular user password"},
    ).status_code == 200
    csrf_token = admin.cookies[CSRF_COOKIE]
    listed = admin.get("/api/auth/admin/users?limit=50&offset=0")

    assert listed.status_code == 200
    assert user.user_id in {record["user_id"] for record in listed.json()}
    assert "password_hash" not in listed.text
    promoted = admin.patch(
        f"/api/auth/admin/users/{user.user_id}",
        json={"role": "admin", "is_active": True},
        headers={"X-CSRF-Token": csrf_token},
    )
    disabled = admin.patch(
        f"/api/auth/admin/users/{user.user_id}",
        json={"role": "admin", "is_active": False},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    assert regular.get("/api/auth/me").status_code == 401
    with sqlite3.connect(store._path) as connection:
        events = connection.execute(
            "SELECT action, target_user_id, details_json FROM admin_audit_events ORDER BY event_id"
        ).fetchall()
    assert len(events) == 2
    assert all(event[0] == "user.account_updated" and event[1] == user.user_id for event in events)
    assert json.loads(events[0][2])["to_role"] == "admin"
    assert json.loads(events[1][2])["to_active"] is False


def test_admin_audit_route_returns_safe_newest_first_pages(
    admin_client: tuple[TestClient, AuthStore],
) -> None:
    """Check audit pagination, account metadata, and exclusion of credentials and session data."""
    admin, store = admin_client
    first = store.create_user("first@example.test", hash_password("first user password"))
    second = store.create_user("second@example.test", hash_password("second user password"))
    csrf_token = admin.cookies[CSRF_COOKIE]
    for target in (first, second):
        response = admin.patch(
            f"/api/auth/admin/users/{target.user_id}",
            json={"role": "user", "is_active": False},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 200

    newest = admin.get("/api/auth/admin/audit?limit=1&offset=0")
    older = admin.get("/api/auth/admin/audit?limit=1&offset=1")

    assert newest.status_code == older.status_code == 200
    newest_event = newest.json()[0]
    older_event = older.json()[0]
    assert newest_event["event_id"] > older_event["event_id"]
    assert newest_event["actor_email"] == "admin@example.test"
    assert newest_event["target_email"] == "second@example.test"
    assert newest_event["details"] == {
        "from_active": True,
        "from_role": "user",
        "to_active": False,
        "to_role": "user",
    }
    assert "password_hash" not in newest.text
    assert "session" not in newest.text.lower()
    assert admin.get("/api/auth/admin/audit?limit=51").status_code == 422


def test_admin_cannot_change_self_or_remove_last_active_admin(
    admin_client: tuple[TestClient, AuthStore],
) -> None:
    """Reject self-lockout at the API and last-active-admin removal inside the transaction."""
    admin, store = admin_client
    actor = store.find_user_by_email("admin@example.test")
    csrf_token = admin.cookies[CSRF_COOKIE]

    self_change = admin.patch(
        f"/api/auth/admin/users/{actor.user_id}",
        json={"role": "user", "is_active": True},
        headers={"X-CSRF-Token": csrf_token},
    )
    other_user = store.create_user("other@example.test", "unused-hash")
    with pytest.raises(LastActiveAdminError):
        store.update_managed_user(other_user.user_id, actor.user_id, "user", True)

    assert self_change.status_code == 409
    assert store.find_user_by_id(actor.user_id).role == "admin"
