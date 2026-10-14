"""Classify an IT support request into a small, validated set of issue categories."""
from __future__ import annotations

import logging

from pydantic import ValidationError

from backend.llm import LLMError, LLMProvider, Message
from backend.llm.types import ImagePart
from backend.schemas import Classification
from backend.telemetry import get_metrics, traced
from backend.utils.json_utils import parse_json_object

logger = logging.getLogger(__name__)

CLASSIFIER_SYSTEM_PROMPT = """You are an IT support triage classifier.
Return ONE JSON object and nothing else:
{"category": "network_issue" | "software_issue" | "hardware_issue" | "unknown",
 "confidence": <0.0-1.0>,
 "needs_action": <true if running a diagnostic or fix could help>,
 "keywords": [<up to 5 short phrases>]}
If the evidence is weak, use "unknown" and a low confidence. Do not guess."""


class ClassifierService:
    """Use the injected LLM to classify requests and validate its proposed result."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    @traced("service.classify")
    async def classify(
        self,
        user_message: str,
        history: list[Message] | None = None,
        images: tuple[ImagePart, ...] = (),
    ) -> Classification:
        """Classify one user message, degrading to UNKNOWN when output is unreliable."""
        messages = [
            Message("system", CLASSIFIER_SYSTEM_PROMPT),
            *(history or []),
            Message("user", user_message, images),
        ]
        try:
            response = await self._llm.chat(messages, temperature=0.0, json_mode=True)
            result = Classification(**parse_json_object(response.text))
        except (LLMError, ValueError, ValidationError):
            logger.warning("classification failed; using unknown")
            result = Classification()
        get_metrics().record_classification(result.category.value)  # keep: classification metric
        return result
