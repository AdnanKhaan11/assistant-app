"""Store small, private user preferences in the existing SQLite preferences table."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3


DEFAULT_THEME = "light"


@dataclass(frozen=True)
class UserSettingsRecord:
    """Represent the supported theme preference and its last update time."""

    theme: str
    updated_at: str | None


class UserSettingsStore:
    """Keep preference SQL scoped to a single account and its existing database."""

    def __init__(self, database_path: str | Path) -> None:
        """Retain the configured database path without opening a connection."""
        self._path = Path(database_path)

    def get(self, user_id: str) -> UserSettingsRecord:
        """Return the account's theme, using the light theme when no row exists."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT preferences_json, updated_at FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return UserSettingsRecord(DEFAULT_THEME, None)
        preferences = self._parse_preferences(row[0])
        theme = preferences.get("theme")
        is_supported_theme = isinstance(theme, str) and theme in {"light", "dark"}
        return UserSettingsRecord(theme if is_supported_theme else DEFAULT_THEME, str(row[1]))

    def set_theme(self, user_id: str, theme: str) -> UserSettingsRecord:
        """Save one validated theme while preserving other stored preference keys."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT preferences_json FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            preferences = self._parse_preferences(row[0]) if row is not None else {}
            preferences["theme"] = theme
            connection.execute(
                """INSERT INTO user_preferences (user_id, preferences_json) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       preferences_json = excluded.preferences_json,
                       updated_at = datetime('now')""",
                (user_id, json.dumps(preferences, separators=(",", ":"), sort_keys=True)),
            )
        return self.get(user_id)

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived connection with foreign-key enforcement enabled."""
        connection = sqlite3.connect(self._path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _parse_preferences(value: object) -> dict[str, object]:
        """Decode a stored preference object, treating invalid JSON as empty settings."""
        try:
            preferences = json.loads(str(value))
        except json.JSONDecodeError:
            return {}
        return preferences if isinstance(preferences, dict) else {}
