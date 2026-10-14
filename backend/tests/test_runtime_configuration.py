"""Test runtime provider configuration validation and its durable, audited SQLite record."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Sequence

from backend.llm.types import LLMResponse, Message, Usage
from backend.memory.runtime_config_store import RuntimeAIConfigStore
from backend.services.runtime_configuration import (
    RuntimeConfigurationRejected,
    RuntimeConfigurationService,
)
from backend.utils.config import Settings


class FakeProvider:
    """Provide an offline provider stub for configuration validation tests."""

    provider_name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        """Track asynchronous cleanup without creating a network client."""
        self.was_closed = False

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Satisfy the provider interface without contacting a model API."""
        return LLMResponse(text="ok", model=self.model, provider=self.provider_name, usage=Usage())

    async def aclose(self) -> None:
        """Record cleanup requested by the validation service."""
        self.was_closed = True


def _create_admin(database_path: Path) -> None:
    """Create the referenced test actor required by the audit table's foreign key."""
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
            ("admin-id", "admin@example.test", "test-hash"),
        )


def test_store_saves_only_non_secret_settings_and_audits_changes(tmp_path: Path) -> None:
    """Verify saved configuration and its audit event commit together without secret fields."""
    store = RuntimeAIConfigStore(tmp_path / "assistant.db")
    _create_admin(tmp_path / "assistant.db")

    first = store.save("groq", "llama-model", "admin-id")
    second = store.save("openai", "gpt-model", "admin-id")

    assert store.get() == second
    assert first.provider == "groq"
    assert second.provider == "openai"
    with sqlite3.connect(tmp_path / "assistant.db") as connection:
        config_json = connection.execute(
            "SELECT config_json FROM runtime_ai_config WHERE config_id = 1"
        ).fetchone()[0]
        events = connection.execute(
            "SELECT action, details_json FROM admin_audit_events ORDER BY event_id"
        ).fetchall()
    assert json.loads(config_json) == {"model": "gpt-model", "provider": "openai"}
    assert [event[0] for event in events] == ["runtime_ai_config.saved", "runtime_ai_config.saved"]
    assert json.loads(events[1][1]) == {
        "from_model": "llama-model",
        "from_provider": "groq",
        "to_model": "gpt-model",
        "to_provider": "openai",
    }
    assert "api_key" not in config_json
    assert "secret" not in events[1][1]


def test_status_separates_startup_active_settings_from_saved_configuration(tmp_path: Path) -> None:
    """Show environment-active settings separately from the saved provider/model pair."""
    settings = Settings(llm_provider="groq", llm_model="custom-groq-model", llm_api_key="secret")
    store = RuntimeAIConfigStore(tmp_path / "assistant.db")
    _create_admin(tmp_path / "assistant.db")
    store.save("openai", "gpt-4o-mini", "admin-id")
    service = RuntimeConfigurationService(settings, store)

    status = asyncio.run(service.status())

    assert status.active_provider == "groq"
    assert status.active_model == "custom-groq-model"
    assert status.saved is not None
    assert status.saved.provider == "openai"
    groq = next(option for option in status.providers if option.provider == "groq")
    assert set(groq.models) == {"custom-groq-model", "llama-3.3-70b-versatile"}
    assert not hasattr(status, "api_key")


def test_validation_uses_registry_defaults_and_a_closable_fake_factory(tmp_path: Path) -> None:
    """Validate a listed model and close its provider without sending any network request."""
    created: list[tuple[str, str | None, str | None, FakeProvider]] = []

    def provider_factory(provider: str, model: str | None, api_key: str | None) -> FakeProvider:
        fake = FakeProvider()
        created.append((provider, model, api_key, fake))
        return fake

    service = RuntimeConfigurationService(
        Settings(llm_provider="groq", llm_api_key="test-key"),
        RuntimeAIConfigStore(tmp_path / "assistant.db"),
        provider_factory,
    )

    result = asyncio.run(service.validate("OPENAI", "gpt-4o-mini"))

    assert result.is_valid is True
    assert (created[0][0], created[0][1], created[0][2]) == ("openai", "gpt-4o-mini", "test-key")
    assert created[0][3].was_closed is True


def test_validation_rejects_unknown_models_without_constructing_a_provider(tmp_path: Path) -> None:
    """Reject unlisted model IDs before provider construction or persistence."""
    store = RuntimeAIConfigStore(tmp_path / "assistant.db")
    service = RuntimeConfigurationService(
        Settings(llm_provider="groq"),
        store,
        lambda *_: (_ for _ in ()).throw(AssertionError("factory must not run")),
    )

    result = asyncio.run(service.validate("openai", "unlisted-model"))

    assert result.is_valid is False
    assert result.message == "The selected model is not in the supported model list."
    try:
        asyncio.run(service.save("openai", "unlisted-model", "admin-id"))
    except RuntimeConfigurationRejected as error:
        assert str(error) == result.message
    else:
        raise AssertionError("invalid settings must not be saved")
    assert store.get() is None
