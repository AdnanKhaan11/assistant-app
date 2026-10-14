"""Validate and persist supported chat provider/model choices without managing secrets."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from backend.llm import LLMProvider, create_provider
from backend.llm.registry import PROVIDERS
from backend.memory.runtime_config_store import RuntimeAIConfigRecord, RuntimeAIConfigStore
from backend.utils.config import Settings


@dataclass(frozen=True)
class ProviderModelOption:
    """Describe one provider and the model IDs the backend currently lists for it."""

    provider: str
    models: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeAIStatus:
    """Show process-active settings separately from the saved non-secret configuration."""

    active_provider: str
    active_model: str
    saved: RuntimeAIConfigRecord | None
    providers: tuple[ProviderModelOption, ...]


@dataclass(frozen=True)
class RuntimeAIValidation:
    """Return a safe validation result without exposing provider exceptions or credentials."""

    is_valid: bool
    provider: str
    model: str
    message: str


class RuntimeConfigurationRejected(Exception):
    """Signal that a runtime provider/model pair failed validation."""


class RuntimeConfigurationService:
    """Keep provider selection policy separate from SQLite and HTTP transport details."""

    def __init__(
        self,
        settings: Settings,
        store: RuntimeAIConfigStore,
        provider_factory: Callable[[str, str | None, str | None], LLMProvider] = create_provider,
    ) -> None:
        """Use startup `settings`, persistent `store`, and an injectable provider factory."""
        self._settings = settings
        self._store = store
        self._provider_factory = provider_factory

    async def status(self) -> RuntimeAIStatus:
        """Return active startup settings, saved settings, and registered default models."""
        saved = await asyncio.to_thread(self._store.get)
        options = []
        for provider, spec in sorted(PROVIDERS.items()):
            models = {spec.default_model}
            if provider == self._settings.llm_provider and self._settings.llm_model:
                models.add(self._settings.llm_model)
            options.append(ProviderModelOption(provider, tuple(sorted(models))))
        return RuntimeAIStatus(
            active_provider=self._settings.llm_provider,
            active_model=self._settings.llm_model or PROVIDERS[self._settings.llm_provider].default_model,
            saved=saved,
            providers=tuple(options),
        )

    async def validate(self, provider: str, model: str) -> RuntimeAIValidation:
        """Check registry support, model availability, and configured credentials without network I/O."""
        normalized_provider = provider.strip().lower()
        normalized_model = model.strip()
        spec = PROVIDERS.get(normalized_provider)
        if spec is None:
            return self._invalid(normalized_provider, normalized_model, "The selected provider is not supported.")
        is_supported_model = self._is_supported_model(
            normalized_provider,
            normalized_model,
            spec.default_model,
        )
        if not normalized_model or not is_supported_model:
            return self._invalid(
                normalized_provider,
                normalized_model,
                "The selected model is not in the supported model list.",
            )
        try:
            candidate = self._provider_factory(
                normalized_provider,
                normalized_model,
                self._settings.llm_api_key,
            )
        except ValueError:
            return self._invalid(
                normalized_provider,
                normalized_model,
                "Provider credentials or configuration are not available.",
            )
        await candidate.aclose()
        return RuntimeAIValidation(True, normalized_provider, normalized_model, "Provider and model are configured.")

    async def save(
        self,
        provider: str,
        model: str,
        actor_user_id: str,
    ) -> RuntimeAIConfigRecord:
        """Validate before storing a non-secret provider/model pair for administrator `actor_user_id`."""
        result = await self.validate(provider, model)
        if not result.is_valid:
            raise RuntimeConfigurationRejected(result.message)
        return await asyncio.to_thread(
            self._store.save,
            result.provider,
            result.model,
            actor_user_id,
        )

    def _is_supported_model(self, provider: str, model: str, default_model: str) -> bool:
        """Allow registered defaults and the already-active environment model for its provider."""
        return model == default_model or (
            provider == self._settings.llm_provider and model == self._settings.llm_model
        )

    @staticmethod
    def _invalid(provider: str, model: str, message: str) -> RuntimeAIValidation:
        """Build a rejected validation response without carrying provider details."""
        return RuntimeAIValidation(False, provider, model, message)
