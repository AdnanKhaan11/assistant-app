from __future__ import annotations

import json

import pytest

from backend.llm import LLMError, Message
from backend.llm.types import ImagePart
from backend.schemas import Category, Classification, ToolFinding
from backend.services.advisor import AdviceComplete, AdvisorService, AnswerDelta, STREAM_RECOMMENDATIONS_MARKER
from backend.services.classifier import CLASSIFIER_SYSTEM_PROMPT, ClassifierService
from backend.services.summarizer import MAX_SUMMARY_CHARS, SUMMARIZER_SYSTEM_PROMPT, SummarizerService
from backend.tests.fakes import FakeProvider


async def test_classifier_builds_messages_and_parses_fenced_json() -> None:
    provider = FakeProvider(
        ['```json\n{"category":"network_issue","confidence":0.9,"needs_action":true,"keywords":["wifi"]}\n```']
    )
    history = [Message("user", "Earlier Wi-Fi report"), Message("assistant", "When did it begin?")]

    result = await ClassifierService(provider).classify("Connection drops", history)

    call = provider.calls[0]
    assert result == Classification(
        category=Category.NETWORK,
        confidence=0.9,
        needs_action=True,
        keywords=["wifi"],
    )
    assert call.messages == (
        Message("system", CLASSIFIER_SYSTEM_PROMPT),
        *history,
        Message("user", "Connection drops"),
    )
    assert call.temperature == 0.0
    assert call.json_mode is True


async def test_classifier_attaches_images_only_to_current_user_message() -> None:
    image = ImagePart("image/png", "aGVsbG8=")
    provider = FakeProvider(['{"category":"hardware_issue","confidence":0.8}'])

    await ClassifierService(provider).classify("Screen is cracked", images=(image,))

    assert provider.calls[0].messages[-1] == Message("user", "Screen is cracked", (image,))


@pytest.mark.parametrize(
    "response",
    ["not json", '{"category":"not-a-category"}', '{"confidence":2.0}'],
)
async def test_classifier_falls_back_on_bad_model_output(response: str) -> None:
    result = await ClassifierService(FakeProvider([response])).classify("Issue")

    assert result == Classification()


async def test_classifier_falls_back_on_provider_error() -> None:
    result = await ClassifierService(FakeProvider([LLMError("provider unavailable")])).classify("Issue")

    assert result == Classification()


async def test_summarizer_includes_history_and_tool_findings() -> None:
    provider = FakeProvider(["  Wi-Fi drops; adapter restart failed; connection remains unstable.  "])
    history = [Message("user", "Wi-Fi drops"), Message("assistant", "Try restarting the adapter.")]
    findings = [ToolFinding(tool="network_check", status="ok", details="packet loss 7%, DNS ok")]

    summary = await SummarizerService(provider).summarize(history, findings)

    call = provider.calls[0]
    prompt = call.messages[1].content
    assert summary == "Wi-Fi drops; adapter restart failed; connection remains unstable."
    assert call.messages[0] == Message("system", SUMMARIZER_SYSTEM_PROMPT)
    assert "user: Wi-Fi drops" in prompt
    assert "assistant: Try restarting the adapter." in prompt
    assert "network_check (ok): packet loss 7%, DNS ok" in prompt
    assert call.temperature == 0.2
    assert call.json_mode is False


async def test_summarizer_attaches_images_to_current_prompt() -> None:
    image = ImagePart("image/jpeg", "aGVsbG8=")
    provider = FakeProvider(["Screen has a visible crack."])

    await SummarizerService(provider).summarize([Message("user", "Screen damage")], [], images=(image,))

    assert provider.calls[0].messages[-1].images == (image,)


