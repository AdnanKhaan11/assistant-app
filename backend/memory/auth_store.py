"""Store user credentials and sessions in SQLite; return only the records auth needs."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
from uuid import uuid4

from backend.memory.migrations import apply_migrations


@dataclass(frozen=True)
class StoredAuthUser:
    """Hold the private account fields needed to verify credentials and identify a session."""

    user_id: str
    email: str
    password_hash: str
    role: str
    is_active: bool
    created_at: str


@dataclass(frozen=True)
class ManagedAuthUser:
    """Expose administrator-managed account fields without credential material."""

    user_id: str
    email: str
    role: str
    is_active: bool
    created_at: str


@dataclass(frozen=True)
class AdminAuditEvent:
    """Hold safe account-audit metadata without credentials or session identifiers."""

    event_id: int
    action: str
    actor_email: str | None
    target_email: str | None
    details_json: str
    created_at: str


class LastActiveAdminError(Exception):
    """Signal that an update would leave the application without an active administrator."""


class AuthStore:
    """Keep account and session SQL behind a small synchronous persistence boundary."""

    def __init__(self, database_path: str | Path) -> None:
        """Prepare `database_path` and apply its pending additive schema migrations."""
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        apply_migrations(self._path)

    def create_user(self, email: str, password_hash: str) -> StoredAuthUser:
        """Create a regular user from `email` and a precomputed `password_hash`."""
        normalized_email = email.strip().casefold()
        user_id = uuid4().hex
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
                (user_id, normalized_email, password_hash),
            )
            row = connection.execute(
                "SELECT user_id, email, password_hash, role, is_active, created_at "
                "FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._user_from_row(row)

    def create_first_admin(self, email: str, password_hash: str) -> StoredAuthUser | None:
        """Create the first admin from `email` and `password_hash`, or refuse if one exists."""
        normalized_email = email.strip().casefold()
        user_id = uuid4().hex
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            admin_exists = connection.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
            if admin_exists:
                return None
            connection.execute(
                "INSERT INTO users (user_id, email, password_hash, role) VALUES (?, ?, ?, 'admin')",
                (user_id, normalized_email, password_hash),
            )
            row = connection.execute(
                "SELECT user_id, email, password_hash, role, is_active, created_at "
                "FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._user_from_row(row)

    def find_user_by_email(self, email: str) -> StoredAuthUser | None:
        """Find `email` case-insensitively for login without returning unrelated accounts."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id, email, password_hash, role, is_active, created_at "
                "FROM users WHERE email = ? COLLATE NOCASE",
                (email.strip(),),
            ).fetchone()
        return self._user_from_row(row) if row is not None else None

    def find_user_by_id(self, user_id: str) -> StoredAuthUser | None:
        """Find one account by its server-issued `user_id` for credential verification."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id, email, password_hash, role, is_active, created_at "
                "FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._user_from_row(row) if row is not None else None

    def list_managed_users(self, limit: int, offset: int) -> list[ManagedAuthUser]:
        """Return safe user fields in stable creation order for a bounded admin page."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT user_id, email, role, is_active, created_at FROM users
                   ORDER BY created_at, user_id LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [self._managed_user_from_row(row) for row in rows]

    def update_managed_user(
        self,
        actor_user_id: str,
        target_user_id: str,
        role: str,
        is_active: bool,
    ) -> ManagedAuthUser | None:
        """Update account access, revoke disabled sessions, and audit the change atomically."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT role, is_active FROM users WHERE user_id = ?",
                (target_user_id,),
            ).fetchone()
            if row is None:
                return None
            old_role, old_is_active = str(row[0]), bool(row[1])
            removes_admin = old_role == "admin" and old_is_active and (role != "admin" or not is_active)
            if removes_admin:
                active_admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND user_id != ?",
                    (target_user_id,),
                ).fetchone()
                if int(active_admins[0]) == 0:
                    raise LastActiveAdminError
            connection.execute(
                "UPDATE users SET role = ?, is_active = ?, updated_at = datetime('now') WHERE user_id = ?",
                (role, int(is_active), target_user_id),
            )
            if not is_active:
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (target_user_id,))
            details = json.dumps(
                {
                    "from_role": old_role,
                    "to_role": role,
                    "from_active": old_is_active,
                    "to_active": is_active,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            connection.execute(
                """INSERT INTO admin_audit_events (actor_user_id, action, target_user_id, details_json)
                   VALUES (?, ?, ?, ?)""",
                (actor_user_id, "user.account_updated", target_user_id, details),
            )
            updated = connection.execute(
                "SELECT user_id, email, role, is_active, created_at FROM users WHERE user_id = ?",
                (target_user_id,),
            ).fetchone()
        return self._managed_user_from_row(updated)

    def list_admin_audit_events(self, limit: int, offset: int) -> list[AdminAuditEvent]:
        """Return newest account audit events with safe actor and target email metadata."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT event.event_id, event.action, actor.email, target.email,
                          event.details_json, event.created_at
                   FROM admin_audit_events AS event
                   LEFT JOIN users AS actor ON actor.user_id = event.actor_user_id
                   LEFT JOIN users AS target ON target.user_id = event.target_user_id
                   ORDER BY event.event_id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [
            AdminAuditEvent(
                event_id=int(row[0]),
                action=str(row[1]),
                actor_email=str(row[2]) if row[2] is not None else None,
                target_email=str(row[3]) if row[3] is not None else None,
                details_json=str(row[4]),
                created_at=str(row[5]),
            )
            for row in rows
        ]

    def update_password(self, user_id: str, old_hash: str, new_hash: str) -> bool:
        """Replace `old_hash` with `new_hash` for `user_id` and revoke all its sessions atomically."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE users SET password_hash = ?, updated_at = datetime('now')
                   WHERE user_id = ? AND password_hash = ? AND is_active = 1""",
                (new_hash, user_id, old_hash),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return True

    def create_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        """Persist a hashed `token_hash` for `user_id` until the UTC `expires_at` timestamp."""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO sessions (session_id, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )

    def create_session_if_password_matches(
        self,
        token_hash: str,
        user_id: str,
        expires_at: str,
        expected_password_hash: str,
    ) -> bool:
        """Create a session only while `user_id` still has the hash verified by the caller."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """INSERT INTO sessions (session_id, user_id, expires_at)
                   SELECT ?, user_id, ? FROM users
                   WHERE user_id = ? AND password_hash = ? AND is_active = 1""",
                (token_hash, expires_at, user_id, expected_password_hash),
            )
        return cursor.rowcount == 1

    def record_auth_attempt(self, email: str) -> int:
        """Record an attempt for `email` and return its count in the rolling 15-minute window."""
        normalized_email = email.strip().casefold()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM login_attempts WHERE attempted_at < datetime('now', '-1 day')"
            )
            connection.execute(
                "INSERT INTO login_attempts (normalized_email) VALUES (?)",
                (normalized_email,),
            )
            row = connection.execute(
                """SELECT COUNT(*) FROM login_attempts
                   WHERE normalized_email = ? AND attempted_at >= datetime('now', '-15 minutes')""",
                (normalized_email,),
            ).fetchone()
        return int(row[0])

    def clear_auth_attempts(self, email: str) -> None:
        """Clear recent attempt history for `email` after a successful account operation."""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM login_attempts WHERE normalized_email = ?",
                (email.strip().casefold(),),
            )

    def find_active_session(self, token_hash: str) -> StoredAuthUser | None:
        """Resolve an unexpired `token_hash` only when its account is still active."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT u.user_id, u.email, u.password_hash, u.role, u.is_active, u.created_at
                   FROM sessions AS s JOIN users AS u ON u.user_id = s.user_id
                   WHERE s.session_id = ? AND julianday(s.expires_at) > julianday('now')
                     AND u.is_active = 1""",
                (token_hash,),
            ).fetchone()
        return self._user_from_row(row) if row is not None else None

    def delete_session(self, token_hash: str) -> bool:
        """Revoke `token_hash` and report whether a session row was removed."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("DELETE FROM sessions WHERE session_id = ?", (token_hash,))
        return cursor.rowcount == 1

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived connection with foreign-key checks enabled."""
        connection = sqlite3.connect(self._path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _user_from_row(row: tuple[object, ...] | None) -> StoredAuthUser:
        """Convert a selected account row to an immutable internal auth record."""
        if row is None:
            raise ValueError("A user row is required.")
        user_id, email, password_hash, role, is_active, created_at = row
        return StoredAuthUser(
            user_id=str(user_id),
            email=str(email),
            password_hash=str(password_hash),
            role=str(role),
            is_active=bool(is_active),
            created_at=str(created_at),
        )

    @staticmethod
    def _managed_user_from_row(row: tuple[object, ...]) -> ManagedAuthUser:
        """Convert a safe administrator listing row without password or session values."""
        user_id, email, role, is_active, created_at = row
        return ManagedAuthUser(
            user_id=str(user_id),
            email=str(email),
            role=str(role),
            is_active=bool(is_active),
            created_at=str(created_at),
        )
