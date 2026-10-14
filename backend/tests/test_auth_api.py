"""Verify auth HTTP behavior using a temporary database and FastAPI's test client."""
from __future__ import annotations

from http.cookies import SimpleCookie
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.auth_api import CSRF_COOKIE, SESSION_COOKIE
from backend.main import app
from backend.memory.auth_store import AuthStore
from backend.services.authentication import AuthenticationService


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide an HTTPS test client with an isolated auth database."""
    service = AuthenticationService(AuthStore(tmp_path / "assistant.db"))
    monkeypatch.setattr(app.state, "authentication", service, raising=False)
    return TestClient(app, base_url="https://testserver")


def test_registration_validates_inputs_and_never_exposes_credentials(client: TestClient) -> None:
    """Check registration creates a user-only profile and validates request fields."""
    response = client.post(
        "/api/auth/register",
        json={"email": "Person@example.test", "password": "a strong password"},
    )

    assert response.status_code == 201
    assert response.json()["email"] == "person@example.test"
    assert response.json()["role"] == "user"
    assert "password_hash" not in response.json()
    assert "token" not in response.json()
    assert client.get("/api/auth/me").status_code == 401
    assert client.post(
        "/api/auth/register",
        json={"email": "invalid", "password": "short", "role": "admin"},
    ).status_code == 422


def test_duplicate_registration_returns_conflict_without_account_details(client: TestClient) -> None:
    """Check duplicate registration returns 409 without exposing stored account data."""
    payload = {"email": "person@example.test", "password": "a strong password"}
    client.post("/api/auth/register", json=payload)

    response = client.post("/api/auth/register", json=payload)

    assert response.status_code == 409
    assert response.json() == {"detail": "An account already uses that email."}


def test_login_sets_secure_cookies_and_current_user_reads_session(client: TestClient) -> None:
    """Check login stores credentials in cookies and `/me` returns safe account fields."""
    client.post(
        "/api/auth/register",
        json={"email": "person@example.test", "password": "a strong password"},
    )
    response = client.post(
        "/api/auth/login",
        json={"email": "person@example.test", "password": "a strong password"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "user"
    assert SESSION_COOKIE not in response.json()
    cookie_headers = response.headers.get_list("set-cookie")
    cookies = {}
    for header in cookie_headers:
        parsed = SimpleCookie()
        parsed.load(header)
        cookies.update(parsed)
    assert cookies[SESSION_COOKIE]["httponly"]
    assert cookies[SESSION_COOKIE]["secure"]
    assert cookies[SESSION_COOKIE]["samesite"] == "strict"
    assert not cookies[CSRF_COOKIE]["httponly"]
    assert client.get("/api/auth/me").json()["email"] == "person@example.test"


def test_login_failure_is_generic_and_throttling_returns_429(client: TestClient) -> None:
    """Check bad credentials have a generic response and repeated attempts are throttled."""
    payload = {"email": "missing@example.test", "password": "a strong password"}
    first = client.post("/api/auth/login", json=payload)
    assert first.status_code == 401
    assert first.json() == {"detail": "Email or password is incorrect."}

    for _ in range(9):
        client.post("/api/auth/login", json=payload)
    limited = client.post("/api/auth/login", json=payload)

    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "900"


def test_logout_requires_csrf_pair_and_revokes_session(client: TestClient) -> None:
    """Check logout rejects missing CSRF proof then revokes and clears a valid session."""
    client.post(
        "/api/auth/register",
        json={"email": "person@example.test", "password": "a strong password"},
    )
    client.post(
        "/api/auth/login",
        json={"email": "person@example.test", "password": "a strong password"},
    )
    csrf_token = client.cookies[CSRF_COOKIE]

    assert client.post("/api/auth/logout").status_code == 403
    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})

    assert response.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert "Max-Age=0" in response.headers.get("set-cookie", "")


def test_password_change_requires_csrf_and_revokes_current_session(client: TestClient) -> None:
    """Check password change verifies the current password and requires a fresh login."""
    client.post(
        "/api/auth/register",
        json={"email": "person@example.test", "password": "initial password 12"},
    )
    client.post(
        "/api/auth/login",
        json={"email": "person@example.test", "password": "initial password 12"},
    )
    csrf_token = client.cookies[CSRF_COOKIE]
    payload = {"current_password": "initial password 12", "new_password": "replacement password"}

    assert client.post("/api/auth/password", json=payload).status_code == 403
    response = client.post("/api/auth/password", json=payload, headers={"X-CSRF-Token": csrf_token})

    assert response.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "person@example.test", "password": "initial password 12"},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": "person@example.test", "password": "replacement password"},
    ).status_code == 200


def test_password_change_rejects_wrong_current_password(client: TestClient) -> None:
    """Check an invalid current password leaves the authenticated session usable."""
    client.post(
        "/api/auth/register",
        json={"email": "person@example.test", "password": "initial password 12"},
    )
    client.post(
        "/api/auth/login",
        json={"email": "person@example.test", "password": "initial password 12"},
    )

    response = client.post(
        "/api/auth/password",
        json={"current_password": "incorrect password", "new_password": "replacement password"},
        headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]},
    )

    assert response.status_code == 400
    assert client.get("/api/auth/me").status_code == 200
