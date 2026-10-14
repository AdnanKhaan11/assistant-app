"""Create the first admin locally; database location comes from app settings and passwords stay hidden."""
from __future__ import annotations

import getpass
import re
import sqlite3
import sys

from backend.memory.auth_store import AuthStore
from backend.utils.auth_security import hash_password
from backend.utils.config import get_settings

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LENGTH = 12
_MAX_PASSWORD_LENGTH = 128


def main() -> int:
    """Prompt for initial admin credentials and return a shell-friendly result code."""
    email = input("Admin email: ").strip()
    if not _EMAIL_PATTERN.fullmatch(email):
        print("Enter a valid email address.", file=sys.stderr)
        return 2
    password = getpass.getpass("Admin password (12-128 characters): ")
    if not _MIN_PASSWORD_LENGTH <= len(password) <= _MAX_PASSWORD_LENGTH:
        print("Password must be between 12 and 128 characters.", file=sys.stderr)
        return 2
    confirmation = getpass.getpass("Confirm admin password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    try:
        admin = AuthStore(get_settings().db_path).create_first_admin(email, hash_password(password))
    except sqlite3.IntegrityError:
        print("An account already uses that email.", file=sys.stderr)
        return 1
    if admin is None:
        print("An administrator already exists.", file=sys.stderr)
        return 1
    print("The first administrator account was created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
