from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from backend.memory.auth_store import AuthStore
from backend.utils.auth_security import (
    create_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)


def test_password_hashing_verifies_only_the_matching_password() -> None:
    """Check one-way password hashing and rejection of wrong or malformed values."""
    encoded_hash = hash_password("correct horse battery staple")

    assert encoded_hash.startswith("pbkdf2_sha256$600000$")
    assert encoded_hash != hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded_hash)
    assert not verify_password("wrong password", encoded_hash)
    assert not verify_password("anything", "malformed")


def test_session_tokens_are_random_and_only_the_hash_is_persisted(tmp_path: Path) -> None:
    """Check an active token resolves while only its digest is stored in SQLite."""
    store = AuthStore(tmp_path / "assistant.db")
    user = store.create_user("person@example.test", hash_password("secret"))
    token = create_session_token()
    token_hash = hash_session_token(token)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    store.create_session(token_hash, user.user_id, expires_at)

    assert store.find_active_session(token_hash) == user
    assert store.find_active_session(token) is None
    with sqlite3.connect(tmp_path / "assistant.db") as connection:
        assert connection.execute("SELECT session_id FROM sessions").fetchone() == (token_hash,)
        assert token not in token_hash


def test_user_lookup_normalizes_email_and_registration_cannot_choose_role(tmp_path: Path) -> None:
    """Check account lookup normalizes email and creation always assigns the user role."""
    store = AuthStore(tmp_path / "assistant.db")
    user = store.create_user(" Person@Example.test ", "hash")

    assert user.email == "person@example.test"
    assert user.role == "user"
    assert store.find_user_by_email("PERSON@example.test") == user
    with pytest.raises(sqlite3.IntegrityError):
        store.create_user("PERSON@example.test", "another-hash")


def test_expired_sessions_and_disabled_accounts_are_rejected(tmp_path: Path) -> None:
    """Check expiry and account deactivation each invalidate session authentication."""
    store = AuthStore(tmp_path / "assistant.db")
    user = store.create_user("person@example.test", "hash")
    expired_token_hash = hash_session_token("expired-token")
    store.create_session(
        expired_token_hash,
        user.user_id,
        (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
    active_token_hash = hash_session_token("active-token")
    store.create_session(
        active_token_hash,
        user.user_id,
        (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )
    assert store.find_active_session(expired_token_hash) is None
    assert store.find_active_session(active_token_hash) == user

    with sqlite3.connect(tmp_path / "assistant.db") as connection:
        connection.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user.user_id,))

    assert store.find_active_session(active_token_hash) is None


def test_logout_revokes_a_session_once(tmp_path: Path) -> None:
    """Check deleting a session revokes it and reports repeat logout accurately."""
    store = AuthStore(tmp_path / "assistant.db")
    user = store.create_user("person@example.test", "hash")
    token_hash = hash_session_token("logout-token")
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    store.create_session(token_hash, user.user_id, expires_at)

    assert store.delete_session(token_hash)
    assert not store.delete_session(token_hash)
    assert store.find_active_session(token_hash) is None
