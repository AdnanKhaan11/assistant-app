"""The ONE internal message format the whole app speaks. Adapters translate to/from provider formats."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ImagePart:
    """Carry one validated image to providers that support vision."""

    media_type: str
    data_base64: str


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    images: tuple[ImagePart, ...] = ()

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None


class LLMError(Exception):
    """Every provider failure is converted to this, so the rest of the app handles ONE error type."""

    def __init__(
        self, message: str, *, retryable: bool = False, status_code: int | None = None, error_type: str = "llm_error"
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.error_type = error_type  # low-cardinality label used in metrics, e.g. "rate_limited"