async def test_summarizer_falls_back_to_latest_user_message_on_llm_error() -> None:
    latest_user_text = "x" * (MAX_SUMMARY_CHARS + 50)
    history = [Message("user", "earlier issue"), Message("assistant", "question"), Message("user", latest_user_text)]
    service = SummarizerService(FakeProvider([LLMError("provider unavailable")]))

    summary = await service.summarize(history, [])

    assert summary == "x" * MAX_SUMMARY_CHARS


async def test_summarizer_uses_fallback_for_empty_model_output() -> None:
    history = [Message("user", "The printer is offline.")]

    summary = await SummarizerService(FakeProvider(["  "])).summarize(history, [])

    assert summary == "The printer is offline."


def _advice_response(recommendations: list[str], answer: str) -> str:
    return json.dumps({"recommendations": recommendations, "answer": answer})


async def test_advisor_returns_json_advice_grounded_in_findings() -> None:
    recommendations = ["Review packet loss with the user.", "Confirm whether drops continue."]
    provider = FakeProvider([_advice_response(recommendations, "network_check reported 7% packet loss.")])
    finding = ToolFinding(tool="network_check", status="ok", details="packet loss 7%")

    result = await AdvisorService(provider).advise(Category.NETWORK, "Wi-Fi drops", [finding])

    assert result == (recommendations, "network_check reported 7% packet loss.")
    assert provider.calls[0].temperature == 0.2
    assert provider.calls[0].json_mode is True
    assert "JSON" in provider.calls[0].messages[0].content.upper()
    assert "Category: network_issue" in provider.calls[0].messages[1].content
    assert "network_check (ok): packet loss 7%" in provider.calls[0].messages[1].content


async def test_advisor_attaches_images_to_current_prompt() -> None:
    image = ImagePart("image/png", "aGVsbG8=")
    provider = FakeProvider(
        [_advice_response(["Review the image with the user.", "Confirm when damage began."], "Please share details.")]
    )

    await AdvisorService(provider).advise(Category.HARDWARE, "Screen issue", [], images=(image,))

    assert provider.calls[0].messages[-1].images == (image,)


async def test_advisor_stream_holds_marker_prefix_and_returns_parsed_recommendations() -> None:
    provider = FakeProvider(
        [],
        stream_chunks=[[
            "The connection remains unstable.",
            "\nRECOMMENDATIONS",
            ":\n- Review the network_check result.\n- Confirm whether drops continue.",
        ]],
    )
    events = [
        event
        async for event in AdvisorService(provider).advise_stream(
            Category.NETWORK,
            "Wi-Fi drops",
            [ToolFinding(tool="network_check", status="ok", details="packet loss 7%")],
        )
    ]

    assert isinstance(events[-1], AdviceComplete)
    assert events[-1].answer == "The connection remains unstable."
    assert events[-1].recommendations == [
        "Review the network_check result.",
        "Confirm whether drops continue.",
    ]
    assert "".join(event.text for event in events if isinstance(event, AnswerDelta)) == events[-1].answer
    assert STREAM_RECOMMENDATIONS_MARKER not in "".join(
        event.text for event in events if isinstance(event, AnswerDelta)
    )
    assert provider.calls[0].json_mode is False
    assert "Never fabricate tool results" in provider.calls[0].messages[0].content


async def test_advisor_stream_without_marker_returns_all_text_and_no_recommendations() -> None:
    provider = FakeProvider([], stream_chunks=[["A", " useful answer."]])

    events = [event async for event in AdvisorService(provider).advise_stream(Category.UNKNOWN, "Issue", [])]

    assert events[-1] == AdviceComplete([], "A useful answer.")
    assert "".join(event.text for event in events if isinstance(event, AnswerDelta)) == events[-1].answer


async def test_advisor_stream_empty_output_uses_safe_fallback() -> None:
    events = [event async for event in AdvisorService(FakeProvider([], stream_chunks=[[]])).advise_stream(
        Category.UNKNOWN, "Issue", []
    )]

    assert events[-1] == AdviceComplete(
        ["Confirm when the issue began.", "Ask what troubleshooting steps have been tried."],
        "I need more details to recommend a next step. When did the issue begin?",
    )


