"""Apply additive, versioned SQLite schema changes for multi-user features."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

CURRENT_VERSION = 1

MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        """CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            email TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX idx_sessions_user_expires ON sessions (user_id, expires_at)",
        """CREATE TABLE login_attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_email TEXT NOT NULL,
            attempted_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX idx_login_attempts_email_time ON login_attempts (normalized_email, attempted_at)",
        """CREATE TABLE conversations (
            conversation_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX idx_conversations_owner_updated ON conversations (owner_id, updated_at DESC)",
        """CREATE TABLE messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX idx_messages_conversation_created ON messages (conversation_id, created_at, message_id)",
        """CREATE TABLE user_preferences (
            user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            preferences_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE runtime_ai_config (
            config_id INTEGER PRIMARY KEY CHECK (config_id = 1),
            config_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE admin_audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            target_user_id TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX idx_admin_audit_created ON admin_audit_events (created_at DESC, event_id DESC)",
    ),
)


def apply_migrations(database_path: str | Path) -> int:
    """Apply pending schema migrations atomically and return the current version."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        for version, statements in enumerate(MIGRATIONS, start=1):
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if applied:
                continue
            with connection:
                connection.execute("BEGIN")
                for statement in statements:
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
    return CURRENT_VERSION
