"""Create a short, factual handoff summary from recent messages and findings."""
from __future__ import annotations

import logging

from backend.llm import LLMError, LLMProvider, Message
from backend.llm.types import ImagePart
from backend.schemas import ToolFinding
from backend.telemetry import traced

logger = logging.getLogger(__name__)
MAX_SUMMARY_CHARS = 300

SUMMARIZER_SYSTEM_PROMPT = """You summarise IT support tickets for the next engineer.
In at most 3 sentences state: the symptoms, what has already been tried, and the current blocker.
Use only facts present in the conversation. Do not invent details."""


class SummarizerService:
    """Use the injected LLM to condense conversation and diagnostic evidence."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    @traced("service.summarize")
    async def summarize(
        self,
        history: list[Message],
        findings: list[ToolFinding],
        images: tuple[ImagePart, ...] = (),
    ) -> str:
        """Summarize recent context, falling back to the latest user message safely."""
        conversation = "\n".join(f"{message.role}: {message.content}" for message in history)
        finding_text = "\n".join(
            f"- {finding.tool} ({finding.status}): {finding.details}" for finding in findings
        )
        prompt = f"Conversation:\n{conversation or 'None.'}\n\nTool findings:\n{finding_text or 'None.'}"
        try:
            response = await self._llm.chat(
                [Message("system", SUMMARIZER_SYSTEM_PROMPT), Message("user", prompt, images)],
                temperature=0.2,
            )
        except LLMError:
            logger.warning("summarization failed; using the latest user message")
            return self._last_user_message(history)

        summary = response.text.strip()
        return summary or self._last_user_message(history)

    @staticmethod
    def _last_user_message(history: list[Message]) -> str:
        for message in reversed(history):
            if message.role == "user":
                return message.content.strip()[:MAX_SUMMARY_CHARS]
        return ""
