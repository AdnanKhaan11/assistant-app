"""Read application settings from the environment; telemetry has its own configuration."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "groq"
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    memory_window_turns: int = 10
    db_path: str = "data/assistant.db"
    tickets_path: str = "data/sample_tickets.json"
    upload_dir: str = "data/uploads"
    max_upload_bytes: int = 5_242_880
    max_attachments_per_message: int = 3
    llm_supports_vision: bool = False
    stream_max_seconds: int = 120
    knowledge_pdf_path: str = "data/uploads/IT_Troubleshooting_Playbook.pdf"
    hf_token: str | None = None
    hf_embedding_model: str = "thenlper/gte-large"
    hf_embedding_url: str = "https://router.huggingface.co/hf-inference/models"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection: str = "troubleshooting_knowledge"

    @classmethod
    def from_env(cls) -> "Settings":
        """Load application settings once at the configuration boundary."""
        load_dotenv()

        def optional(name: str) -> str | None:
            value = os.getenv(name)
            return value if value else None

        def integer(name: str, default: int) -> int:
            """Get an integer from the environment, or return a default.
            the work of this function is to handle the case where the environment variable is not set or is not a valid integer.
            why we need this function? because environment variables are always strings, and we need to convert them to the appropriate type.
            """
            value = os.getenv(name)
            if value is None or value == "":
                return default
            try:
                return int(value)
            except ValueError as error:
                raise ValueError(f"{name} must be an integer") from error

        def number(name: str, default: float) -> float:
            """this function retrieves a float value from the environment variables.
            why do we need this function? because environment variables are always strings, and we need to convert them to the appropriate type.
            """
            value = os.getenv(name)
            if value is None or value == "":
                return default
            try:
                return float(value)
            except ValueError as error:
                raise ValueError(f"{name} must be a number") from error

        def boolean(name: str, default: bool) -> bool:
            value = os.getenv(name)
            if value is None or value == "":
                return default
            normalized = value.lower()
            if normalized in {"true", "1"}:
                return True
            if normalized in {"false", "0"}:
                return False
            raise ValueError(f"{name} must be true, false, 1, or 0")

        return cls(
            llm_provider=os.getenv("LLM_PROVIDER") or cls.llm_provider,
            llm_model=optional("LLM_MODEL"),
            llm_api_key=optional("LLM_API_KEY"),
            llm_timeout_seconds=number("LLM_TIMEOUT_SECONDS", cls.llm_timeout_seconds),
            llm_max_retries=integer("LLM_MAX_RETRIES", cls.llm_max_retries),
            memory_window_turns=integer("MEMORY_WINDOW_TURNS", cls.memory_window_turns),
            db_path=os.getenv("DB_PATH") or cls.db_path,
            tickets_path=os.getenv("TICKETS_PATH") or cls.tickets_path,
            upload_dir=os.getenv("UPLOAD_DIR") or cls.upload_dir,
            max_upload_bytes=integer("MAX_UPLOAD_BYTES", cls.max_upload_bytes),
            max_attachments_per_message=integer(
                "MAX_ATTACHMENTS_PER_MESSAGE", cls.max_attachments_per_message
            ),
            llm_supports_vision=boolean("LLM_SUPPORTS_VISION", cls.llm_supports_vision),
            stream_max_seconds=integer("STREAM_MAX_SECONDS", cls.stream_max_seconds),
            knowledge_pdf_path=os.getenv("KNOWLEDGE_PDF_PATH") or cls.knowledge_pdf_path,
            hf_token=optional("HF_TOKEN"),
            hf_embedding_model=os.getenv("HF_EMBEDDING_MODEL") or cls.hf_embedding_model,
            hf_embedding_url=os.getenv("HF_EMBEDDING_URL") or cls.hf_embedding_url,
            qdrant_url=optional("QDRANT_URL"),
            qdrant_api_key=optional("QDRANT_API_KEY"),
            qdrant_collection=os.getenv("QDRANT_COLLECTION") or cls.qdrant_collection,
        )


# @lru_cache is used to cache the result of the get_settings function, so that it only reads the environment variables once and returns the same Settings object for subsequent calls. This is useful for performance and consistency, as the settings are unlikely to change during the lifetime of the application.
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings snapshot."""
    return Settings.from_env()
