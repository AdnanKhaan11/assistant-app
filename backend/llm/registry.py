"""Provider table + factory. Adding a new OpenAI-compatible provider = ONE new line in PROVIDERS."""
from __future__ import annotations

import os
from dataclasses import dataclass

from backend.telemetry import TelemetryConfig

from .anthropic_provider import AnthropicProvider
from .base import LLMProvider
from .instrumented import InstrumentedProvider
from .openai_compat import OpenAICompatProvider
from .retry import RetryingProvider


@dataclass(frozen=True)
class ProviderSpec:
    kind: str            # "openai_compat" | "anthropic"
    base_url: str
    default_model: str
    key_env: str | None  # None = no key needed (local Ollama)


PROVIDERS: dict[str, ProviderSpec] = {
    "openai":     ProviderSpec("openai_compat", "https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    "groq":       ProviderSpec("openai_compat", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "gemini":     ProviderSpec("openai_compat", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash", "GEMINI_API_KEY"),
    "anthropic":  ProviderSpec("anthropic", "https://api.anthropic.com", "claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY"),
    "ollama":     ProviderSpec("openai_compat", "http://localhost:11434/v1", "llama3.1", None),
    "openrouter": ProviderSpec("openai_compat", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini", "OPENROUTER_API_KEY"),
    "deepseek":   ProviderSpec("openai_compat", "https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
}


def create_provider(
    provider: str,
    model: str | None = None,
    api_key: str | None = None,
    *,
    timeout: float = 30.0,
    max_retries: int = 2,
    telemetry: TelemetryConfig | None = None,
) -> LLMProvider:
    """Build the full stack:  Retrying( Instrumented( adapter ) )."""
    spec = PROVIDERS.get(provider.lower())
    if spec is None:
        raise ValueError(f"Unknown LLM provider '{provider}'. Choose one of: {', '.join(PROVIDERS)}")

    key = api_key or (os.getenv(spec.key_env) if spec.key_env else None)
    if spec.key_env and not key:
        raise ValueError(f"Missing API key for '{provider}': set LLM_API_KEY or {spec.key_env}")

    adapter_cls = AnthropicProvider if spec.kind == "anthropic" else OpenAICompatProvider
    adapter = adapter_cls(
        provider_name=provider.lower(), base_url=spec.base_url, api_key=key,
        model=model or spec.default_model, timeout=timeout,
    )
    capture = telemetry.capture_content if telemetry else False
    return RetryingProvider(InstrumentedProvider(adapter, capture_content=capture), max_retries=max_retries)
