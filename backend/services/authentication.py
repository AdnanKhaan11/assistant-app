"""Coordinate account registration, credential checks, and revocable session creation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import sqlite3

from backend.memory.auth_store import (
    AdminAuditEvent,
    AuthStore,
    LastActiveAdminError,
    ManagedAuthUser,
    StoredAuthUser,
)
from backend.utils.auth_security import (
    create_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)

SESSION_TTL = timedelta(hours=8)
MAX_AUTH_ATTEMPTS = 10


class EmailAlreadyRegistered(Exception):
    """Signal that registration email is already associated with an account."""


class AuthenticationFailed(Exception):
    """Signal a generic login failure without distinguishing account state."""


class AuthenticationRateLimited(Exception):
    """Signal that too many registration or login attempts were made for one email."""


class AdminUserNotFound(Exception):
    """Signal that an administrator requested a user ID that does not exist."""


class AdminActionRejected(Exception):
    """Signal an unsafe role or account-status change requested by an administrator."""


@dataclass(frozen=True)
class LoginSession:
    """Return the authenticated account and the one-time raw token for cookie delivery."""

    user: StoredAuthUser
    token: str


class AuthenticationService:
    """Keep password checks and session policy outside the HTTP and storage layers."""

    def __init__(self, store: AuthStore) -> None:
        """Use `store` as the persistence boundary for accounts and sessions."""
        self._store = store

    async def register(self, email: str, password: str) -> StoredAuthUser:
        """Create a user using `email` and plaintext `password`, never persisting plaintext."""
        await self._check_attempt_limit(email)
        password_hash = await asyncio.to_thread(hash_password, password)
        try:
            user = await asyncio.to_thread(self._store.create_user, email, password_hash)
        except sqlite3.IntegrityError as error:
            raise EmailAlreadyRegistered from error
        await asyncio.to_thread(self._store.clear_auth_attempts, email)
        return user

    async def login(self, email: str, password: str) -> LoginSession:
        """Verify `email` and `password`, then create a short-lived opaque session token."""
        await self._check_attempt_limit(email)
        user = await asyncio.to_thread(self._store.find_user_by_email, email)
        is_valid = user is not None and user.is_active and await asyncio.to_thread(
            verify_password, password, user.password_hash
        )
        if not is_valid or user is None:
            raise AuthenticationFailed
        token = create_session_token()
        expires_at = (datetime.now(timezone.utc) + SESSION_TTL).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        session_created = await asyncio.to_thread(
            self._store.create_session_if_password_matches,
            hash_session_token(token),
            user.user_id,
            expires_at,
            user.password_hash,
        )
        if not session_created:
            raise AuthenticationFailed
        await asyncio.to_thread(self._store.clear_auth_attempts, email)
        return LoginSession(user=user, token=token)

    async def authenticate(self, token: str) -> StoredAuthUser | None:
        """Resolve `token` to its active account, returning `None` for invalid sessions."""
        return await asyncio.to_thread(self._store.find_active_session, hash_session_token(token))

    async def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        """Verify `current_password`, save `new_password`, and invalidate every account session."""
        user = await asyncio.to_thread(self._store.find_user_by_id, user_id)
        if user is None or not user.is_active:
            raise AuthenticationFailed
        is_valid = await asyncio.to_thread(verify_password, current_password, user.password_hash)
        if not is_valid:
            raise AuthenticationFailed
        new_hash = await asyncio.to_thread(hash_password, new_password)
        changed = await asyncio.to_thread(self._store.update_password, user_id, user.password_hash, new_hash)
        if not changed:
            raise AuthenticationFailed
        await asyncio.to_thread(self._store.clear_auth_attempts, user.email)

    async def logout(self, token: str) -> bool:
        """Revoke `token` and report whether its session existed."""
        return await asyncio.to_thread(self._store.delete_session, hash_session_token(token))

    async def list_users(self, limit: int, offset: int) -> list[ManagedAuthUser]:
        """Return a bounded page using `limit` and `offset` for the admin dashboard."""
        if not 1 <= limit <= 50 or offset < 0:
            raise AdminActionRejected
        return await asyncio.to_thread(self._store.list_managed_users, limit, offset)

    async def list_admin_audit_events(self, limit: int, offset: int) -> list[AdminAuditEvent]:
        """Return one bounded page of audit events without blocking the async API thread."""
        if not 1 <= limit <= 50 or offset < 0:
            raise AdminActionRejected
        return await asyncio.to_thread(self._store.list_admin_audit_events, limit, offset)

    async def update_user(
        self,
        actor_user_id: str,
        target_user_id: str,
        role: str,
        is_active: bool,
    ) -> ManagedAuthUser:
        """Apply an audited access change, preventing self-lockout and last-admin removal."""
        if actor_user_id == target_user_id or role not in {"user", "admin"}:
            raise AdminActionRejected
        try:
            user = await asyncio.to_thread(
                self._store.update_managed_user,
                actor_user_id,
                target_user_id,
                role,
                is_active,
            )
        except LastActiveAdminError as error:
            raise AdminActionRejected from error
        if user is None:
            raise AdminUserNotFound
        return user

    async def _check_attempt_limit(self, email: str) -> None:
        """Count `email`'s attempt and stop the operation after the configured window limit."""
        attempt_count = await asyncio.to_thread(self._store.record_auth_attempt, email)
        if attempt_count > MAX_AUTH_ATTEMPTS:
            raise AuthenticationRateLimited
