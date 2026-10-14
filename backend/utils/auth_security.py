"""Provide password hashing and opaque session-token helpers without external packages."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_HASH_SCHEME = "pbkdf2_sha256"
_HASH_ITERATIONS = 600_000
_MIN_HASH_ITERATIONS = 100_000
_MAX_HASH_ITERATIONS = 1_000_000
_SALT_BYTES = 16
_TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    """Hash a password for storage; `password` is the submitted plaintext secret."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _HASH_ITERATIONS)
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"{_HASH_SCHEME}${_HASH_ITERATIONS}${salt_text}${digest_text}"


def verify_password(password: str, encoded_hash: str) -> bool:
    """Check `password` against the encoded hash without leaking comparison timing."""
    try:
        scheme, iterations_text, salt_text, digest_text = encoded_hash.split("$", maxsplit=3)
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_text, altchars=b"-_", validate=True)
        expected = base64.b64decode(digest_text, altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return False
    if (
        scheme != _HASH_SCHEME
        or not _MIN_HASH_ITERATIONS <= iterations <= _MAX_HASH_ITERATIONS
        or not salt
        or not expected
    ):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def create_session_token() -> str:
    """Create a cryptographically random bearer token to send only to its user."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    """Hash `token` before database storage so a database leak cannot reveal sessions."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
