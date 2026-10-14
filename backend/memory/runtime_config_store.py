"""Persist non-secret runtime AI settings and their audit events in SQLite."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from backend.memory.migrations import apply_migrations


@dataclass(frozen=True)
class RuntimeAIConfigRecord:
    """Represent one saved provider/model pair without credentials."""

    provider: str
    model: str
    updated_at: str


class RuntimeAIConfigStore:
    """Keep runtime configuration SQL and audit writes behind a short-lived SQLite boundary."""

    def __init__(self, database_path: str | Path) -> None:
        """Use the existing additive migrations for `database_path` and retain its location."""
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        apply_migrations(self._path)

    def get(self) -> RuntimeAIConfigRecord | None:
        """Read the saved provider/model pair, returning `None` before the first save."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT config_json, updated_at FROM runtime_ai_config WHERE config_id = 1"
            ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def save(self, provider: str, model: str, actor_user_id: str) -> RuntimeAIConfigRecord:
        """Save a validated non-secret pair and audit its change atomically."""
        config_json = json.dumps(
            {"provider": provider, "model": model},
            separators=(",", ":"),
            sort_keys=True,
        )
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            previous_row = connection.execute(
                "SELECT config_json FROM runtime_ai_config WHERE config_id = 1"
            ).fetchone()
            previous = self._previous_config(previous_row)
            connection.execute(
                """INSERT INTO runtime_ai_config (config_id, config_json) VALUES (1, ?)
                   ON CONFLICT(config_id) DO UPDATE SET
                       config_json = excluded.config_json,
                       updated_at = datetime('now')""",
                (config_json,),
            )
            details = {"to_provider": provider, "to_model": model}
            if previous is not None:
                details.update({"from_provider": previous[0], "from_model": previous[1]})
            connection.execute(
                """INSERT INTO admin_audit_events
                   (actor_user_id, action, details_json) VALUES (?, ?, ?)""",
                (
                    actor_user_id,
                    "runtime_ai_config.saved",
                    json.dumps(details, separators=(",", ":"), sort_keys=True),
                ),
            )
            row = connection.execute(
                "SELECT config_json, updated_at FROM runtime_ai_config WHERE config_id = 1"
            ).fetchone()
        return self._record_from_row(row)

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived database connection with foreign-key enforcement enabled."""
        connection = sqlite3.connect(self._path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @classmethod
    def _record_from_row(cls, row: tuple[object, ...] | None) -> RuntimeAIConfigRecord:
        """Convert a persisted JSON row and reject malformed configuration clearly."""
        if row is None:
            raise ValueError("A runtime configuration row is required.")
        config = json.loads(str(row[0]))
        if not isinstance(config, dict):
            raise ValueError("Saved runtime configuration must be a JSON object.")
        provider = config.get("provider")
        model = config.get("model")
        if not isinstance(provider, str) or not isinstance(model, str):
            raise ValueError("Saved runtime configuration requires provider and model strings.")
        return RuntimeAIConfigRecord(provider, model, str(row[1]))

    @staticmethod
    def _previous_config(row: tuple[object, ...] | None) -> tuple[str, str] | None:
        """Read the prior provider/model pair used to describe a non-secret audit change."""
        if row is None:
            return None
        config = json.loads(str(row[0]))
        if not isinstance(config, dict):
            raise ValueError("Saved runtime configuration must be a JSON object.")
        provider, model = config.get("provider"), config.get("model")
        if not isinstance(provider, str) or not isinstance(model, str):
            raise ValueError("Saved runtime configuration requires provider and model strings.")
        return provider, model
