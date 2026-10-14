"""Turn validated findings into concise recommendations grounded in available evidence."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import AsyncIterator

from backend.llm import LLMError, LLMProvider, Message
from backend.llm.types import ImagePart
from backend.schemas import Category, ToolFinding
from backend.telemetry import traced
from backend.utils.json_utils import parse_json_object

logger = logging.getLogger(__name__)
_DIAGNOSTIC_CLAIM = re.compile(
    r"\b(?:diagnostics?|tests?|checks?)\b.{0,60}\b(?:was|were|been)?\s*"
    r"(?:run|running|performed|completed|conducted|shows?|showed|finds?|found|reports?|reported|"
    r"indicates?|indicated|confirms?|confirmed|detects?|detected)\b|"
    r"\b(?:ran|run|running|performed|completed|conducted|shows?|showed|finds?|found|reports?|reported|"
    r"indicates?|indicated|confirms?|confirmed|detects?|detected)\b.{0,60}"
    r"\b(?:diagnostics?|tests?|checks?|network_check|issue_lookup)\b|"
    r"\b(?:network_check|issue_lookup)\b.{0,60}\b(?:ran|run|running|performed|completed|conducted|"
    r"shows?|showed|finds?|found|reports?|reported|indicates?|indicated|confirms?|confirmed|detects?|detected)\b",
    re.IGNORECASE,
)

ADVISOR_SYSTEM_PROMPT = """You are an IT support advisor. Rules:
- If evidence is insufficient, say "I don't know based on current data" and ask a clarifying question.
- Never fabricate tool results. Only cite findings you were given.
- For actions that change system state, say they REQUIRE the user's confirmation first.
- When possible, name the tool that supports each recommendation.
- With no tool findings, do not claim that diagnostics or checks were run.
- Return one valid JSON object only, with a "recommendations" array containing 2 to 5
  concise recommendations and an "answer" string containing friendly advice."""

STREAM_RECOMMENDATIONS_MARKER = "\nRECOMMENDATIONS:\n"


@dataclass(frozen=True)
class AnswerDelta:
    """Carry one user-visible answer fragment from advisor streaming."""

    text: str


@dataclass(frozen=True)
class AdviceComplete:
    """Carry the parsed recommendations and complete answer after streaming."""

    recommendations: list[str]
    answer: str


AdviceStreamEvent = AnswerDelta | AdviceComplete


class AdvisorService:
    """Generate validated recommendations from injected model and tool evidence."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    @traced("service.advise")
    async def advise(
        self,
        category: Category,
        summary: str,
        findings: list[ToolFinding],
        images: tuple[ImagePart, ...] = (),
    ) -> tuple[list[str], str]:
        """Return grounded advice, or a safe clarification when evidence is inadequate."""
        try:
            response = await self._llm.chat(
                self._build_messages(category, summary, findings, images),
                temperature=0.2,
                json_mode=True,
            )
            recommendations, answer = self._validate_output(parse_json_object(response.text))
        except Exception:
            logger.exception("advisor failed; using safe fallback")
            return self._fallback(findings)

        if not findings and self._claims_diagnostic(recommendations, answer):
            logger.warning("advisor claimed diagnostics without findings; using safe fallback")
            return self._fallback([])
        if findings and (self._has_weak_evidence(findings) or not self._references_findings(answer, findings)):
            logger.warning("advisor response was not grounded in tool findings; using safe fallback")
            return self._fallback(findings)
        return recommendations, answer

    @staticmethod
    def _build_messages(
        category: Category,
        summary: str,
        findings: list[ToolFinding],
        images: tuple[ImagePart, ...] = (),
    ) -> list[Message]:
        finding_text = "\n".join(
            f"- {finding.tool} ({finding.status}): {finding.details}" for finding in findings
        ) or "None."
        prompt = f"Category: {category.value}\nSummary: {summary}\nTool findings:\n{finding_text}"
        return [Message("system", ADVISOR_SYSTEM_PROMPT), Message("user", prompt, images)]

    @traced("service.advise")
    async def advise_stream(
        self,
        category: Category,
        summary: str,
        findings: list[ToolFinding],
        images: tuple[ImagePart, ...] = (),
    ) -> AsyncIterator[AdviceStreamEvent]:
        """Stream only answer text while buffering and parsing the recommendation section."""
        messages = self._build_stream_messages(category, summary, findings, images)
        answer_parts: list[str] = []
        pending = ""
        recommendation_text = ""
        marker_found = False
        try:
            async for chunk in self._llm.chat_stream(messages, temperature=0.2, json_mode=False):
                if marker_found:
                    recommendation_text += chunk
                    continue
                pending += chunk
                marker_index = pending.find(STREAM_RECOMMENDATIONS_MARKER)
                if marker_index >= 0:
                    answer_chunk = pending[:marker_index]
                    recommendation_text = pending[marker_index + len(STREAM_RECOMMENDATIONS_MARKER) :]
                    pending = ""
                    marker_found = True
                    if answer_chunk:
                        answer_parts.append(answer_chunk)
                        yield AnswerDelta(answer_chunk)
                    continue
                safe_length = max(0, len(pending) - len(STREAM_RECOMMENDATIONS_MARKER) + 1)
                if safe_length:
                    answer_chunk, pending = pending[:safe_length], pending[safe_length:]
                    answer_parts.append(answer_chunk)
                    yield AnswerDelta(answer_chunk)
        except LLMError:
            if answer_parts:
                raise
            recommendations, answer = self._fallback(findings)
            yield AnswerDelta(answer)
            yield AdviceComplete(recommendations, answer)
            return

        if not marker_found and pending:
            answer_parts.append(pending)
            yield AnswerDelta(pending)
        answer = "".join(answer_parts)
        recommendations = self._parse_stream_recommendations(recommendation_text) if marker_found else []
        if not answer:
            recommendations, answer = self._fallback(findings)
            yield AnswerDelta(answer)
        yield AdviceComplete(recommendations, answer)

    @staticmethod
    def _build_stream_messages(
        category: Category,
        summary: str,
        findings: list[ToolFinding],
        images: tuple[ImagePart, ...],
    ) -> list[Message]:
        """Request answer-first plain text so only prose needs to be streamed."""
        system = (
            f"{ADVISOR_SYSTEM_PROMPT}\nWrite the answer first, then a line exactly "
            "RECOMMENDATIONS: followed by 2 to 5 short '- ' bullet lines."
        )
        return [Message("system", system), *AdvisorService._build_messages(category, summary, findings, images)[1:]]

    @staticmethod
    def _parse_stream_recommendations(text: str) -> list[str]:
        """Extract at most five nonempty bullet recommendations from buffered text."""
        recommendations = []
        for line in text.splitlines():
            item = re.sub(r"^\s*[-*]\s+", "", line).strip()
            if item:
                recommendations.append(item)
            if len(recommendations) == 5:
                break
        return recommendations

    @staticmethod
    def _validate_output(data: dict) -> tuple[list[str], str]:
        recommendations = data.get("recommendations")
        answer = data.get("answer")
        if not isinstance(recommendations, list) or not 2 <= len(recommendations) <= 5:
            raise ValueError("Advisor must return between two and five recommendations")
        if any(not isinstance(item, str) or not item.strip() for item in recommendations):
            raise ValueError("Advisor recommendations must be nonempty text")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Advisor answer must be nonempty text")
        return [item.strip() for item in recommendations], answer.strip()

    @staticmethod
    def _claims_diagnostic(recommendations: list[str], answer: str) -> bool:
        return bool(_DIAGNOSTIC_CLAIM.search(" ".join([*recommendations, answer])))

    @staticmethod
    def _has_weak_evidence(findings: list[ToolFinding]) -> bool:
        return not any(finding.status == "ok" and finding.details.strip() for finding in findings)

    @staticmethod
    def _references_findings(answer: str, findings: list[ToolFinding]) -> bool:
        return all(finding.tool.casefold() in answer.casefold() for finding in findings)

    @staticmethod
    def _fallback(findings: list[ToolFinding]) -> tuple[list[str], str]:
        if not findings:
            return (
                ["Confirm when the issue began.", "Ask what troubleshooting steps have been tried."],
                "I need more details to recommend a next step. When did the issue begin?",
            )

        recommendations = [
            "Review the reported tool findings with the user.",
            "Confirm whether the symptoms are still occurring.",
        ]
        if AdvisorService._has_weak_evidence(findings):
            tools = ", ".join(finding.tool for finding in findings)
            answer = (
                f"I don't know based on current data. {tools} did not provide usable results. "
                "What do you see now?"
            )
        else:
            evidence = "; ".join(f"{finding.tool}: {finding.details}" for finding in findings)
            answer = f"Current findings: {evidence}. What would you like help with next?"
        return recommendations, answer
