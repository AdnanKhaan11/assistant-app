"""Store private conversations in SQLite; each operation scopes rows to their authenticated owner."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from uuid import uuid4

from backend.memory.migrations import apply_migrations


@dataclass(frozen=True)
class ConversationRecord:
    """Describe one conversation without exposing its messages or another owner's data."""

    conversation_id: str
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ConversationMessageRecord:
    """Describe one stored message and its server-assigned conversation order."""

    message_id: str
    role: str
    content: str
    created_at: str
    updated_at: str


class ConversationStore:
    """Keep conversation and message SQL behind owner-scoped persistence methods."""

    def __init__(self, database_path: str | Path) -> None:
        """Prepare `database_path` and ensure the versioned conversation schema exists."""
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        apply_migrations(self._path)

    def create(self, owner_id: str, title: str, conversation_id: str) -> ConversationRecord:
        """Create `conversation_id` for `owner_id` with its initial `title`."""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO conversations (conversation_id, owner_id, title) VALUES (?, ?, ?)",
                (conversation_id, owner_id, title),
            )
            row = connection.execute(
                "SELECT conversation_id, title, created_at, updated_at "
                "FROM conversations WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            ).fetchone()
        return self._conversation_from_row(row)

    def list_for_owner(self, owner_id: str, limit: int, offset: int) -> list[ConversationRecord]:
        """Return a bounded page for `owner_id`, newest conversation first."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT conversation_id, title, created_at, updated_at FROM conversations
                   WHERE owner_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT ? OFFSET ?""",
                (owner_id, limit, offset),
            ).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def get_for_owner(self, owner_id: str, conversation_id: str) -> ConversationRecord | None:
        """Find `conversation_id` only when it belongs to `owner_id`."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT conversation_id, title, created_at, updated_at FROM conversations
                   WHERE conversation_id = ? AND owner_id = ?""",
                (conversation_id, owner_id),
            ).fetchone()
        return self._conversation_from_row(row) if row is not None else None

    def rename(self, owner_id: str, conversation_id: str, title: str) -> bool:
        """Set `title` only on `conversation_id` owned by `owner_id`."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE conversations SET title = ?, updated_at = datetime('now')
                   WHERE conversation_id = ? AND owner_id = ?""",
                (title, conversation_id, owner_id),
            )
        return cursor.rowcount == 1

    def delete(self, owner_id: str, conversation_id: str) -> bool:
        """Delete one owned conversation, allowing SQLite to cascade its messages."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            )
        return cursor.rowcount == 1

    def append_message(
        self,
        owner_id: str,
        conversation_id: str,
        message_id: str,
        role: str,
        content: str,
    ) -> bool:
        """Append `content` for `role` only if `owner_id` owns the target conversation."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """INSERT INTO messages (message_id, conversation_id, role, content)
                   SELECT ?, conversation_id, ?, ? FROM conversations
                   WHERE conversation_id = ? AND owner_id = ?""",
                (message_id, role, content, conversation_id, owner_id),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    "UPDATE conversations SET updated_at = datetime('now') "
                    "WHERE conversation_id = ? AND owner_id = ?",
                    (conversation_id, owner_id),
                )
        return cursor.rowcount == 1

    def messages_for_owner(
        self,
        owner_id: str,
        conversation_id: str,
        limit: int,
        offset: int,
    ) -> list[ConversationMessageRecord] | None:
        """Return an oldest-first page, or `None` when the conversation is not owned."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            exists = connection.execute(
                "SELECT 1 FROM conversations WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            ).fetchone()
            if exists is None:
                return None
            rows = connection.execute(
                """SELECT message_id, role, content, created_at, updated_at FROM messages
                   WHERE conversation_id = ? ORDER BY rowid LIMIT ? OFFSET ?""",
                (conversation_id, limit, offset),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def get_message_for_owner(
        self,
        owner_id: str,
        conversation_id: str,
        message_id: str,
    ) -> ConversationMessageRecord | None:
        """Find `message_id` only when its parent conversation belongs to `owner_id`."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT m.message_id, m.role, m.content, m.created_at, m.updated_at
                   FROM messages AS m JOIN conversations AS c
                   ON c.conversation_id = m.conversation_id
                   WHERE m.message_id = ? AND m.conversation_id = ? AND c.owner_id = ?""",
                (message_id, conversation_id, owner_id),
            ).fetchone()
        return self._message_from_row(row) if row is not None else None

    def edit_user_message(
        self,
        owner_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> bool:
        """Replace an owned user message and delete every later message in conversation order."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT m.rowid FROM messages AS m JOIN conversations AS c
                   ON c.conversation_id = m.conversation_id
                   WHERE m.message_id = ? AND m.conversation_id = ? AND c.owner_id = ?
                     AND m.role = 'user'""",
                (message_id, conversation_id, owner_id),
            ).fetchone()
            if row is None:
                return False
            message_rowid = int(row[0])
            connection.execute(
                "UPDATE messages SET content = ?, updated_at = datetime('now') WHERE rowid = ?",
                (content, message_rowid),
            )
            connection.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND rowid > ?",
                (conversation_id, message_rowid),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = datetime('now') "
                "WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            )
        return True

    def delete_message(self, owner_id: str, conversation_id: str, message_id: str) -> bool:
        """Delete an owned message and all later turns atomically to avoid stale replies."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT m.rowid FROM messages AS m JOIN conversations AS c
                   ON c.conversation_id = m.conversation_id
                   WHERE m.message_id = ? AND m.conversation_id = ? AND c.owner_id = ?""",
                (message_id, conversation_id, owner_id),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND rowid >= ?",
                (conversation_id, int(row[0])),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = datetime('now') "
                "WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            )
        return True

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived SQLite connection with foreign-key enforcement enabled."""
        connection = sqlite3.connect(self._path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _conversation_from_row(row: tuple[object, ...] | None) -> ConversationRecord:
        """Convert a selected conversation row into an immutable domain record."""
        if row is None:
            raise ValueError("A conversation row is required.")
        conversation_id, title, created_at, updated_at = row
        return ConversationRecord(
            str(conversation_id), str(title), str(created_at), str(updated_at)
        )

    @staticmethod
    def _message_from_row(row: tuple[object, ...]) -> ConversationMessageRecord:
        """Convert a selected message row into an immutable domain record."""
        message_id, role, content, created_at, updated_at = row
        return ConversationMessageRecord(
            str(message_id), str(role), str(content), str(created_at), str(updated_at)
        )
