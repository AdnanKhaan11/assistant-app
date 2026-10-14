"""Test content-based attachment validation and safe display names."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.utils.attachments import AttachmentTooLarge, UnsupportedAttachment, read_upload, sanitize_filename


@dataclass
class _Upload:
    content: bytes
    requested: list[int]

    async def read(self, size: int = -1) -> bytes:
        self.requested.append(size)
        data, self.content = self.content[:size], self.content[size:]
        return data


@pytest.mark.parametrize(
    ("content", "expected_type"),
    [
        (b"\x89PNG\r\n\x1a\nimage", "image/png"),
        (b"\xff\xd8\xffimage", "image/jpeg"),
        (b"GIF89aimage", "image/gif"),
        (b"RIFF1234WEBPimage", "image/webp"),
    ],
)
async def test_recognizes_supported_image_signatures(content: bytes, expected_type: str) -> None:
    upload = _Upload(content, [])

    data, kind, content_type = await read_upload(upload, max_bytes=100, vision_enabled=True)

    assert data == content
    assert kind == "image"
    assert content_type == expected_type


async def test_accepts_plain_utf8_text_and_rejects_images_when_vision_is_off() -> None:
    text, kind, content_type = await read_upload(_Upload(b"diagnostic log", []), max_bytes=100, vision_enabled=False)
    assert (text, kind, content_type) == (b"diagnostic log", "text", "text/plain")

    with pytest.raises(UnsupportedAttachment, match="Image analysis is not enabled"):
        await read_upload(_Upload(b"\x89PNG\r\n\x1a\nimage", []), max_bytes=100, vision_enabled=False)


@pytest.mark.parametrize("content", [b"MZ\x00\x00binary", b"\xff\xfe", b"<svg></svg>"])
async def test_rejects_binary_or_unsupported_content(content: bytes) -> None:
    with pytest.raises(UnsupportedAttachment):
        await read_upload(_Upload(content, []), max_bytes=100, vision_enabled=True)


async def test_oversize_upload_reads_no_more_than_limit_plus_one() -> None:
    upload = _Upload(b"x" * 100, [])

    with pytest.raises(AttachmentTooLarge):
        await read_upload(upload, max_bytes=10, vision_enabled=False)

    assert sum(upload.requested) == 11
    assert len(upload.content) == 89


def test_sanitizes_traversal_and_limits_filename_length() -> None:
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename(r"..\..\secret?.txt") == "secret_.txt"
    assert len(sanitize_filename("x" * 120)) == 100
