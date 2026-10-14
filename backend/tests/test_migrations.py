from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from backend.memory.migrations import apply_migrations


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def test_migrations_create_versioned_schema_and_are_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "assistant.db"

    assert apply_migrations(database_path) == 1
    assert apply_migrations(database_path) == 1

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]
        assert {
            "users",
            "sessions",
            "login_attempts",
            "conversations",
            "messages",
            "user_preferences",
            "runtime_ai_config",
            "admin_audit_events",
        } <= _table_names(connection)


def test_migrations_leave_legacy_turns_and_attachments_unchanged(tmp_path: Path) -> None:
    database_path = tmp_path / "assistant.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """CREATE TABLE turns (id INTEGER PRIMARY KEY, ticket_id TEXT, content TEXT);
            CREATE TABLE attachments (attachment_id TEXT PRIMARY KEY, ticket_id TEXT);
            INSERT INTO turns VALUES (1, 'legacy-ticket', 'legacy turn');
            INSERT INTO attachments VALUES ('legacy-attachment', 'legacy-ticket');"""
        )
        before = {
            table: connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()[0]
            for table in ("turns", "attachments")
        }

    apply_migrations(database_path)

    with sqlite3.connect(database_path) as connection:
        after = {
            table: connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()[0]
            for table in ("turns", "attachments")
        }
        assert after == before
        assert connection.execute("SELECT * FROM turns").fetchall() == [
            (1, "legacy-ticket", "legacy turn")
        ]
        assert connection.execute("SELECT * FROM attachments").fetchall() == [
            ("legacy-attachment", "legacy-ticket")
        ]


def test_migrations_enforce_user_roles_and_conversation_ownership(tmp_path: Path) -> None:
    database_path = tmp_path / "assistant.db"
    apply_migrations(database_path)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
            ("user-1", "person@example.test", "hash"),
        )
        connection.execute(
            "INSERT INTO conversations (conversation_id, owner_id, title) VALUES (?, ?, ?)",
            ("conversation-1", "user-1", "Private"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO users (user_id, email, password_hash, role) VALUES (?, ?, ?, ?)",
                ("user-2", "other@example.test", "hash", "superuser"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO conversations (conversation_id, owner_id, title) VALUES (?, ?, ?)",
                ("conversation-2", "missing-user", "Orphan"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
                ("user-3", "PERSON@example.test", "hash"),
            )


def test_failed_migration_rolls_back_schema_and_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.memory.migrations as migrations

    database_path = tmp_path / "assistant.db"
    monkeypatch.setattr(migrations, "MIGRATIONS", (("CREATE TABLE transient (id INTEGER)", "INVALID SQL"),))

    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(database_path)

    with sqlite3.connect(database_path) as connection:
        assert "transient" not in _table_names(connection)
        assert connection.execute("SELECT * FROM schema_migrations").fetchall() == []
