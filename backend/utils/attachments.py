"""Validate bounded attachment uploads by content and sanitize display names."""
from __future__ import annotations

import re
from typing import Protocol

CHUNK_SIZE = 64 * 1024
FILENAME_ALLOWED = re.compile(r"[^A-Za-z0-9._ -]+")
MARKUP_PREFIX = re.compile(rb"^\s*(?:<\?xml[^>]*>\s*)?(?:<!doctype\s+html\b|<html\b|<svg\b)", re.IGNORECASE)


class AttachmentTooLarge(ValueError):
    """Signal an upload that exceeds the configured byte limit."""


class UnsupportedAttachment(ValueError):
    """Signal unsupported, malformed, or disabled attachment content."""


class AttachmentNotFound(LookupError):
    """Signal a missing attachment or an ownership mismatch."""


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


async def read_upload(
    upload: AsyncReadable,
    *,
    max_bytes: int,
    vision_enabled: bool,
) -> tuple[bytes, str, str]:
    """Read at most the configured limit plus one byte and detect supported content."""
    chunks: list[bytes] = []
    size = 0
    while size <= max_bytes:
        chunk = await upload.read(min(CHUNK_SIZE, max_bytes + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size > max_bytes:
        raise AttachmentTooLarge("Attachment exceeds the configured size limit.")
    content = b"".join(chunks)
    kind, content_type = _detect_content(content)
    if kind == "image" and not vision_enabled:
        raise UnsupportedAttachment("Image analysis is not enabled for the current model.")
    return content, kind, content_type


def _detect_content(content: bytes) -> tuple[str, str]:
    """Recognize supported image signatures or safe UTF-8 text without NUL bytes."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image", "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image", "image/webp"
    if b"\x00" in content:
        raise UnsupportedAttachment("Unsupported attachment content.")
    if MARKUP_PREFIX.match(content):
        raise UnsupportedAttachment("Unsupported attachment content.")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnsupportedAttachment("Unsupported attachment content.") from error
    return "text", "text/plain"


def sanitize_filename(filename: str | None) -> str:
    """Keep a bounded, portable basename for display without path components."""
    basename = (filename or "attachment").replace("\\", "/").rsplit("/", 1)[-1]
    sanitized = FILENAME_ALLOWED.sub("_", basename).strip(" .")[:100]
    return sanitized or "attachment"
