"""Test one-time administrator creation against isolated SQLite storage."""
from __future__ import annotations

from pathlib import Path
import sqlite3

from backend.memory.auth_store import AuthStore


def test_first_admin_is_created_once_without_changing_regular_users(tmp_path: Path) -> None:
    """Check the first-admin operation is atomic in policy and preserves regular accounts."""
    store = AuthStore(tmp_path / "assistant.db")
    regular_user = store.create_user("person@example.test", "regular-hash")

    administrator = store.create_first_admin("admin@example.test", "admin-hash")
    second_attempt = store.create_first_admin("other@example.test", "other-hash")

    assert administrator is not None
    assert administrator.role == "admin"
    assert second_attempt is None
    assert store.find_user_by_email("person@example.test") == regular_user
    with sqlite3.connect(tmp_path / "assistant.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone() == (1,)


def test_first_admin_normalizes_email_and_rejects_duplicates(tmp_path: Path) -> None:
    """Check normalized emails retain the unique constraint for local admin bootstrap."""
    store = AuthStore(tmp_path / "assistant.db")

    administrator = store.create_first_admin(" Admin@Example.test ", "admin-hash")

    assert administrator is not None
    assert administrator.email == "admin@example.test"
    assert store.create_first_admin("ADMIN@example.test", "replacement-hash") is None


def test_bootstrap_does_not_promote_or_replace_an_existing_regular_account(tmp_path: Path) -> None:
    """Check the one-time admin path refuses an email already registered as a normal user."""
    store = AuthStore(tmp_path / "assistant.db")
    regular_user = store.create_user("person@example.test", "regular-hash")

    try:
        store.create_first_admin("PERSON@example.test", "admin-hash")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("Bootstrap must not replace or promote an existing account.")

    assert store.find_user_by_email("person@example.test") == regular_user
