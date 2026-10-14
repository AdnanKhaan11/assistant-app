"""Persist structured ticket turns in SQLite as the durable memory source."""
from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    category   TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_turns_ticket ON turns (ticket_id, id);
"""


class LongTermMemory:
    """Store and retrieve durable ticket turns without exposing SQLite to callers."""

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.executescript(SCHEMA)

    def save_turn(self, ticket_id: str, role: str, content: str, category: str | None = None) -> None:
        """Persist one conversation turn using bound SQL parameters."""
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO turns (ticket_id, role, content, category) VALUES (?, ?, ?, ?)",
                    (ticket_id, role, content, category),
                )

    def recent(self, ticket_id: str, limit: int = 20) -> list[dict[str, str | None]]:
        """Return the newest limited turns in chronological order for one ticket."""
        if limit <= 0:
            return []

        query = """
            SELECT role, content, category, created_at
            FROM (
                SELECT id, role, content, category, created_at
                FROM turns
                WHERE ticket_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
        """
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                rows = connection.execute(query, (ticket_id, limit)).fetchall()
        return [
            {"role": role, "content": content, "category": category, "created_at": created_at}
            for role, content, category, created_at in rows
        ]

    def delete_ticket(self, ticket_id: str) -> None:
        """Delete all persisted turns for one ticket."""
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute("DELETE FROM turns WHERE ticket_id = ?", (ticket_id,))
