from __future__ import annotations

import pytest

from backend.utils.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_from_env_uses_defaults_and_normalizes_optional_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_RETRIES",
        "MEMORY_WINDOW_TURNS",
        "DB_PATH",
        "TICKETS_PATH",
        "UPLOAD_DIR",
        "MAX_UPLOAD_BYTES",
        "MAX_ATTACHMENTS_PER_MESSAGE",
        "LLM_SUPPORTS_VISION",
        "STREAM_MAX_SECONDS",
        "KNOWLEDGE_PDF_PATH",
        "HF_TOKEN",
        "HF_EMBEDDING_MODEL",
        "HF_EMBEDDING_URL",
        "QDRANT_URL",
        "QDRANT_API_KEY",
        "QDRANT_COLLECTION",
    ):
        monkeypatch.setenv(name, "")

    settings = Settings.from_env()

    assert settings.llm_provider == "groq"
    assert settings.llm_model is None
    assert settings.llm_api_key is None
    assert settings.llm_timeout_seconds == 30.0
    assert settings.llm_max_retries == 2
    assert settings.memory_window_turns == 10
    assert settings.db_path == "data/assistant.db"
    assert settings.tickets_path == "data/sample_tickets.json"
    assert settings.upload_dir == "data/uploads"
    assert settings.max_upload_bytes == 5_242_880
    assert settings.max_attachments_per_message == 3
    assert settings.llm_supports_vision is False
    assert settings.stream_max_seconds == 120
    assert settings.knowledge_pdf_path == "data/uploads/IT_Troubleshooting_Playbook.pdf"
    assert settings.hf_token is None
    assert settings.hf_embedding_model == "thenlper/gte-large"
    assert settings.hf_embedding_url == "https://router.huggingface.co/hf-inference/models"
    assert settings.qdrant_url is None
    assert settings.qdrant_api_key is None
    assert settings.qdrant_collection == "troubleshooting_knowledge"


def test_from_env_reads_values(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "gpt-test",
        "LLM_API_KEY": "secret-test-key",
        "LLM_TIMEOUT_SECONDS": "12.5",
        "LLM_MAX_RETRIES": "4",
        "MEMORY_WINDOW_TURNS": "6",
        "DB_PATH": "tmp/memory.db",
        "TICKETS_PATH": "tmp/tickets.json",
        "UPLOAD_DIR": "tmp/uploads",
        "MAX_UPLOAD_BYTES": "2048",
        "MAX_ATTACHMENTS_PER_MESSAGE": "5",
        "LLM_SUPPORTS_VISION": "true",
        "STREAM_MAX_SECONDS": "90",
        "KNOWLEDGE_PDF_PATH": "data/kb/book.pdf",
        "HF_TOKEN": "hf-test-token",
        "HF_EMBEDDING_MODEL": "org/model-test",
        "HF_EMBEDDING_URL": "https://hf.example/embeddings",
        "QDRANT_URL": "http://qdrant.test:6333",
        "QDRANT_API_KEY": "qdrant-test-key",
        "QDRANT_COLLECTION": "kb-test",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings.from_env()

    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-test"
    assert settings.llm_api_key == "secret-test-key"
    assert settings.llm_timeout_seconds == 12.5
    assert settings.llm_max_retries == 4
    assert settings.memory_window_turns == 6
    assert settings.db_path == "tmp/memory.db"
    assert settings.tickets_path == "tmp/tickets.json"
    assert settings.upload_dir == "tmp/uploads"
    assert settings.max_upload_bytes == 2048
    assert settings.max_attachments_per_message == 5
    assert settings.llm_supports_vision is True
    assert settings.stream_max_seconds == 90
    assert settings.knowledge_pdf_path == "data/kb/book.pdf"
    assert settings.hf_token == "hf-test-token"
    assert settings.hf_embedding_model == "org/model-test"
    assert settings.hf_embedding_url == "https://hf.example/embeddings"
    assert settings.qdrant_url == "http://qdrant.test:6333"
    assert settings.qdrant_api_key == "qdrant-test-key"
    assert settings.qdrant_collection == "kb-test"


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("1", True), ("false", False), ("0", False)])
def test_vision_setting_accepts_supported_boolean_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("LLM_SUPPORTS_VISION", value)

    assert Settings.from_env().llm_supports_vision is expected


def test_vision_setting_rejects_ambiguous_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_SUPPORTS_VISION", "yes")

    with pytest.raises(ValueError, match="LLM_SUPPORTS_VISION must be true, false, 1, or 0"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value"),
    [("MAX_UPLOAD_BYTES", "large"), ("MAX_ATTACHMENTS_PER_MESSAGE", "many"), ("STREAM_MAX_SECONDS", "later")],
)
def test_new_integer_settings_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=f"{name} must be an integer"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("LLM_TIMEOUT_SECONDS", "slow", "LLM_TIMEOUT_SECONDS must be a number"),
        ("LLM_MAX_RETRIES", "many", "LLM_MAX_RETRIES must be an integer"),
        ("MEMORY_WINDOW_TURNS", "lots", "MEMORY_WINDOW_TURNS must be an integer"),
    ],
)
def test_from_env_rejects_invalid_numbers(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        Settings.from_env()


def test_get_settings_caches_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    first = get_settings()
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    assert get_settings() is first
    assert get_settings().llm_provider == "groq"