async def test_advisor_stream_falls_back_before_any_delta_and_propagates_late_error() -> None:
    early = FakeProvider([], stream_chunks=[[LLMError("unavailable")]])
    early_events = [event async for event in AdvisorService(early).advise_stream(Category.UNKNOWN, "Issue", [])]
    assert isinstance(early_events[-1], AdviceComplete)
    assert "When did the issue begin?" in early_events[-1].answer

    late = FakeProvider([], stream_chunks=[["a sufficiently long partial answer", LLMError("unavailable")]])
    with pytest.raises(LLMError):
        _ = [event async for event in AdvisorService(late).advise_stream(Category.UNKNOWN, "Issue", [])]


async def test_advisor_gives_safe_advice_without_claiming_a_diagnostic() -> None:
    recommendations = ["Confirm when the issue began.", "Ask what steps have been tried."]
    provider = FakeProvider([_advice_response(recommendations, "When did the issue begin?")])

    result = await AdvisorService(provider).advise(Category.UNKNOWN, "Issue", [])

    assert result == (recommendations, "When did the issue begin?")


@pytest.mark.parametrize(
    "response",
    [
        _advice_response(
            ["The network_check found packet loss.", "Restart the router."],
            "Our diagnostic found packet loss.",
        ),
        _advice_response(
            ["The network_check reported packet loss.", "Restart the router."],
            "network_check reported packet loss.",
        ),
    ],
)
async def test_advisor_replaces_diagnostic_claim_when_no_findings_exist(response: str) -> None:

    recommendations, answer = await AdvisorService(FakeProvider([response])).advise(
        Category.NETWORK,
        "Wi-Fi drops",
        [],
    )

    assert len(recommendations) == 2
    assert "diagnostic" not in " ".join(recommendations).casefold()
    assert "diagnostic" not in answer.casefold()
    assert "When did the issue begin?" in answer


async def test_advisor_uses_uncertainty_fallback_for_weak_findings() -> None:
    response = _advice_response(
        ["Review the check result.", "Confirm whether the issue continues."],
        "network_check indicates a problem.",
    )
    finding = ToolFinding(tool="network_check", status="error", details="diagnostic timed out")

    recommendations, answer = await AdvisorService(FakeProvider([response])).advise(
        Category.NETWORK,
        "Wi-Fi drops",
        [finding],
    )

    assert len(recommendations) == 2
    assert "I don't know based on current data" in answer
    assert "network_check" in answer
    assert answer.endswith("What do you see now?")


async def test_advisor_falls_back_when_answer_does_not_reference_findings() -> None:
    response = _advice_response(
        ["Check the reported packet loss.", "Confirm whether drops continue."],
        "The connection is unstable.",
    )
    finding = ToolFinding(tool="network_check", status="ok", details="packet loss 7%")

    recommendations, answer = await AdvisorService(FakeProvider([response])).advise(
        Category.NETWORK,
        "Wi-Fi drops",
        [finding],
    )

    assert len(recommendations) == 2
    assert "network_check: packet loss 7%" in answer


@pytest.mark.parametrize(
    "response",
    ["not json", _advice_response(["Only one recommendation."], "Need more details?")],
)
async def test_advisor_uses_safe_fallback_for_invalid_output(response: str) -> None:
    recommendations, answer = await AdvisorService(FakeProvider([response])).advise(
        Category.UNKNOWN,
        "Issue",
        [],
    )

    assert len(recommendations) == 2
    assert "When did the issue begin?" in answer


async def test_advisor_uses_safe_fallback_for_provider_error() -> None:
    recommendations, answer = await AdvisorService(FakeProvider([LLMError("unavailable")])).advise(
        Category.UNKNOWN,
        "Issue",
        [],
    )

    assert len(recommendations) == 2
    assert "When did the issue begin?" in answer

