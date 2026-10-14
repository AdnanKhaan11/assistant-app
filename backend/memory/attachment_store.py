"""Persist attachment bytes and ticket-scoped metadata in the configured upload directory."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import sqlite3
from pathlib import Path

from backend.utils.attachments import AttachmentNotFound

SCHEMA = """
CREATE TABLE IF NOT EXISTS attachments (
    attachment_id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    kind TEXT NOT NULL,
    size_bytes INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_ticket ON attachments (ticket_id, attachment_id);
"""


@dataclass(frozen=True)
class StoredAttachment:
    """Describe stored metadata and bytes returned only to the owning ticket."""

    attachment_id: str
    ticket_id: str
    filename: str
    content_type: str
    kind: str
    size_bytes: int
    content: bytes


class AttachmentStore:
    """Own attachment file paths and metadata, keeping persistence behind a small API."""

    def __init__(self, db_path: str, upload_dir: str) -> None:
        self._db_path = Path(db_path)
        self._upload_dir = Path(upload_dir).resolve()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._db_path)) as connection:
            with connection:
                connection.executescript(SCHEMA)

    def save(
        self,
        *,
        ticket_id: str,
        attachment_id: str,
        filename: str,
        content_type: str,
        kind: str,
        content: bytes,
    ) -> None:
        """Write bytes and ticket metadata with a server-generated id."""
        path = self._attachment_path(ticket_id, attachment_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as attachment_file:
            attachment_file.write(content)
        try:
            with closing(sqlite3.connect(self._db_path)) as connection:
                with connection:
                    connection.execute(
                        """INSERT INTO attachments
                           (attachment_id, ticket_id, filename, content_type, kind, size_bytes)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (attachment_id, ticket_id, filename, content_type, kind, len(content)),
                    )
        except sqlite3.Error:
            path.unlink(missing_ok=True)
            raise

    def get(self, ticket_id: str, attachment_id: str) -> StoredAttachment:
        """Read one attachment only when its metadata confirms ticket ownership."""
        with closing(sqlite3.connect(self._db_path)) as connection:
            row = connection.execute(
                """SELECT filename, content_type, kind, size_bytes
                   FROM attachments WHERE ticket_id = ? AND attachment_id = ?""",
                (ticket_id, attachment_id),
            ).fetchone()
        if row is None:
            raise AttachmentNotFound("Attachment not found.")
        try:
            content = self._attachment_path(ticket_id, attachment_id).read_bytes()
        except FileNotFoundError as error:
            raise AttachmentNotFound("Attachment not found.") from error
        return StoredAttachment(attachment_id, ticket_id, *row, content)

    def delete_ticket(self, ticket_id: str) -> None:
        """Remove a ticket's stored files and metadata rows."""
        with closing(sqlite3.connect(self._db_path)) as connection:
            with connection:
                rows = connection.execute(
                    "SELECT attachment_id FROM attachments WHERE ticket_id = ?", (ticket_id,)
                ).fetchall()
                connection.execute("DELETE FROM attachments WHERE ticket_id = ?", (ticket_id,))
        for (attachment_id,) in rows:
            self._attachment_path(ticket_id, attachment_id).unlink(missing_ok=True)
        ticket_directory = (self._upload_dir / ticket_id).resolve()
        if self._upload_dir in ticket_directory.parents:
            try:
                ticket_directory.rmdir()
            except OSError:
                pass

    def _attachment_path(self, ticket_id: str, attachment_id: str) -> Path:
        """Resolve a ticket-scoped path and reject any escape from the upload root."""
        path = (self._upload_dir / ticket_id / attachment_id).resolve()
        if self._upload_dir not in path.parents:
            raise ValueError("Attachment path must remain inside the upload directory.")
        return path
