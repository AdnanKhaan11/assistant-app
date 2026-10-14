"""Test attachment metadata ownership, path containment, and ticket deletion."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.memory.attachment_store import AttachmentStore
from backend.utils.attachments import AttachmentNotFound


def test_store_reads_only_for_owner_and_deletes_files_and_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "assistant.db")
    upload_dir = tmp_path / "uploads"
    store = AttachmentStore(db_path, str(upload_dir))
    store.save(
        ticket_id="T-1",
        attachment_id="a" * 32,
        filename="capture.png",
        content_type="image/png",
        kind="image",
        content=b"png-bytes",
    )
    stored_path = upload_dir / "T-1" / ("a" * 32)

    stored = store.get("T-1", "a" * 32)
    assert stored.filename == "capture.png"
    assert stored.content == b"png-bytes"
    assert stored.size_bytes == 9
    with pytest.raises(AttachmentNotFound):
        store.get("T-2", "a" * 32)

    store.delete_ticket("T-1")

    assert not stored_path.exists()
    with pytest.raises(AttachmentNotFound):
        store.get("T-1", "a" * 32)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM attachments").fetchone()[0] == 0


def test_store_rejects_paths_outside_upload_root(tmp_path: Path) -> None:
    store = AttachmentStore(str(tmp_path / "assistant.db"), str(tmp_path / "uploads"))

    with pytest.raises(ValueError, match="inside the upload directory"):
        store.save(
            ticket_id="..",
            attachment_id="../outside",
            filename="x.txt",
            content_type="text/plain",
            kind="text",
            content=b"x",
        )


def test_store_does_not_overwrite_existing_attachment(tmp_path: Path) -> None:
    store = AttachmentStore(str(tmp_path / "assistant.db"), str(tmp_path / "uploads"))
    values = {
        "ticket_id": "T-1",
        "attachment_id": "b" * 32,
        "filename": "one.txt",
        "content_type": "text/plain",
        "kind": "text",
    }
    store.save(**values, content=b"first")

    with pytest.raises(FileExistsError):
        store.save(**values, content=b"second")

    assert store.get("T-1", "b" * 32).content == b"first"
