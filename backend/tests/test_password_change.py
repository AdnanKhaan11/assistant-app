"""Test password replacement and account-wide session revocation."""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from backend.memory.auth_store import AuthStore
from backend.services.authentication import AuthenticationFailed, AuthenticationService
from backend.utils.auth_security import hash_password, verify_password


@pytest.fixture
def auth_setup(tmp_path: Path) -> tuple[AuthStore, AuthenticationService, str]:
    """Create an isolated account and its authentication service."""
    store = AuthStore(tmp_path / "assistant.db")
    service = AuthenticationService(store)
    user = store.create_user("person@example.test", hash_password("initial password"))
    return store, service, user.user_id


@pytest.mark.asyncio
async def test_password_change_replaces_hash_and_revokes_all_sessions(
    auth_setup: tuple[AuthStore, AuthenticationService, str],
) -> None:
    """Check a valid password change updates the hash and logs every session out."""
    store, service, user_id = auth_setup
    first_session = await service.login("person@example.test", "initial password")
    second_session = await service.login("person@example.test", "initial password")

    await service.change_password(user_id, "initial password", "replacement password")

    updated_user = store.find_user_by_id(user_id)
    assert updated_user is not None
    assert verify_password("replacement password", updated_user.password_hash)
    assert await service.authenticate(first_session.token) is None
    assert await service.authenticate(second_session.token) is None
    assert (await service.login("person@example.test", "replacement password")).user.user_id == user_id


@pytest.mark.asyncio
async def test_password_change_rejects_wrong_current_password(
    auth_setup: tuple[AuthStore, AuthenticationService, str],
) -> None:
    """Check an incorrect current password cannot replace the stored password hash."""
    store, service, user_id = auth_setup
    user = store.find_user_by_id(user_id)
    assert user is not None
    store.update_password(user_id, user.password_hash, hash_password("initial password"))

    with pytest.raises(AuthenticationFailed):
        await service.change_password(user_id, "wrong password", "replacement password")

    stored_user = store.find_user_by_id(user_id)
    assert stored_user is not None
    assert verify_password("initial password", stored_user.password_hash)


def test_store_will_not_update_a_disabled_or_changed_account(tmp_path: Path) -> None:
    """Check conditional password update protects disabled accounts and stale hashes."""
    store = AuthStore(tmp_path / "assistant.db")
    user = store.create_user("person@example.test", "old-hash")
    with sqlite3.connect(tmp_path / "assistant.db") as connection:
        connection.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user.user_id,))

    assert not store.update_password(user.user_id, "old-hash", "new-hash")
    assert not store.update_password("missing-user", "old-hash", "new-hash")


def test_session_creation_rechecks_verified_password_hash(tmp_path: Path) -> None:
    """Check a login verified before a password change cannot create a stale session after it."""
    store = AuthStore(tmp_path / "assistant.db")
    user = store.create_user("person@example.test", "old-hash")
    store.update_password(user.user_id, "old-hash", "new-hash")

    assert not store.create_session_if_password_matches(
        "old-session-hash", user.user_id, "2099-01-01T00:00:00.000000Z", "old-hash"
    )
    assert store.create_session_if_password_matches(
        "new-session-hash", user.user_id, "2099-01-01T00:00:00.000000Z", "new-hash"
    )
